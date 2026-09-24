"""把 Router 保留的原始表达规范化，并补充透明、可追踪的默认值。"""

from __future__ import annotations

import re
from datetime import date as Date
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from app.domain.constraints import (
    ActorContext,
    Assumption,
    ConstraintSource,
    ConstraintValue,
    DateReference,
    EnrichmentResult,
    GeoLocation,
    Interpretation,
    NormalizedConstraints,
    PartyProfile,
    StopRole,
    TimeScope,
    TimeWindow,
    Weekday,
)
from app.domain.providers import GeocodeRequest, GeocodeResolution, GeocodingFact
from app.providers.geocoding import GeocodingProvider


class EnvironmentContext(BaseModel):
    """Enrichment 需要的环境事实，由代码或工具适配器提供而非 LLM 猜测。"""
    model_config = ConfigDict(extra="forbid")

    now: datetime
    default_location: GeoLocation


class TemporalCompiler:
    """Small, shared temporal vocabulary used by DemoRouter and Enrichment.

    It deliberately compiles only a finite contract.  It never guesses a
    date for an unknown phrase and never replaces the model's raw evidence.
    """

    _RELATIVE_DATE_ALIASES: tuple[tuple[str, DateReference], ...] = (
        ("今天晚上", DateReference.TODAY),
        ("今天夜里", DateReference.TODAY),
        ("今晚", DateReference.TODAY),
        ("今夜", DateReference.TODAY),
        ("明天晚上", DateReference.TOMORROW),
        ("明晚", DateReference.TOMORROW),
        ("后天晚上", DateReference.DAY_AFTER_TOMORROW),
        ("后晚", DateReference.DAY_AFTER_TOMORROW),
        ("今天", DateReference.TODAY),
        ("明天", DateReference.TOMORROW),
        ("后天", DateReference.DAY_AFTER_TOMORROW),
    )
    _WEEKDAY_ALIASES: dict[str, Weekday] = {
        "一": Weekday.MONDAY,
        "二": Weekday.TUESDAY,
        "三": Weekday.WEDNESDAY,
        "四": Weekday.THURSDAY,
        "五": Weekday.FRIDAY,
        "六": Weekday.SATURDAY,
        "日": Weekday.SUNDAY,
        "天": Weekday.SUNDAY,
    }
    _TIME_SCOPE_ALIASES: tuple[tuple[str, TimeScope], ...] = (
        ("一整天", TimeScope.ALL_DAY),
        ("全天", TimeScope.ALL_DAY),
        ("从早到晚", TimeScope.ALL_DAY),
        ("玩一天", TimeScope.ALL_DAY),
        ("今天晚上", TimeScope.EVENING),
        ("明天晚上", TimeScope.EVENING),
        ("后天晚上", TimeScope.EVENING),
        ("今晚", TimeScope.EVENING),
        ("明晚", TimeScope.EVENING),
        ("后晚", TimeScope.EVENING),
        ("今夜", TimeScope.EVENING),
        ("上午", TimeScope.MORNING),
        ("早上", TimeScope.MORNING),
        ("下午", TimeScope.AFTERNOON),
        ("晚上", TimeScope.EVENING),
    )

    @classmethod
    def extract_date(
        cls,
        text: str | None,
    ) -> tuple[str | None, DateReference | None, Weekday | None, int | None, Date | None]:
        if not text:
            return None, None, None, None, None
        value = text.strip()
        absolute_match = re.search(
            r"(?P<year>20\d{2})[年/-](?P<month>\d{1,2})[月/-](?P<day>\d{1,2})日?",
            value,
        )
        if absolute_match:
            try:
                absolute_date = Date(
                    int(absolute_match.group("year")),
                    int(absolute_match.group("month")),
                    int(absolute_match.group("day")),
                )
            except ValueError:
                # Keep the raw phrase for Gate; do not construct an invalid
                # structured reference that cannot be resolved.
                return absolute_match.group(0), None, None, None, None
            return (
                absolute_match.group(0),
                DateReference.ABSOLUTE,
                None,
                None,
                absolute_date,
            )

        for phrase, reference in sorted(cls._RELATIVE_DATE_ALIASES, key=lambda item: -len(item[0])):
            if phrase in value:
                return phrase, reference, None, None, None

        weekday_match = re.search(
            r"(?P<prefix>本周|这周|下周|周|星期)(?P<day>[一二三四五六日天])",
            value,
        )
        if weekday_match:
            prefix = weekday_match.group("prefix")
            week_offset = 0 if prefix in {"本周", "这周"} else 1 if prefix == "下周" else None
            return (
                weekday_match.group(0),
                DateReference.WEEKDAY,
                cls._WEEKDAY_ALIASES[weekday_match.group("day")],
                week_offset,
                None,
            )
        return None, None, None, None, None

    @classmethod
    def extract_unresolved_date_text(cls, text: str | None) -> str | None:
        """Keep a bounded date-like phrase that the finite compiler cannot resolve.

        This is deliberately not a general date parser.  It only preserves the
        user's explicit temporal wording so Enrichment/Gate can ask for a
        concrete date instead of treating an unresolved date as omitted.
        """

        if not text:
            return None
        value = text.strip()
        for pattern in (
            r"等[^，,。；;]{0,16}那天",
            r"忙完[^，,。；;]{0,10}(?:那天|以后)",
        ):
            match = re.search(pattern, value)
            if match:
                return match.group(0)
        return None

    @classmethod
    def extract_time(
        cls,
        text: str | None,
    ) -> tuple[str | None, TimeScope | None, TimeWindow | None]:
        if not text:
            return None, None, None
        value = text.strip()
        for phrase, scope in sorted(cls._TIME_SCOPE_ALIASES, key=lambda item: -len(item[0])):
            if phrase in value:
                return phrase, scope, None
        if "中午" in value:
            return "中午", None, TimeWindow(start="11:30", end="14:00")

        range_match = re.search(
            r"(?P<start>\d{1,2})(?::|：)?(?P<start_minute>\d{2})?\s*"
            r"[-‐‑‒–—到至]\s*"
            r"(?P<end>\d{1,2})(?::|：)?(?P<end_minute>\d{2})?",
            value,
        )
        if range_match:
            raw_range = range_match.group(0)
            try:
                window = TimeWindow(
                    start=f"{int(range_match.group('start')):02d}:{int(range_match.group('start_minute') or 0):02d}",
                    end=f"{int(range_match.group('end')):02d}:{int(range_match.group('end_minute') or 0):02d}",
                )
            except ValueError:
                return raw_range, TimeScope.EXPLICIT_RANGE, None
            return raw_range, TimeScope.EXPLICIT_RANGE, window
        return None, None, None

    @classmethod
    def resolve_date(
        cls,
        reference: DateReference | None,
        *,
        current_date: Date,
        weekday: Weekday | None = None,
        week_offset: int | None = None,
        absolute_date: Date | None = None,
    ) -> Date | None:
        if reference == DateReference.TODAY:
            return current_date
        if reference == DateReference.TOMORROW:
            return current_date + timedelta(days=1)
        if reference == DateReference.DAY_AFTER_TOMORROW:
            return current_date + timedelta(days=2)
        if reference == DateReference.ABSOLUTE:
            return absolute_date
        if reference != DateReference.WEEKDAY or weekday is None:
            return None
        target_weekday = list(Weekday).index(weekday)
        if week_offset is None:
            return current_date + timedelta(days=(target_weekday - current_date.weekday()) % 7)
        week_start = current_date - timedelta(days=current_date.weekday())
        return week_start + timedelta(days=7 * week_offset + target_weekday)

    @classmethod
    def normalize_date(cls, raw_text: str | None, current_date: Date) -> Date | None:
        _, reference, weekday, week_offset, absolute_date = cls.extract_date(raw_text)
        return cls.resolve_date(
            reference,
            current_date=current_date,
            weekday=weekday,
            week_offset=week_offset,
            absolute_date=absolute_date,
        )

    @classmethod
    def normalize_time_window(cls, raw_text: str | None) -> TimeWindow | None:
        _, scope, explicit_window = cls.extract_time(raw_text)
        if explicit_window is not None:
            return explicit_window
        if scope is None:
            return None
        return cls.time_window_for_scope(scope)

    @staticmethod
    def time_window_for_scope(scope: TimeScope) -> TimeWindow | None:
        return {
            TimeScope.MORNING: TimeWindow(start="09:00", end="12:00"),
            TimeScope.AFTERNOON: TimeWindow(start="14:00", end="18:00"),
            TimeScope.EVENING: TimeWindow(start="18:00", end="22:00"),
            TimeScope.ALL_DAY: TimeWindow(start="09:00", end="21:00"),
            TimeScope.EXPLICIT_RANGE: None,
        }[scope]

    @classmethod
    def normalize_clock_text(cls, text: str | None) -> str | None:
        """Normalize one field-directed clock expression to ``HH:MM``.

        This is intentionally independent of a surrounding intent.  Callers
        must already know whether the value is a departure or return clock;
        the method only handles the bounded Chinese/Arabic clock vocabulary.
        """
        if not text:
            return None
        value = text.strip()
        match = re.search(
            r"(?P<period>上午|早上|中午|下午|晚上|夜里|夜间)?\s*"
            r"(?P<hour>\d{1,2}|[零〇一二两三四五六七八九十]+)"
            r"(?:[:：](?P<minute>\d{1,2})|点(?P<half>半)|点(?P<cnminute>[零〇一二两三四五六七八九十]+)分?|点)?",
            value,
        )
        if match is None:
            return None
        hour_text = match.group("hour")
        if hour_text.isdigit():
            hour = int(hour_text)
        else:
            hour = cls._parse_chinese_number(hour_text)
        if hour is None:
            return None
        if match.group("half"):
            minute = 30
        elif match.group("cnminute"):
            minute = cls._parse_chinese_number(match.group("cnminute"))
        else:
            minute = int(match.group("minute")) if match.group("minute") else 0
        if minute is None or not 0 <= minute <= 59:
            return None
        period = match.group("period")
        if period in {"下午", "晚上", "夜里", "夜间"} and 1 <= hour <= 11:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        elif period in {"上午", "早上"} and hour == 12:
            hour = 0
        if not 0 <= hour <= 23:
            return None
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _parse_chinese_number(value: str) -> int | None:
        digits = {
            "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
            "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        }
        if value in digits:
            return digits[value]
        if value == "十":
            return 10
        if "十" in value:
            left, right = value.split("十", 1)
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None


class EnrichmentService:
    """将原始约束转换为 Planner 可直接消费的统一约束。

    重要规则：用户没说时可以使用默认值；用户说了但代码无法可靠解析时不能
    偷偷默认，而应留下 ``None`` 交给 NeedQuestionGate 判断是否反问。
    """

    DEFAULT_BUDGET = 120
    DEFAULT_DISTANCE_KM = 8.0
    ALL_DAY_START = "09:00"
    ALL_DAY_END = "21:00"
    # A departure-only request needs a finite bound for the existing Planner,
    # but this is an internal search horizon rather than a user return
    # deadline.  Keep the distinction in the rule id and visible assumption.
    DEFAULT_PLANNING_HORIZON_END = "23:59"

    def __init__(self, geocoding_provider: GeocodingProvider | None = None) -> None:
        self._geocoding_provider = geocoding_provider

    def enrich(
        self,
        interpretation: Interpretation,
        actor: ActorContext,
        environment: EnvironmentContext,
    ) -> EnrichmentResult:
        raw = interpretation.raw_constraints
        assumptions: list[Assumption] = []

        # 用户明确说了但无法解析的文本必须保持未解决。只有真正缺失的字段才可
        # 使用默认值，这样 Gate 会对歧义表达提问，而不是覆盖用户原话。
        date_value = None
        structured_date = TemporalCompiler.resolve_date(
            raw.date_reference,
            current_date=environment.now.date(),
            weekday=raw.weekday,
            week_offset=raw.week_offset,
            absolute_date=raw.absolute_date,
        )
        if raw.date_reference is not None and structured_date is not None:
            date_rule_id = {
                DateReference.TODAY: "date.reference.today.v1",
                DateReference.TOMORROW: "date.reference.tomorrow.v1",
                DateReference.DAY_AFTER_TOMORROW: "date.reference.day_after_tomorrow.v1",
                DateReference.WEEKDAY: "date.reference.weekday.v1",
                DateReference.ABSOLUTE: "date.reference.absolute.v1",
            }[raw.date_reference]
            date_value = ConstraintValue[Date](
                value=structured_date,
                source=ConstraintSource.USER_INFERRED,
                raw_text=(
                    raw.date_text
                    or interpretation.evidence_map.get("date_reference")
                    or interpretation.evidence_map.get("absolute_date")
                ),
                confidence=(
                    self._confidence(interpretation, "date_reference")
                    or self._confidence(interpretation, "date_text")
                    or self._confidence(interpretation, "absolute_date")
                ),
                rule_id=date_rule_id,
            )
        else:
            normalized_date = self._normalize_date(raw.date_text, environment.now.date())
            if normalized_date is not None:
                date_value = ConstraintValue[Date](
                    value=normalized_date,
                    source=ConstraintSource.USER_INFERRED,
                    raw_text=raw.date_text,
                    confidence=self._confidence(interpretation, "date_text"),
                    rule_id="date.relative.zh_cn.v1",
                )
        if date_value is None and raw.date_text is None and raw.date_reference is None:
            default_date = self._next_saturday(environment.now.date())
            date_value = ConstraintValue[Date](
                value=default_date,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="date.default.next_saturday.v1",
            )
            assumptions.append(
                Assumption(
                    field="date",
                    value=default_date.isoformat(),
                    reason="用户未指定日期，先按最近的周六规划",
                    rule_id="date.default.next_saturday.v1",
                )
            )

        departure_at = self._normalize_departure_at(
            raw.departure_at_text,
            raw.departure_at,
        )
        departure_at_value = (
            ConstraintValue[str](
                value=departure_at,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw.departure_at_text,
                confidence=self._confidence(interpretation, "departure_at_text"),
                rule_id="departure_at.clock.zh_cn.v1",
            )
            if departure_at is not None
            else None
        )

        return_by = self._normalize_return_by(raw.return_by_text, raw.return_by)
        return_by_value = None
        if return_by is not None:
            return_by_value = ConstraintValue[str](
                value=return_by,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw.return_by_text,
                confidence=self._confidence(interpretation, "return_by_text"),
                rule_id="return_by.clock.zh_cn.v1",
            )

        exact_stop_count = (
            ConstraintValue[int](
                value=raw.exact_stop_count,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=interpretation.evidence_map.get("exact_stop_count"),
                confidence=self._confidence(interpretation, "exact_stop_count"),
                rule_id="plan_structure.exact_stop_count.v1",
            )
            if raw.exact_stop_count is not None
            else None
        )
        required_stop_roles = (
            ConstraintValue[tuple[StopRole, ...]](
                value=raw.required_stop_roles,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=interpretation.evidence_map.get("required_stop_roles"),
                confidence=self._confidence(interpretation, "required_stop_roles"),
                rule_id="plan_structure.required_roles.v1",
            )
            if raw.required_stop_roles
            else None
        )

        # Prefer the bounded structured temporal contract.  Old checkpoints
        # still fall back to their *_text fields below.
        structured_time_window = raw.explicit_time_window
        if structured_time_window is not None:
            time_scope = TimeScope.EXPLICIT_RANGE
            normalized_time = structured_time_window
        elif raw.time_scope is not None:
            time_scope = raw.time_scope
            normalized_time = (
                self._normalize_time_window(raw.time_text)
                if raw.time_scope == TimeScope.EXPLICIT_RANGE
                else self._time_window_for_scope(raw.time_scope)
            )
        else:
            time_scope = self._infer_time_scope(raw.time_text)
            normalized_time = self._normalize_time_window(raw.time_text)
        multi_role_time_default = self._multi_role_default_time_window(
            raw.required_stop_roles,
            return_by=return_by,
            exact_stop_count=raw.exact_stop_count,
        )
        multi_role_window_applied = False
        departure_overrides_scope = False
        departure_horizon_applied = False
        departure_return_window_applied = False
        if structured_time_window is not None:
            # An explicit numeric range is authoritative.  A departure clock
            # cannot widen or replace it.
            normalized_time = structured_time_window
        elif time_scope == TimeScope.ALL_DAY:
            normalized_time = self._time_window_for_scope(TimeScope.ALL_DAY)
        elif time_scope in {
            TimeScope.MORNING,
            TimeScope.AFTERNOON,
            TimeScope.EVENING,
        }:
            scope_window = self._time_window_for_scope(time_scope)
            # “下午/晚上”是模糊时段；一个明确的出发时刻具有更高优先级。
            # 只有当现有窗口确实来自该模糊时段（或窗口尚未形成）时才覆盖，
            # 显式的“10:00-16:00”仍由后续冲突检查保护。
            scope_is_inferred = normalized_time is None or normalized_time == scope_window
            if departure_at is not None and scope_is_inferred:
                override_end = (
                    return_by
                    if return_by is not None and return_by > departure_at
                    else self.DEFAULT_PLANNING_HORIZON_END
                )
                if override_end is not None and override_end > departure_at:
                    normalized_time = TimeWindow(
                        start=departure_at,
                        end=override_end,
                    )
                    departure_overrides_scope = True
                    departure_return_window_applied = (
                        return_by is not None and return_by > departure_at
                    )
                    departure_horizon_applied = not departure_return_window_applied
            if not departure_overrides_scope and normalized_time is None:
                normalized_time = scope_window

        # A multi-role request that names a meal period needs a window that can
        # actually reach that meal. This only widens a missing or inferred
        # period/default; explicit numeric ranges remain untouched.
        inferred_period = (
            time_scope in {
                TimeScope.MORNING,
                TimeScope.AFTERNOON,
                TimeScope.EVENING,
            }
            and structured_time_window is None
            and normalized_time == self._time_window_for_scope(time_scope)
        )
        if (
            departure_at is None
            and multi_role_time_default is not None
            and time_scope != TimeScope.ALL_DAY
            and time_scope != TimeScope.EXPLICIT_RANGE
            and (raw.time_text is None or inferred_period)
        ):
            normalized_time = multi_role_time_default
            multi_role_window_applied = True

        # A precise departure overrides a fuzzy period's start, but it does
        # not turn that fuzzy period into a user-authored explicit range.  The
        # distinction controls whether Planner may report an outside-window
        # hard conflict.
        effective_time_scope = time_scope
        time_scope_value = (
            ConstraintValue[TimeScope](
                value=effective_time_scope,
                source=ConstraintSource.USER_INFERRED,
                raw_text=(
                    raw.time_text
                    or interpretation.evidence_map.get("explicit_time_window")
                ),
                confidence=(
                    self._confidence(interpretation, "time_scope")
                    or self._confidence(interpretation, "time_text")
                ),
                rule_id="time.scope.zh_cn.v1",
            )
            if effective_time_scope is not None
            else None
        )
        time_value = None
        if normalized_time is not None:
            time_value = ConstraintValue[TimeWindow](
                value=normalized_time,
                source=(
                    ConstraintSource.DEFAULT_RULE
                    if (
                        effective_time_scope == TimeScope.ALL_DAY
                        or multi_role_window_applied
                        or departure_horizon_applied
                    )
                    else ConstraintSource.USER_INFERRED
                ),
                raw_text=(
                    raw.time_text
                    or interpretation.evidence_map.get("explicit_time_window")
                ),
                confidence=(
                    self._confidence(interpretation, "explicit_time_window")
                    or self._confidence(interpretation, "time_text")
                ),
                rule_id=(
                    "time.explicit_range.v1"
                    if structured_time_window is not None
                    or raw.time_scope == TimeScope.EXPLICIT_RANGE
                    else (
                        "time.all_day.default_window.v1"
                        if effective_time_scope == TimeScope.ALL_DAY
                        else (
                            "time.departure_only.planning_horizon.v1"
                            if departure_horizon_applied
                            else (
                                "time.window.from_departure_return.v1"
                                if departure_return_window_applied
                                else (
                                    "time.window.from_departure_overrides_scope.v1"
                                    if departure_overrides_scope
                                    else (
                                        "time.multi_role.meal_anchor.v1"
                                        if multi_role_window_applied
                                        else "time.period.zh_cn.v1"
                                    )
                                )
                            )
                        )
                    )
                ),
            )
            if effective_time_scope == TimeScope.ALL_DAY:
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=normalized_time.model_dump(),
                        reason="“全天”按 09:00–21:00 的可见产品规则规划",
                        rule_id="time.all_day.default_window.v1",
                    )
                )
            elif departure_horizon_applied:
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=normalized_time.model_dump(),
                        reason=(
                            "已指定精确出发时刻；结束时间仅作为当日排程搜索范围，"
                            "不代表用户要求此时返程"
                        ),
                        rule_id="time.departure_only.planning_horizon.v1",
                    )
                )
            elif departure_overrides_scope:
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=normalized_time.model_dump(),
                        reason="已指定精确出发时刻，优先于“上午/下午/晚上”的模糊时段",
                        rule_id="time.window.from_departure_overrides_scope.v1",
                    )
                )
            elif multi_role_window_applied:
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=normalized_time.model_dump(),
                        reason=(
                            "行程包含明确的午饭/晚饭角色，默认时间窗扩展到相应餐时锚点"
                        ),
                        rule_id="time.multi_role.meal_anchor.v1",
                    )
                )
        # A meal-period default is safe only for a genuinely single-role
        # request.  For “活动和晚饭” the StructureCompiler owns the shape and
        # Gate should not collapse the whole outing into an evening dinner
        # window merely because one required role is DINNER.
        elif (
            raw.time_text is None
            and departure_at is None
            and raw.required_stop_roles == (StopRole.DINNER,)
        ):
            dinner_window = TimeWindow(start="18:00", end="22:00")
            time_value = ConstraintValue[TimeWindow](
                value=dinner_window,
                source=ConstraintSource.USER_INFERRED,
                raw_text=interpretation.evidence_map.get("required_stop_roles"),
                rule_id="time.dinner_role.zh_cn.v1",
            )
        elif (
            raw.time_text is None
            and departure_at is None
            and raw.required_stop_roles == (StopRole.LUNCH,)
        ):
            lunch_window = TimeWindow(start="11:30", end="14:00")
            time_value = ConstraintValue[TimeWindow](
                value=lunch_window,
                source=ConstraintSource.USER_INFERRED,
                raw_text=interpretation.evidence_map.get("required_stop_roles"),
                rule_id="time.lunch_role.zh_cn.v1",
            )
        elif raw.time_text is None and departure_at is not None and return_by is not None:
            if return_by > departure_at:
                time_value = ConstraintValue[TimeWindow](
                    value=TimeWindow(start=departure_at, end=return_by),
                    source=ConstraintSource.USER_INFERRED,
                    raw_text=raw.departure_at_text,
                    rule_id="time.window.from_departure_return.v1",
                )
            else:
                # Keep both user clocks intact for the Planner's direct
                # chronology conflict, while avoiding an invalid reversed
                # TimeWindow object that would mask that conflict.
                horizon = TimeWindow(
                    start=departure_at,
                    end=self.DEFAULT_PLANNING_HORIZON_END,
                )
                time_value = ConstraintValue[TimeWindow](
                    value=horizon,
                    source=ConstraintSource.DEFAULT_RULE,
                    raw_text=raw.departure_at_text,
                    rule_id="time.departure_only.planning_horizon.v1",
                )
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=horizon.model_dump(),
                        reason=(
                            "出发时间晚于返程截止，结束时间仅暂用当日排程搜索范围；"
                            "实际结果由出发与返程硬约束冲突决定"
                        ),
                        rule_id="time.departure_only.planning_horizon.v1",
                    )
                )
        elif raw.time_text is None and departure_at is not None:
            default_time = TimeWindow(
                start=departure_at,
                end=self.DEFAULT_PLANNING_HORIZON_END,
            )
            time_value = ConstraintValue[TimeWindow](
                value=default_time,
                source=ConstraintSource.DEFAULT_RULE,
                raw_text=raw.departure_at_text,
                rule_id="time.departure_only.planning_horizon.v1",
            )
            assumptions.append(
                Assumption(
                    field="time_window",
                    value=default_time.model_dump(),
                    reason=(
                        "已指定精确出发时刻；结束时间仅作为当日排程搜索范围，"
                        "不代表用户要求此时返程"
                    ),
                    rule_id="time.departure_only.planning_horizon.v1",
                )
            )
        elif raw.time_text is None:
            default_time = TimeWindow(start="14:00", end="18:00")
            time_value = ConstraintValue[TimeWindow](
                value=default_time,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="time.default.afternoon.v1",
            )
            assumptions.append(
                Assumption(
                    field="time_window",
                    value=default_time.model_dump(),
                    reason="用户未指定时间，先按周末下午规划",
                    rule_id="time.default.afternoon.v1",
                )
            )
        distance = self._normalize_distance(
            raw.max_distance_km,
            raw.max_distance_text,
        )
        if distance is not None and distance.source == ConstraintSource.DEFAULT_RULE:
            assumptions.append(
                Assumption(
                    field="max_distance_km",
                    value=distance.value,
                    reason="用户未指定距离，使用北京城区默认搜索半径",
                    rule_id=distance.rule_id or "distance.default.beijing.v1",
                )
            )

        total_distance = None
        if raw.total_distance_km is not None:
            total_distance = ConstraintValue[float](
                value=raw.total_distance_km,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=raw.total_distance_text,
                confidence=self._confidence(interpretation, "total_distance_km"),
                rule_id="total_distance.total_route.v1",
            )

        budget = None
        if raw.budget_per_person is not None:
            budget = ConstraintValue[int](
                value=raw.budget_per_person,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=raw.budget_text,
                confidence=self._confidence(interpretation, "budget_per_person"),
            )
        elif not raw.strict_budget:
            budget = ConstraintValue[int](
                value=self.DEFAULT_BUDGET,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="budget.default.beijing.v1",
            )
            assumptions.append(
                Assumption(
                    field="budget_per_person",
                    value=self.DEFAULT_BUDGET,
                    reason="用户未指定预算，使用北京演示场景的人均默认预算",
                    rule_id="budget.default.beijing.v1",
                )
            )

        # 只有检测到明确人群信息才标记 USER_EXPLICIT；否则默认一位成人，并把
        # 该决定放进 assumptions，避免“默认两个人”之类的隐式业务猜测。
        has_party = any(
            value is not None
            for value in (raw.adults, raw.children, raw.child_age)
        ) or bool(raw.members)
        party = PartyProfile(
            adults=raw.adults if raw.adults is not None else 1,
            children=raw.children if raw.children is not None else (1 if raw.child_age is not None else 0),
            child_age=raw.child_age,
            members=raw.members,
        )
        party_value = ConstraintValue[PartyProfile](
            value=party,
            source=(
                ConstraintSource.USER_INFERRED
                if has_party and "party" in interpretation.inferred_fields
                else ConstraintSource.USER_EXPLICIT
                if has_party
                else ConstraintSource.DEFAULT_RULE
            ),
            raw_text=(
                interpretation.evidence_map.get("party")
                if has_party
                else None
            ),
            confidence=self._confidence(interpretation, "party") if has_party else None,
            rule_id=None if has_party else "party.default.solo.v1",
        )
        if not has_party:
            assumptions.append(
                Assumption(
                    field="party",
                    value=party.model_dump(),
                    reason="用户未说明同行人，先按一位成人规划",
                    rule_id="party.default.solo.v1",
                )
            )

        # 显式地点只能由 GeocodingProvider 变成坐标；无法解析时保留 None，由
        # Gate 反问。用户没有给地点才可以使用会话默认出发地。
        location = None
        geocoding_fact: GeocodingFact | None = None
        if raw.location_text:
            if self._geocoding_provider is not None:
                geocoding_fact = self._geocoding_provider.geocode(
                    GeocodeRequest(
                        location_text=raw.location_text,
                        city=environment.default_location.city,
                    )
                )
            if (
                geocoding_fact is not None
                and geocoding_fact.resolution == GeocodeResolution.RESOLVED
                and geocoding_fact.point is not None
            ):
                location = ConstraintValue[GeoLocation](
                    value=GeoLocation(
                        city=geocoding_fact.city or environment.default_location.city,
                        district=geocoding_fact.district or "",
                        address=geocoding_fact.address or raw.location_text,
                        latitude=geocoding_fact.point.latitude,
                        longitude=geocoding_fact.point.longitude,
                        adcode=geocoding_fact.adcode,
                    ),
                    source=ConstraintSource.REAL_TOOL,
                    raw_text=raw.location_text,
                    rule_id="location.geocoded.v1",
                )
        else:
            location = ConstraintValue[GeoLocation](
                value=environment.default_location,
                source=ConstraintSource.SYSTEM_CONTEXT,
                rule_id="location.session_default.v1",
            )

        constraints = NormalizedConstraints(
            date=date_value,
            time_scope=time_scope_value,
            time_window=time_value,
            duration_minutes=(
                ConstraintValue[int](
                    value=raw.duration_minutes,
                    source=ConstraintSource.USER_EXPLICIT,
                    confidence=self._confidence(interpretation, "duration_minutes"),
                )
                if raw.duration_minutes is not None
                else None
            ),
            location=location,
            party=party_value,
            budget_per_person=budget,
            max_distance_km=distance,
            preferences=raw.preferences,
            diet_tags=raw.diet_tags,
            scene_tags=raw.scene_tags,
            avoid=raw.avoid,
            strict_budget=raw.strict_budget,
            require_availability_confirmation=raw.require_availability_confirmation,
            departure_at=departure_at_value,
            exact_stop_count=exact_stop_count,
            required_stop_roles=required_stop_roles,
            return_by=return_by_value,
            total_distance_km=total_distance,
        )
        return EnrichmentResult(
            constraints=constraints,
            assumptions=assumptions,
            geocoding_fact=geocoding_fact,
        )

    @staticmethod
    def _confidence(interpretation: Interpretation, field: str) -> float | None:
        return interpretation.extraction_confidence.get(field)

    @staticmethod
    def _normalize_date(raw_text: str | None, current_date: Date) -> Date | None:
        """Legacy wrapper around the shared bounded temporal compiler."""

        return TemporalCompiler.normalize_date(raw_text, current_date)

    @staticmethod
    def _next_saturday(current_date: Date) -> Date:
        days_ahead = (5 - current_date.weekday()) % 7
        return current_date + timedelta(days=days_ahead)

    @staticmethod
    def _normalize_time_window(raw_text: str | None) -> TimeWindow | None:
        """Legacy wrapper around the shared bounded temporal compiler."""

        return TemporalCompiler.normalize_time_window(raw_text)

    @staticmethod
    def _infer_time_scope(raw_text: str | None) -> TimeScope | None:
        """Compatibility mapping for legacy adapters that only emit time_text."""

        return TemporalCompiler.extract_time(raw_text)[1]

    @staticmethod
    def _time_window_for_scope(scope: TimeScope) -> TimeWindow | None:
        return TemporalCompiler.time_window_for_scope(scope)

    @classmethod
    def _multi_role_default_time_window(
        cls,
        required_roles: tuple[StopRole, ...],
        *,
        return_by: str | None,
        exact_stop_count: int | None,
    ) -> TimeWindow | None:
        """Choose a finite default window for multi-role meal requests.

        This helper is intentionally limited to role combinations already
        supported by the closed skeleton registry. It never overrides an
        explicit numeric time range; ``return_by`` only acts as an upper bound
        when the window is otherwise inferred.
        """

        # A single named meal role can still belong to a multi-stop request,
        # e.g. “下午出去，晚饭想吃家常菜”.  With no exact one-stop request,
        # keep the afternoon start but widen the inferred horizon to dinner;
        # otherwise the 14:00–18:00 fuzzy afternoon window makes the required
        # dinner role impossible.  An exact one-stop dinner remains handled by
        # the dedicated 18:00–22:00 branch below.
        if len(required_roles) < 2 and exact_stop_count == 1:
            return None
        if (
            len(required_roles) < 2
            and required_roles == (StopRole.DINNER,)
            and exact_stop_count is None
        ):
            return TimeWindow(start="14:00", end="21:00")
        if len(required_roles) < 2 and not (
            exact_stop_count is not None and exact_stop_count > 1
        ):
            return None
        roles = tuple(required_roles)
        has_lunch = StopRole.LUNCH in roles
        has_dinner = StopRole.DINNER in roles
        if not has_lunch and not has_dinner:
            return None

        if has_lunch and has_dinner:
            start = "09:00" if roles[0] == StopRole.ACTIVITY else "11:30"
            default_end = "20:30"
        elif has_dinner:
            start = "14:00"
            default_end = "21:00"
        elif roles[0] == StopRole.ACTIVITY:
            start = "09:00"
            default_end = "14:00"
        else:
            start = "11:30"
            # With an explicit multi-stop count, leave room for the other
            # stops around lunch instead of treating lunch as the whole plan.
            default_end = "18:00"

        end = default_end
        if return_by is not None and return_by < end:
            end = return_by
        return TimeWindow(start=start, end=end)

    @staticmethod
    def _normalize_return_by(
        return_by_text: str | None,
        explicit: str | None,
    ) -> str | None:
        """把“最晚 18:00 到家”一类表达归一化成 HH:MM 时钟，无法解析则不猜。"""
        candidate = explicit
        if candidate is None and return_by_text:
            match = re.search(r"(\d{1,2})[:：](\d{2})", return_by_text)
            if match:
                candidate = f"{match.group(1)}:{match.group(2)}"
            else:
                chinese = re.search(
                    r"(?:(上午|下午|晚上)\s*)?"
                    r"([一二三四五六七八九十两]+)点"
                    r"(?:(半)|([零〇一二三四五六七八九十两]+)分?)?"
                    r"\s*(?:前|之前)?\s*(?:到家|回家|回来)",
                    return_by_text,
                )
                if chinese:
                    period, hour_text, half_text, minute_text = chinese.groups()
                    hour = EnrichmentService._parse_chinese_hour(hour_text)
                    if hour is not None:
                        if half_text:
                            minute = 30
                        elif minute_text:
                            minute = EnrichmentService._parse_chinese_minute(minute_text)
                        else:
                            minute = 0
                        if minute is not None:
                            candidate = EnrichmentService._normalize_clock_with_period(
                                period,
                                hour,
                                minute,
                            )
        if candidate is None:
            return None
        parts = candidate.strip().split(":")
        if len(parts) != 2:
            return None
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        return f"{hour:02d}:{minute:02d}"

    @classmethod
    def _normalize_departure_at(
        cls,
        departure_at_text: str | None,
        explicit: str | None,
    ) -> str | None:
        """Parse only controlled, explicit departure-clock expressions to HH:MM."""
        if explicit is not None:
            return cls._normalize_clock(explicit)
        if not departure_at_text:
            return None
        text = departure_at_text.strip()
        if any(marker in text for marker in ("左右", "大约", "约")):
            return None
        numeric = re.fullmatch(
            r"(?:(上午|下午|晚上)\s*)?(\d{1,2})[:：](\d{2})\s*(?:准时\s*)?(?:出发|离开)",
            text,
        )
        if numeric:
            period, hour_text, minute_text = numeric.groups()
            return cls._normalize_clock_with_period(period, int(hour_text), int(minute_text))
        chinese = re.fullmatch(
            r"(?:(上午|下午|晚上)\s*)?([一二三四五六七八九十两]+)点"
            r"(?:(半)|([零〇一二三四五六七八九十两]+)分?)?"
            r"\s*(?:准时\s*)?(?:出发|离开)",
            text,
        )
        if not chinese:
            return None
        period, hour_text, half_text, minute_text = chinese.groups()
        hour = cls._parse_chinese_hour(hour_text)
        if hour is None:
            return None
        if half_text:
            minute = 30
        elif minute_text:
            minute = cls._parse_chinese_minute(minute_text)
            if minute is None:
                return None
        else:
            minute = 0
        return cls._normalize_clock_with_period(period, hour, minute)

    @staticmethod
    def _normalize_clock(value: str) -> str | None:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
        if not match:
            return None
        hour, minute = (int(part) for part in match.groups())
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        return f"{hour:02d}:{minute:02d}"

    @classmethod
    def _normalize_clock_with_period(
        cls,
        period: str | None,
        hour: int,
        minute: int,
    ) -> str | None:
        if period in {"下午", "晚上"} and 1 <= hour <= 11:
            hour += 12
        elif period == "上午" and hour == 12:
            hour = 0
        return cls._normalize_clock(f"{hour:02d}:{minute:02d}")

    @staticmethod
    def _parse_chinese_hour(value: str) -> int | None:
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if value in digits:
            return digits[value]
        if value == "十":
            return 10
        if len(value) == 2 and value[0] == "十" and value[1] in digits:
            return 10 + digits[value[1]]
        return None

    @staticmethod
    def _parse_chinese_minute(value: str) -> int | None:
        """Parse the small Chinese minute vocabulary used in exact clock phrases."""
        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if value in digits:
            return digits[value]
        if value == "十":
            return 10
        if len(value) == 2 and value[0] == "十" and value[1] in digits:
            return 10 + digits[value[1]]
        if len(value) == 2 and value[1] == "十" and value[0] in digits:
            return digits[value[0]] * 10
        if len(value) == 3 and value[1] == "十" and value[0] in digits and value[2] in digits:
            return digits[value[0]] * 10 + digits[value[2]]
        return None

    @staticmethod
    def _add_minutes_to_clock(value: str, minutes: int) -> str | None:
        hour, minute = (int(part) for part in value.split(":"))
        total = hour * 60 + minute + minutes
        if total >= 24 * 60:
            return None
        return f"{total // 60:02d}:{total % 60:02d}"

    @classmethod
    def _normalize_distance(
        cls,
        explicit_km: float | None,
        raw_text: str | None,
    ) -> ConstraintValue[float] | None:
        """把少量模糊距离词映射到产品规则，并保留命中的 rule_id。"""
        if explicit_km is not None:
            return ConstraintValue[float](
                value=explicit_km,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=raw_text,
            )
        rules = {
            "步行可达": (2.0, "distance.walkable.v1"),
            "附近": (5.0, "distance.nearby.v1"),
            "别太远": (cls.DEFAULT_DISTANCE_KM, "distance.not_far.beijing.v1"),
        }
        if raw_text and raw_text.strip() in rules:
            value, rule_id = rules[raw_text.strip()]
            return ConstraintValue[float](
                value=value,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw_text,
                rule_id=rule_id,
            )
        if raw_text:
            return None
        return ConstraintValue[float](
            value=cls.DEFAULT_DISTANCE_KM,
            source=ConstraintSource.DEFAULT_RULE,
            rule_id="distance.default.beijing.v1",
        )

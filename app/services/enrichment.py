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
    EnrichmentResult,
    GeoLocation,
    Interpretation,
    NormalizedConstraints,
    PartyProfile,
    StopRole,
    TimeScope,
    TimeWindow,
)
from app.domain.providers import GeocodeRequest, GeocodeResolution, GeocodingFact
from app.providers.geocoding import GeocodingProvider


class EnvironmentContext(BaseModel):
    """Enrichment 需要的环境事实，由代码或工具适配器提供而非 LLM 猜测。"""
    model_config = ConfigDict(extra="forbid")

    now: datetime
    default_location: GeoLocation


class EnrichmentService:
    """将原始约束转换为 Planner 可直接消费的统一约束。

    重要规则：用户没说时可以使用默认值；用户说了但代码无法可靠解析时不能
    偷偷默认，而应留下 ``None`` 交给 NeedQuestionGate 判断是否反问。
    """

    DEFAULT_BUDGET = 120
    DEFAULT_DISTANCE_KM = 8.0
    DEFAULT_OUTING_MINUTES = 4 * 60
    ALL_DAY_START = "09:00"
    ALL_DAY_END = "21:00"

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
        normalized_date = self._normalize_date(raw.date_text, environment.now.date())
        date_value = None
        if normalized_date is not None:
            date_value = ConstraintValue[Date](
                value=normalized_date,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw.date_text,
                confidence=self._confidence(interpretation, "date_text"),
                rule_id="date.relative.zh_cn.v1",
            )
        elif raw.date_text is None:
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

        # TimeScope is a small semantic vocabulary owned by the interpreter.
        # Keep a legacy text fallback for old adapters/checkpoints, but do not
        # silently treat an explicit unknown expression as an afternoon default.
        time_scope = raw.time_scope or self._infer_time_scope(raw.time_text)
        time_scope_value = (
            ConstraintValue[TimeScope](
                value=time_scope,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw.time_text,
                confidence=(
                    self._confidence(interpretation, "time_scope")
                    or self._confidence(interpretation, "time_text")
                ),
                rule_id="time.scope.zh_cn.v1",
            )
            if time_scope is not None
            else None
        )
        normalized_time = self._normalize_time_window(raw.time_text)
        if time_scope == TimeScope.ALL_DAY:
            normalized_time = TimeWindow(
                start=self.ALL_DAY_START,
                end=self.ALL_DAY_END,
            )
        elif normalized_time is None and time_scope in {
            TimeScope.MORNING,
            TimeScope.AFTERNOON,
            TimeScope.EVENING,
        }:
            normalized_time = self._time_window_for_scope(time_scope)
        time_value = None
        if normalized_time is not None:
            time_value = ConstraintValue[TimeWindow](
                value=normalized_time,
                source=(
                    ConstraintSource.DEFAULT_RULE
                    if time_scope == TimeScope.ALL_DAY
                    else ConstraintSource.USER_INFERRED
                ),
                raw_text=raw.time_text,
                confidence=self._confidence(interpretation, "time_text"),
                rule_id=(
                    "time.all_day.default_window.v1"
                    if time_scope == TimeScope.ALL_DAY
                    else "time.period.zh_cn.v1"
                ),
            )
            if time_scope == TimeScope.ALL_DAY:
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=normalized_time.model_dump(),
                        reason="“全天”按 09:00–21:00 的可见产品规则规划",
                        rule_id="time.all_day.default_window.v1",
                    )
                )
        elif (
            raw.time_text is None
            and departure_at is None
            and StopRole.DINNER in raw.required_stop_roles
        ):
            dinner_window = TimeWindow(start="18:00", end="22:00")
            time_value = ConstraintValue[TimeWindow](
                value=dinner_window,
                source=ConstraintSource.USER_INFERRED,
                raw_text=interpretation.evidence_map.get("required_stop_roles"),
                rule_id="time.dinner_role.zh_cn.v1",
            )
        elif raw.time_text is None and departure_at is not None and return_by is not None:
            time_value = ConstraintValue[TimeWindow](
                value=TimeWindow(start=departure_at, end=return_by),
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw.departure_at_text,
                rule_id="time.window.from_departure_return.v1",
            )
        elif raw.time_text is None and departure_at is not None:
            default_end = self._add_minutes_to_clock(
                departure_at,
                self.DEFAULT_OUTING_MINUTES,
            )
            if default_end is not None:
                default_time = TimeWindow(start=departure_at, end=default_end)
                time_value = ConstraintValue[TimeWindow](
                    value=default_time,
                    source=ConstraintSource.DEFAULT_RULE,
                    raw_text=raw.departure_at_text,
                    rule_id="time.default.from_departure.v1",
                )
                assumptions.append(
                    Assumption(
                        field="time_window",
                        value=default_time.model_dump(),
                        reason="已按你指定的出发时刻，采用默认 4 小时行程窗口",
                        rule_id="time.default.from_departure.v1",
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
        """解析受控的中文相对日期；未知表达返回 None，不做近似猜测。"""
        if not raw_text:
            return None
        text = raw_text.strip()
        offsets = {"今天": 0, "明天": 1, "后天": 2}
        if text in offsets:
            return current_date + timedelta(days=offsets[text])
        weekday_match = re.fullmatch(
            r"(本周|这周|下周|周|星期)([一二三四五六日天])",
            text,
        )
        if weekday_match:
            week_marker, weekday_text = weekday_match.groups()
            target_weekday = {
                "一": 0,
                "二": 1,
                "三": 2,
                "四": 3,
                "五": 4,
                "六": 5,
                "日": 6,
                "天": 6,
            }[weekday_text]
            if week_marker in {"本周", "这周"}:
                return current_date + timedelta(
                    days=target_weekday - current_date.weekday()
                )
            if week_marker == "下周":
                next_monday = current_date + timedelta(
                    days=7 - current_date.weekday()
                )
                return next_monday + timedelta(days=target_weekday)
            return current_date + timedelta(
                days=(target_weekday - current_date.weekday()) % 7
            )
        try:
            return Date.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _next_saturday(current_date: Date) -> Date:
        days_ahead = (5 - current_date.weekday()) % 7
        return current_date + timedelta(days=days_ahead)

    @staticmethod
    def _normalize_time_window(raw_text: str | None) -> TimeWindow | None:
        """将常见时段词或显式起止时间转换成统一时间窗。"""
        if not raw_text:
            return None
        text = raw_text.strip()
        periods = {
            "上午": TimeWindow(start="09:00", end="12:00"),
            "中午": TimeWindow(start="11:30", end="14:00"),
            "下午": TimeWindow(start="14:00", end="18:00"),
            "晚上": TimeWindow(start="18:00", end="22:00"),
        }
        if text in periods:
            return periods[text]
        match = re.fullmatch(r"(\d{1,2}):?(\d{2})?\s*[-到至]\s*(\d{1,2}):?(\d{2})?", text)
        if match:
            start_hour, start_minute, end_hour, end_minute = match.groups()
            return TimeWindow(
                start=f"{int(start_hour):02d}:{int(start_minute or 0):02d}",
                end=f"{int(end_hour):02d}:{int(end_minute or 0):02d}",
            )
        return None

    @staticmethod
    def _infer_time_scope(raw_text: str | None) -> TimeScope | None:
        """Compatibility mapping for legacy adapters that only emit time_text."""

        if not raw_text:
            return None
        text = raw_text.strip()
        if text in {"一整天", "全天", "从早到晚", "玩一天"}:
            return TimeScope.ALL_DAY
        if text == "上午":
            return TimeScope.MORNING
        if text == "下午":
            return TimeScope.AFTERNOON
        if text == "晚上":
            return TimeScope.EVENING
        if re.search(r"\d{1,2}:?\d{2}.*[-到至].*\d{1,2}:?\d{2}", text):
            return TimeScope.EXPLICIT_RANGE
        return None

    @staticmethod
    def _time_window_for_scope(scope: TimeScope) -> TimeWindow:
        return {
            TimeScope.MORNING: TimeWindow(start="09:00", end="12:00"),
            TimeScope.AFTERNOON: TimeWindow(start="14:00", end="18:00"),
            TimeScope.EVENING: TimeWindow(start="18:00", end="22:00"),
        }[scope]

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
            r"(?:(上午|下午|晚上)\s*)?([一二三四五六七八九十两]+)点(半)?\s*(?:准时\s*)?(?:出发|离开)",
            text,
        )
        if not chinese:
            return None
        period, hour_text, minute_text = chinese.groups()
        hour = cls._parse_chinese_hour(hour_text)
        if hour is None:
            return None
        minute = 30 if minute_text == "半" else 0
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

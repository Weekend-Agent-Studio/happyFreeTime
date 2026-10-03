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
    PlanRequest,
    PartyProfile,
    RequestPatch,
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
    def extract_departure_period(cls, text: str | None) -> TimeScope | None:
        """Extract a finite period attached to the departure action.

        This is intentionally separate from :meth:`extract_time`: ``早上出去
        玩`` scopes the whole outing, while ``早上出发`` scopes only the
        departure and must be completed with a clock by the Gate.  The method
        does not infer a clock or a full itinerary window.
        """

        if not text:
            return None
        value = text.strip()
        periods = (
            ("上午", TimeScope.MORNING),
            ("早上", TimeScope.MORNING),
            ("下午", TimeScope.AFTERNOON),
            ("晚上", TimeScope.EVENING),
            ("夜里", TimeScope.EVENING),
            ("夜间", TimeScope.EVENING),
        )
        for phrase, scope in periods:
            escaped = re.escape(phrase)
            if re.search(
                rf"{escaped}.{{0,8}}(?:出发|出门|离开)|"
                rf"(?:出发|出门|离开).{{0,8}}{escaped}",
                value,
            ):
                return scope
        return None

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
    """Compile non-temporal extracted values into an atomic request patch."""

    DEFAULT_BUDGET = 120
    DEFAULT_DISTANCE_KM = 8.0

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

        set_fields: dict[str, object] = {
            "preferences": raw.preferences,
            "diet_tags": raw.diet_tags,
            "scene_tags": raw.scene_tags,
            "avoid": raw.avoid,
            "strict_budget": raw.strict_budget,
            "require_availability_confirmation": raw.require_availability_confirmation,
        }
        if raw.duration_minutes is not None:
            set_fields["duration_minutes"] = ConstraintValue[int](
                value=raw.duration_minutes,
                source=ConstraintSource.USER_EXPLICIT,
                confidence=self._confidence(interpretation, "duration_minutes"),
            )
        for name, value in (
            ("location", location),
            ("party", party_value),
            ("budget_per_person", budget),
            ("max_distance_km", distance),
            ("exact_stop_count", exact_stop_count),
            ("required_stop_roles", required_stop_roles),
            ("total_distance_km", total_distance),
        ):
            if value is not None:
                set_fields[name] = value
        request_patch = RequestPatch(
            base_revision=0,
            source=ConstraintSource.USER_EXPLICIT,
            set_fields=set_fields,
            evidence={
                name: value.raw_text
                for name, value in set_fields.items()
                if isinstance(value, ConstraintValue) and value.raw_text
            },
        )
        return EnrichmentResult(
            request_patch=request_patch,
            assumptions=assumptions,
            geocoding_fact=geocoding_fact,
        )

    @staticmethod
    def _confidence(interpretation: Interpretation, field: str) -> float | None:
        return interpretation.extraction_confidence.get(field)

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

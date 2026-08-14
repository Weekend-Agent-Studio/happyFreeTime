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
    TimeWindow,
)


class EnvironmentContext(BaseModel):
    """Enrichment 需要的环境事实，由代码或工具适配器提供而非 LLM 猜测。"""
    model_config = ConfigDict(extra="forbid")

    now: datetime
    default_location: GeoLocation
    resolved_location: GeoLocation | None = None


class EnrichmentService:
    """将原始约束转换为 Planner 可直接消费的统一约束。

    重要规则：用户没说时可以使用默认值；用户说了但代码无法可靠解析时不能
    偷偷默认，而应留下 ``None`` 交给 NeedQuestionGate 判断是否反问。
    """

    DEFAULT_BUDGET = 120
    DEFAULT_DISTANCE_KM = 8.0

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

        normalized_time = self._normalize_time_window(raw.time_text)
        time_value = None
        if normalized_time is not None:
            time_value = ConstraintValue[TimeWindow](
                value=normalized_time,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw.time_text,
                confidence=self._confidence(interpretation, "time_text"),
                rule_id="time.period.zh_cn.v1",
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

        # 位置解析是工具适配点。M1 通过 EnvironmentContext 接收已解析的位置；
        # 若用户给出的地点无法完成地理编码，系统不会自行编造坐标。
        location = None
        if raw.location_text:
            if environment.resolved_location is not None:
                location = ConstraintValue[GeoLocation](
                    value=environment.resolved_location,
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
        )
        return EnrichmentResult(constraints=constraints, assumptions=assumptions)

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

"""Compile post-plan constraint proposals into normalized constraints.

The Router is allowed to preserve user-language values in ``ConstraintPatch``.
This module is the single place that turns those values into the same
``NormalizedConstraints`` contract used by the create flow.  It is deliberately
not a second conversation router: unresolved values become a field-scoped
question and no partial constraint object is returned.
"""

from __future__ import annotations

import re
from datetime import date as Date

from pydantic import BaseModel, ConfigDict

from app.domain.constraints import (
    ActorContext,
    ConstraintPatch,
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    NormalizedConstraints,
    QuestionDecision,
    TimeScope,
    TimeWindow,
)
from app.domain.planning import ConstraintConflict
from app.domain.providers import GeocodeRequest, GeocodeResolution
from app.providers.geocoding import GeocodingProvider
from app.services.enrichment import EnvironmentContext, TemporalCompiler


class ConstraintPatchResult(BaseModel):
    """One atomic compiler result: updated constraints, question, or conflict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    updated_constraints: NormalizedConstraints | None = None
    question: QuestionDecision | None = None
    conflict: ConstraintConflict | None = None


class ConstraintPatchCompiler:
    """Normalize a bounded patch without allowing raw text into the Planner."""

    _FIELD_RULES = {
        "date": ("改到哪一天？可以说周六、明天或具体日期。", "question.patch.date.v1"),
        "time_window": ("希望从哪个时间段开始安排？例如“下午出发”。", "question.patch.time_window.v1"),
        "departure_at": ("准点出发时间是什么？例如“下午两点出发”。", "question.patch.departure_at.v1"),
        "return_by": ("最晚几点回家？例如“晚上七点前回家”。", "question.patch.return_by.v1"),
        "location": ("新的出发地点是什么？可以提供具体地标。", "question.patch.location.v1"),
        "budget_per_person": ("新的人均预算是多少？例如“人均三百元”。", "question.patch.budget.v1"),
        "max_distance_km": ("最多能接受多少公里的距离？", "question.patch.distance.v1"),
        "total_distance_km": ("全程最多能接受多少公里？", "question.patch.total_distance.v1"),
    }

    def __init__(self, *, geocoding_provider: GeocodingProvider | None = None) -> None:
        self._geocoding_provider = geocoding_provider

    def compile(
        self,
        *,
        base: NormalizedConstraints,
        proposal: ConstraintPatch,
        actor: ActorContext,
        environment: EnvironmentContext,
    ) -> ConstraintPatchResult:
        """Compile all fields atomically against the active constraints."""

        updates: dict[str, object] = {}

        if proposal.date_text:
            date_value = self._compile_date(proposal.date_text, environment)
            if date_value is None:
                return self._ask("date")
            updates["date"] = date_value

        if proposal.time_window_text:
            time_value = self._compile_time_window(proposal.time_window_text)
            if time_value is None:
                return self._ask("time_window")
            scope, window = time_value
            updates["time_scope"] = self._value(scope, proposal.time_window_text, "time.patch.scope.v1")
            updates["time_window"] = self._value(window, proposal.time_window_text, "time.patch.window.v1")
            # A new fuzzy period supersedes an earlier exact departure.  This
            # is an explicit user patch, not an untouched field.
            updates["departure_at"] = None

        if proposal.departure_at_text:
            value = TemporalCompiler.normalize_clock_text(proposal.departure_at_text)
            if value is None:
                return self._ask("departure_at")
            updates["departure_at"] = self._value(value, proposal.departure_at_text, "departure_at.patch.v1")

        if proposal.return_by_text:
            value = TemporalCompiler.normalize_clock_text(proposal.return_by_text)
            if value is None:
                return self._ask("return_by")
            updates["return_by"] = self._value(value, proposal.return_by_text, "return_by.patch.v1")

        if proposal.budget_text:
            amount = self._parse_amount(proposal.budget_text)
            if amount is None or amount <= 0:
                return self._ask("budget_per_person")
            updates["budget_per_person"] = self._value(amount, proposal.budget_text, "budget.patch.v1")
            updates["strict_budget"] = True if proposal.strict_budget is None else proposal.strict_budget
        elif proposal.strict_budget is not None:
            updates["strict_budget"] = proposal.strict_budget

        if proposal.max_distance_text:
            distance = self._parse_float(proposal.max_distance_text)
            if distance is None or distance <= 0:
                return self._ask("max_distance_km")
            updates["max_distance_km"] = self._value(distance, proposal.max_distance_text, "distance.patch.v1")

        if proposal.total_distance_text:
            distance = self._parse_float(proposal.total_distance_text)
            if distance is None or distance <= 0:
                return self._ask("total_distance_km")
            updates["total_distance_km"] = self._value(distance, proposal.total_distance_text, "total_distance.patch.v1")

        if proposal.location_text:
            if self._geocoding_provider is None:
                return self._ask("location")
            geocoding_fact = self._geocoding_provider.geocode(
                GeocodeRequest(
                    location_text=proposal.location_text,
                    city=(base.location.value.city if base.location is not None else environment.default_location.city),
                )
            )
            if geocoding_fact.resolution != GeocodeResolution.RESOLVED or geocoding_fact.point is None:
                return self._ask("location")
            location = GeoLocation(
                city=geocoding_fact.city or environment.default_location.city,
                district=geocoding_fact.district or "",
                address=geocoding_fact.address or proposal.location_text,
                latitude=geocoding_fact.point.latitude,
                longitude=geocoding_fact.point.longitude,
                adcode=geocoding_fact.adcode,
            )
            updates["location"] = self._value(location, proposal.location_text, "location.patch.geocoded.v1")

        if proposal.preferences:
            updates["preferences"] = self._merge(base.preferences, proposal.preferences)
        if proposal.diet_tags:
            updates["diet_tags"] = self._merge(base.diet_tags, proposal.diet_tags)
        if proposal.avoid:
            updates["avoid"] = self._merge(base.avoid, proposal.avoid)

        self._apply_clears(updates, proposal.clear_fields)
        if not updates:
            return ConstraintPatchResult(
                question=QuestionDecision(
                    need_question=True,
                    field="constraint_patch",
                    continuation="patch_constraints",
                    question="你想补充哪条约束？可以选择时间、预算、地点或偏好。",
                    severity="blocking",
                    rule_id="question.patch.empty.v2",
                    allow_free_text=False,
                )
            )

        candidate = base.model_copy(update=updates)
        departure = candidate.departure_at.value if candidate.departure_at else None
        return_by = candidate.return_by.value if candidate.return_by else None
        if departure is not None and return_by is not None and departure >= return_by:
            return ConstraintPatchResult(
                conflict=ConstraintConflict(
                    code="DEPARTURE_NOT_BEFORE_RETURN_BY",
                    message="准点出发时间必须早于最晚回家时间。",
                    fields=["departure_at", "return_by"],
                    relaxation_options=["提前出发", "延后最晚回家时间"],
                )
            )
        return ConstraintPatchResult(updated_constraints=candidate)

    @classmethod
    def proposal_from_text(cls, text: str, *, has_plans: bool) -> ConstraintPatch | None:
        """Offline Demo adapter for the same proposal contract as the LLM.

        This is intentionally the only deterministic text adapter for patches.
        The Graph and Planner never inspect these phrases directly.
        """
        if not has_plans:
            return None
        value = text.strip()
        if not value or any(
            marker in value
            for marker in ("换站", "换这站", "换个", "换活动", "换晚餐", "替换", "更换")
        ):
            return None
        if "保留" in value and any(marker in value for marker in ("活动", "晚餐", "晚饭", "餐厅")):
            # A role-preservation sentence is a replacement request, even if
            # it does not literally contain the verb “替换”.
            return None
        date_text, _, _, _, _ = TemporalCompiler.extract_date(value)
        time_text, scope, explicit_window = TemporalCompiler.extract_time(value)
        departure = None
        clock_like = re.search(
            r"(?:\d{1,2}[:：]\d{1,2}|[零〇一二两三四五六七八九十]+点(?:半|[零〇一二两三四五六七八九十]+分?)?)",
            value,
        )
        if re.search(r"出发|出门|离开", value) and clock_like and TemporalCompiler.normalize_clock_text(value):
            departure = value
            time_text = None
        return_by = value if re.search(r"回家|到家|回来", value) else None
        strict_budget: bool | None = None
        budget = None
        if "预算不限" in value or "不设预算" in value:
            strict_budget = False
        elif "预算" in value or "人均" in value or "每人" in value:
            budget = value
            strict_budget = True
        max_distance = value if re.search(r"(?:公里|千米|km|KM)", value) else None
        location_match = re.search(r"从(?P<location>[^，,。；;]{2,30})(?:出发|出门)", value)
        location = location_match.group("location") if location_match else None
        preferences = tuple(label for keyword, label in (("安静", "安静"), ("聊天", "适合聊天"), ("浪漫", "浪漫"), ("轻松", "轻松"), ("不累", "不累")) if keyword in value)
        diet_tags = tuple(label for keyword, label in (("少辣", "少辣"), ("清淡", "清淡")) if keyword in value)
        avoid = ("博物馆",) if "不要博物馆" in value else ()
        has_patch_marker = any(marker in value for marker in ("补充", "忘了说", "对了", "另外", "再加", "重新规划", "改到", "改成", "改为"))
        if not (has_patch_marker or date_text or departure or return_by or budget or strict_budget is not None or location or preferences or diet_tags or avoid or max_distance or scope is not None or explicit_window is not None):
            return None
        return ConstraintPatch(
            date_text=date_text,
            return_by_text=return_by,
            departure_at_text=departure,
            time_window_text=value if time_text and departure is None and return_by is None else None,
            location_text=location,
            budget_text=budget,
            max_distance_text=max_distance,
            preferences=preferences,
            diet_tags=diet_tags,
            avoid=avoid,
            strict_budget=strict_budget,
            clear_fields=("budget_per_person", "strict_budget") if strict_budget is False else (),
        )

    def _compile_date(self, text: str, environment: EnvironmentContext) -> ConstraintValue[Date] | None:
        raw, reference, weekday, week_offset, absolute = TemporalCompiler.extract_date(text)
        value = TemporalCompiler.resolve_date(reference, current_date=environment.now.date(), weekday=weekday, week_offset=week_offset, absolute_date=absolute)
        if value is None:
            return None
        return self._value(value, raw or text, "date.patch.v1")

    @staticmethod
    def _compile_time_window(text: str) -> tuple[TimeScope, TimeWindow] | None:
        _, scope, explicit = TemporalCompiler.extract_time(text)
        if explicit is not None:
            return TimeScope.EXPLICIT_RANGE, explicit
        if scope is None:
            return None
        window = TemporalCompiler.time_window_for_scope(scope)
        return (scope, window) if window is not None else None

    @staticmethod
    def _value(value, raw_text: str, rule_id: str) -> ConstraintValue:
        return ConstraintValue(value=value, source=ConstraintSource.USER_EXPLICIT, raw_text=raw_text, rule_id=rule_id)

    @staticmethod
    def _merge(existing: list[str], additions: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys((*existing, *additions)))

    @staticmethod
    def _apply_clears(updates: dict[str, object], fields: tuple[str, ...]) -> None:
        value_fields = {"date", "time_scope", "time_window", "location", "budget_per_person", "max_distance_km", "total_distance_km", "departure_at", "return_by"}
        list_fields = {"preferences", "diet_tags", "avoid"}
        for field in fields:
            if field in value_fields or field in list_fields:
                updates[field] = [] if field in list_fields else None
            if field == "strict_budget":
                updates[field] = False

    @classmethod
    def _ask(cls, field: str) -> ConstraintPatchResult:
        question, rule_id = cls._FIELD_RULES[field]
        return ConstraintPatchResult(
            question=QuestionDecision(
                need_question=True,
                field=field,
                continuation="patch_constraints",
                question=question,
                severity="blocking",
                rule_id=rule_id,
            )
        )

    @staticmethod
    def _parse_float(value: str) -> float | None:
        match = re.search(r"\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None

    @staticmethod
    def _parse_amount(value: str) -> int | None:
        match = re.search(r"\d{1,5}|[零〇一二两三四五六七八九十百千万]+", value)
        if not match:
            return None
        token = match.group(0)
        if token.isdigit():
            return int(token)
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        total = 0
        section = 0
        number = 0
        for char in token:
            if char in digits:
                number = digits[char]
                continue
            unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        return total + section + number

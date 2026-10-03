"""Compile post-plan wire proposals into the shared RequestPatch contract."""

from __future__ import annotations

import re
from datetime import date as Date

from pydantic import BaseModel, ConfigDict

from app.domain.constraints import (
    ActorContext,
    ClarificationIssue,
    ConstraintPatch,
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    PlanRequest,
    RequestPatch,
    TimeScope,
    TimeWindow,
)
from app.domain.providers import GeocodeRequest, GeocodeResolution
from app.providers.geocoding import GeocodingProvider
from app.services.enrichment import EnvironmentContext, TemporalCompiler


class ConstraintPatchCompilation(BaseModel):
    """Normalized update operations and any fields that still need input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patch: RequestPatch
    issues: tuple[ClarificationIssue, ...] = ()


class ConstraintPatchProposalCompiler:
    """Normalize a proposal; ConstraintEngine alone applies and validates it."""

    def __init__(self, *, geocoding_provider: GeocodingProvider | None = None) -> None:
        self._geocoding_provider = geocoding_provider

    def compile(
        self,
        *,
        base: PlanRequest,
        proposal: ConstraintPatch,
        actor: ActorContext,
        environment: EnvironmentContext,
    ) -> ConstraintPatchCompilation:
        """Compile every supported field against the current request revision."""

        updates: dict[str, object] = {}
        add_to_fields: dict[str, tuple[str, ...]] = {}
        clear_fields: list[str] = []
        issues: list[ClarificationIssue] = []

        if proposal.date_text:
            date_value = self._compile_date(proposal.date_text, environment)
            if date_value is None:
                issues.append(self._issue("date", base.revision))
            else:
                updates["planning_window.date"] = date_value

        if proposal.departure_period is not None and not proposal.departure_at_text:
            # “早上/下午出发” constrains only the departure action.  Do not
            # compile it as the itinerary's overall time window; ask for the
            # exact clock through the same field-scoped resume path as CREATE.
            issues.append(self._issue("departure_at", base.revision))

        if proposal.time_window_text:
            time_value = self._compile_time_window(proposal.time_window_text)
            if time_value is None:
                issues.append(self._issue("time_window", base.revision))
            else:
                scope, window = time_value
                source = (
                    ConstraintSource.USER_INFERRED
                    if scope != TimeScope.EXPLICIT_RANGE
                    else ConstraintSource.USER_EXPLICIT
                )
                rule_id = (
                    "time.trip.range.v1"
                    if scope == TimeScope.EXPLICIT_RANGE
                    else f"time.trip.{scope.value}.v1"
                )
                updates["planning_window.start_at"] = ConstraintValue[str](
                    value=window.start,
                    source=source,
                    raw_text=proposal.time_window_text,
                    rule_id=rule_id,
                )
                updates["planning_window.end_at"] = ConstraintValue[str](
                    value=window.end,
                    source=source,
                    raw_text=proposal.time_window_text,
                    rule_id=rule_id,
                )
                updates["planning_window.start_kind"] = "trip_start"
                updates["planning_window.end_kind"] = "trip_end"

        if proposal.departure_at_text:
            value = TemporalCompiler.normalize_clock_text(proposal.departure_at_text)
            if value is None:
                issues.append(self._issue("departure_at", base.revision))
            else:
                updates["planning_window.start_at"] = self._value(
                    value, proposal.departure_at_text, "time.departure.clock.v1"
                )
                updates["planning_window.start_kind"] = "departure"
            if value is not None and base.planning_window.end_at is None and "planning_window.end_at" not in updates:
                updates["planning_window.end_at"] = ConstraintValue[str](
                    value="23:59",
                    source=ConstraintSource.DEFAULT_RULE,
                    rule_id="time.departure.default_horizon.v1",
                )

        if proposal.return_by_text:
            value = TemporalCompiler.normalize_clock_text(proposal.return_by_text)
            if value is None:
                issues.append(self._issue("return_by", base.revision))
            else:
                updates["planning_window.end_at"] = self._value(
                    value, proposal.return_by_text, "time.return.clock.v1"
                )
                updates["planning_window.end_kind"] = "return_deadline"

        if proposal.budget_text:
            amount = self._parse_amount(proposal.budget_text)
            if amount is None or amount <= 0:
                issues.append(self._issue("budget_per_person", base.revision))
            else:
                updates["budget_per_person"] = self._value(amount, proposal.budget_text, "budget.patch.v1")
                updates["strict_budget"] = True if proposal.strict_budget is None else proposal.strict_budget
        elif proposal.strict_budget is not None:
            updates["strict_budget"] = proposal.strict_budget

        if proposal.max_distance_text:
            distance = self._parse_float(proposal.max_distance_text)
            if distance is None or distance <= 0:
                issues.append(self._issue("max_distance_km", base.revision))
            else:
                updates["max_distance_km"] = self._value(distance, proposal.max_distance_text, "distance.patch.v1")

        if proposal.total_distance_text:
            distance = self._parse_float(proposal.total_distance_text)
            if distance is None or distance <= 0:
                issues.append(self._issue("total_distance_km", base.revision))
            else:
                updates["total_distance_km"] = self._value(distance, proposal.total_distance_text, "total_distance.patch.v1")

        if proposal.location_text:
            if self._geocoding_provider is None:
                issues.append(self._issue("location", base.revision))
            else:
                geocoding_fact = self._geocoding_provider.geocode(
                    GeocodeRequest(
                        location_text=proposal.location_text,
                        city=(base.location.value.city if base.location is not None else environment.default_location.city),
                    )
                )
                if geocoding_fact.resolution != GeocodeResolution.RESOLVED or geocoding_fact.point is None:
                    issues.append(self._issue("location", base.revision))
                else:
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
            add_to_fields["preferences"] = proposal.preferences
        if proposal.diet_tags:
            add_to_fields["diet_tags"] = proposal.diet_tags
        if proposal.avoid:
            add_to_fields["avoid"] = proposal.avoid

        self._compile_clears(clear_fields, proposal.clear_fields)
        if proposal.strict_budget is False:
            updates["strict_budget"] = False
        if not updates and not add_to_fields and not clear_fields and not issues:
            issues.append(ClarificationIssue(
                field="constraint_patch",
                code="EMPTY_CONSTRAINT_PATCH",
                reason="no_supported_constraint_change",
                expected_value_type="constraint_patch",
                request_revision=base.revision,
            ))

        return ConstraintPatchCompilation(
            patch=RequestPatch(
                base_revision=base.revision,
                set_fields=updates,
                clear_fields=tuple(dict.fromkeys(clear_fields)),
                add_to_fields=add_to_fields,
                source=ConstraintSource.USER_EXPLICIT,
            ),
            issues=tuple(issues),
        )

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
        departure_period = TemporalCompiler.extract_departure_period(value)
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
        if not (has_patch_marker or date_text or departure or departure_period or return_by or budget or strict_budget is not None or location or preferences or diet_tags or avoid or max_distance or scope is not None or explicit_window is not None):
            return None
        return ConstraintPatch(
            date_text=date_text,
            return_by_text=return_by,
            departure_at_text=departure,
            departure_period=departure_period,
            time_window_text=(
                value
                if time_text
                and departure is None
                and departure_period is None
                and return_by is None
                else None
            ),
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
    def _compile_clears(target: list[str], fields: tuple[str, ...]) -> None:
        aliases = {
            "date": ("planning_window.date",),
            "time_scope": ("planning_window.start_at", "planning_window.end_at"),
            "time_window": ("planning_window.start_at", "planning_window.end_at"),
            "departure_at": ("planning_window.start_at",),
            "return_by": ("planning_window.end_at",),
        }
        for field in fields:
            if field == "strict_budget":
                continue
            if field in aliases:
                target.extend(aliases[field])
            else:
                target.append(field)

    @staticmethod
    def _issue(field: str, revision: int) -> ClarificationIssue:
        codes = {
            "date": ("DATE_REQUIRES_RESOLUTION", "date"),
            "time_window": ("TRIP_TIME_REQUIRES_RESOLUTION", "time_window"),
            "departure_at": ("DEPARTURE_TIME_REQUIRES_CLOCK", "clock"),
            "return_by": ("RETURN_TIME_REQUIRES_CLOCK", "clock"),
            "location": ("LOCATION_REQUIRES_RESOLUTION", "location"),
            "budget_per_person": ("BUDGET_AMOUNT_REQUIRED", "integer"),
            "max_distance_km": ("MAX_DISTANCE_REQUIRES_NUMBER", "number"),
            "total_distance_km": ("TOTAL_DISTANCE_REQUIRES_NUMBER", "number"),
        }
        code, expected = codes.get(field, ("PATCH_FIELD_REQUIRES_INPUT", "text"))
        return ClarificationIssue(
            field=field,
            code=code,
            reason="explicit_patch_value_could_not_be_normalized",
            expected_value_type=expected,
            request_revision=revision,
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

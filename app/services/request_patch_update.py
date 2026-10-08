"""Compile post-plan Router proposals into the canonical RequestPatch contract."""

from __future__ import annotations

import re
from datetime import date as Date

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
from app.services.request_patch_compiler import RequestPatchCompilation


class RequestPatchUpdateCompiler:
    """Normalize a proposal; ConstraintEngine alone applies and validates it."""

    def __init__(self, *, geocoding_provider: GeocodingProvider | None = None) -> None:
        self._geocoding_provider = geocoding_provider

    def compile_update_proposal(
        self,
        *,
        base: PlanRequest,
        proposal: ConstraintPatch,
        actor: ActorContext,
        environment: EnvironmentContext,
        evidence_map: dict[str, str] | None = None,
    ) -> RequestPatchCompilation:
        """Compile every supported field against the current request revision."""

        updates: dict[str, object] = {}
        add_to_fields: dict[str, tuple[str, ...]] = {}
        clear_fields: list[str] = []
        issues: list[ClarificationIssue] = []
        evidence_map = evidence_map or {}
        patch_evidence = evidence_map.get("patch")

        if proposal.clear_structure:
            clear_fields.extend(("exact_stop_count", "required_stop_roles"))
        elif proposal.exact_stop_count is not None:
            updates["exact_stop_count"] = self._value(
                proposal.exact_stop_count,
                evidence_map.get("exact_stop_count") or patch_evidence,
                "plan_structure.exact_stop_count.patch.v1",
            )
        if not proposal.clear_structure and proposal.required_stop_roles:
            updates["required_stop_roles"] = self._value(
                proposal.required_stop_roles,
                evidence_map.get("required_stop_roles") or patch_evidence,
                "plan_structure.required_stop_roles.patch.v1",
            )
        if not proposal.clear_structure and proposal.add_required_stop_roles:
            current_roles = (
                base.required_stop_roles.value
                if base.required_stop_roles is not None
                else ()
            )
            # ``add`` is intentionally not set-union: a second activity is a
            # meaningful extra slot and must remain representable.
            merged_roles = tuple((*current_roles, *proposal.add_required_stop_roles))
            updates["required_stop_roles"] = self._value(
                merged_roles,
                evidence_map.get("required_stop_roles")
                or evidence_map.get("add_required_stop_roles")
                or patch_evidence,
                "plan_structure.required_stop_roles.append.v1",
            )
        if proposal.structure_hint_text and not (
            proposal.exact_stop_count is not None
            or proposal.required_stop_roles
            or proposal.add_required_stop_roles
            or proposal.clear_structure
        ):
            issues.append(
                self._issue(
                    "exact_stop_count",
                    base.revision,
                    code="STOP_COUNT_REQUIRES_NUMBER",
                    expected="integer",
                )
            )

        if proposal.activity_time_scope is not None:
            updates["activity_time_scope"] = self._value(
                proposal.activity_time_scope,
                proposal.activity_time_text or evidence_map.get("activity_time_scope"),
                f"time.activity.{proposal.activity_time_scope.value}.patch.v1",
            )

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

        for raw_text, field, rule_id in (
            (proposal.origin_text, "location", "location.patch.geocoded.v1"),
            (proposal.planning_area_text, "planning_area", "planning_area.patch.geocoded.v1"),
        ):
            if not raw_text:
                continue
            if self._geocoding_provider is None:
                issues.append(self._issue(field, base.revision))
                continue
            geocoding_fact = self._geocoding_provider.geocode(
                GeocodeRequest(
                    location_text=raw_text,
                    city=(base.location.value.city if base.location is not None else environment.default_location.city),
                )
            )
            if geocoding_fact.resolution != GeocodeResolution.RESOLVED or geocoding_fact.point is None:
                issues.append(self._issue(field, base.revision))
                continue
            location = GeoLocation(
                city=geocoding_fact.city or environment.default_location.city,
                district=geocoding_fact.district or "",
                address=geocoding_fact.address or raw_text,
                latitude=geocoding_fact.point.latitude,
                longitude=geocoding_fact.point.longitude,
                adcode=geocoding_fact.adcode,
            )
            updates[field] = self._value(location, raw_text, rule_id)

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

        return RequestPatchCompilation(
            patch=RequestPatch(
                base_revision=base.revision,
                set_fields=updates,
                clear_fields=tuple(dict.fromkeys(clear_fields)),
                add_to_fields=add_to_fields,
                source=ConstraintSource.USER_EXPLICIT,
            ),
            issues=tuple(issues),
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
    def _issue(
        field: str,
        revision: int,
        *,
        code: str | None = None,
        expected: str | None = None,
    ) -> ClarificationIssue:
        codes = {
            "date": ("DATE_REQUIRES_RESOLUTION", "date"),
            "time_window": ("TRIP_TIME_REQUIRES_RESOLUTION", "time_window"),
            "departure_at": ("DEPARTURE_TIME_REQUIRES_CLOCK", "clock"),
            "return_by": ("RETURN_TIME_REQUIRES_CLOCK", "clock"),
            "location": ("LOCATION_REQUIRES_RESOLUTION", "location"),
            "planning_area": ("PLANNING_AREA_REQUIRES_RESOLUTION", "location"),
            "budget_per_person": ("BUDGET_AMOUNT_REQUIRED", "integer"),
            "max_distance_km": ("MAX_DISTANCE_REQUIRES_NUMBER", "number"),
            "total_distance_km": ("TOTAL_DISTANCE_REQUIRES_NUMBER", "number"),
        }
        default_code, default_expected = codes.get(field, ("PATCH_FIELD_REQUIRES_INPUT", "text"))
        return ClarificationIssue(
            field=field,
            code=code or default_code,
            reason="explicit_patch_value_could_not_be_normalized",
            expected_value_type=expected or default_expected,
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

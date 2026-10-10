"""Project existing domain findings into a small safe recovery reason."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.domain.catalog import ConstraintViolation
from app.domain.constraints import ConstraintSource, PlanRequest
from app.domain.planning import ConstraintConflict, SkeletonSearchTrace
from app.domain.recovery import RecoveryDiagnostics, RecoveryKind, RecoveryReason, RecoveryStage


_HARD_CONFLICT_CODES = frozenset(
        {
            "DEPARTURE_NOT_BEFORE_RETURN_BY",
            "DEPARTURE_OUTSIDE_TIME_WINDOW",
            "INVALID_TIME_WINDOW_ORDER",
            "STALE_REQUEST_REVISION",
        "UNKNOWN_REQUEST_FIELD",
        "FIELD_PROVENANCE_UNSUPPORTED",
        "REQUEST_FIELD_NOT_CLEARABLE",
        "REQUEST_LIST_OPERATION_UNSUPPORTED",
        "INVALID_REQUEST_PATCH",
        "STRICT_BUDGET_AMOUNT_REQUIRED",
        "STOP_COUNT_BELOW_REQUIRED_ROLES",
        "DUPLICATE_MEAL_ROLE",
    }
)
_MODIFICATION_CODES = frozenset(
    {
        "LOCKED_STOP_UNAVAILABLE",
        "NO_REPLACEMENT_CANDIDATES",
        "NO_CLOSER_REPLACEMENT",
        "NO_REPLACEMENT_PLAN",
        "NO_VALID_REPLACEMENT",
    }
)
_TIME_REJECTION_KEYS = frozenset(
    {"time_window", "duration_minutes", "departure_at", "return_by", "meal_window"}
)
_ROUTE_REJECTION_KEYS = frozenset(
    {"route_distance", "max_distance_km", "total_distance_km", "route_time"}
)
_ROUTE_FINDING_FIELDS = _ROUTE_REJECTION_KEYS | {"opening_hours"}


class RecoveryReasonAdapter:
    """Pure, allowlisted projection; it never replaces the source finding."""

    def from_conflict(
        self,
        conflict: ConstraintConflict,
        request: PlanRequest,
        *,
        stage: RecoveryStage | None = None,
        plan_version_id: str | None = None,
        search_traces: Sequence[SkeletonSearchTrace] = (),
        catalog_violations: Sequence[ConstraintViolation] = (),
        route_failure_field_sets: Sequence[Iterable[str]] = (),
    ) -> RecoveryReason:
        fields = tuple(dict.fromkeys(field for field in conflict.fields if field))
        route_failure_sets = tuple(frozenset(items) for items in route_failure_field_sets)
        resolved_stage = stage or self._stage(
            conflict,
            fields,
            search_traces,
            catalog_violations,
            route_failure_sets,
        )
        kind = (
            RecoveryKind.HARD_CONFLICT
            if conflict.code in _HARD_CONFLICT_CODES
            else RecoveryKind.MODIFICATION_FAILED
            if resolved_stage == RecoveryStage.MODIFICATION
            or conflict.code in _MODIFICATION_CODES
            else RecoveryKind.NO_FEASIBLE_PLAN
        )
        return RecoveryReason(
            code=conflict.code,
            kind=kind,
            stage=resolved_stage,
            fields=fields,
            request_revision=request.revision,
            plan_version_id=plan_version_id,
            public_summary=conflict.message,
            diagnostics=self._diagnostics(
                request,
                fields,
                search_traces,
                catalog_violations,
                route_failure_sets,
            ),
        )

    @classmethod
    def _stage(
        cls,
        conflict: ConstraintConflict,
        fields: tuple[str, ...],
        traces: Sequence[SkeletonSearchTrace],
        violations: Sequence[ConstraintViolation],
        route_failure_sets: Sequence[frozenset[str]],
    ) -> RecoveryStage:
        if conflict.code in _HARD_CONFLICT_CODES:
            return RecoveryStage.CONSTRAINT_COMPILATION
        if conflict.code in _MODIFICATION_CODES:
            return RecoveryStage.MODIFICATION
        if conflict.code in {
            "UNSUPPORTED_PLAN_STRUCTURE",
            "NO_PLAN_SPEC",
        }:
            return RecoveryStage.PLAN_STRUCTURE
        if conflict.code in {
            "NO_CANDIDATES_FOR_REQUIRED_ROLES",
            "NO_CANDIDATES_AFTER_HARD_FILTER",
            "NO_CANDIDATES_WITHIN_SEARCH_RADIUS",
            "NO_CANDIDATES_WITHIN_OPENING_HOURS",
            "NO_CANDIDATES_FOR_WEATHER",
            "NO_CANDIDATES_FOR_PARTY",
        }:
            return RecoveryStage.RETRIEVAL
        if conflict.code == "NO_PLAN_AFTER_AVAILABILITY":
            return RecoveryStage.AVAILABILITY
        if conflict.code in {
            "NO_PLAN_AFTER_ROUTE_VERIFICATION",
            "NO_PLAN_AFTER_LOCAL_REPLAN",
            "NO_PLAN_WITHIN_DISTANCE",
        }:
            return RecoveryStage.ROUTE
        if conflict.code == "NO_SCHEDULE_WITHIN_TIME_WINDOW":
            return RecoveryStage.SCHEDULING
        if conflict.code == "NO_PLAN_WITHIN_STRICT_BUDGET":
            return (
                RecoveryStage.VERIFICATION
                if route_failure_sets
                else RecoveryStage.RETRIEVAL
            )
        if route_failure_sets and all(
            "availability" in finding for finding in route_failure_sets
        ):
            return RecoveryStage.AVAILABILITY
        if route_failure_sets:
            return RecoveryStage.ROUTE
        if fields and set(fields).issubset({"plan_structure", "required_stop_roles"}):
            return RecoveryStage.PLAN_STRUCTURE
        if any(
            any(trace.rejected_by.get(reason, 0) for reason in _TIME_REJECTION_KEYS)
            for trace in traces
        ):
            return RecoveryStage.SCHEDULING
        if any(trace.expansions for trace in traces):
            return RecoveryStage.SCHEDULING
        if violations or traces:
            return RecoveryStage.RETRIEVAL
        return RecoveryStage.RETRIEVAL

    @classmethod
    def _diagnostics(
        cls,
        request: PlanRequest,
        fields: tuple[str, ...],
        traces: Sequence[SkeletonSearchTrace],
        violations: Sequence[ConstraintViolation],
        route_failure_sets: Sequence[frozenset[str]],
    ) -> RecoveryDiagnostics:
        rejected_by: dict[str, int] = {}
        for trace in traces:
            for reason, count in trace.rejected_by.items():
                rejected_by[reason] = rejected_by.get(reason, 0) + count

        available_minutes = None
        bounds = request.planning_window.clock_bounds
        if bounds is not None:
            start_hour, start_minute = (int(part) for part in bounds.start.split(":"))
            end_hour, end_minute = (int(part) for part in bounds.end.split(":"))
            available_minutes = max(
                0,
                (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute),
            )
            if request.duration_minutes is not None:
                available_minutes = min(available_minutes, request.duration_minutes.value)

        sources: dict[str, ConstraintSource] = {}
        window = request.planning_window
        if window.date is not None:
            sources["date"] = window.date.source
        if window.start_at is not None:
            start_key = (
                "departure_at"
                if window.start_kind == "departure"
                else "time_window_start"
            )
            sources[start_key] = window.start_at.source
        if window.end_at is not None:
            end_key = (
                "return_by"
                if window.end_kind == "return_deadline"
                else "time_window_end"
            )
            sources[end_key] = window.end_at.source
        if request.duration_minutes is not None:
            sources["duration_minutes"] = request.duration_minutes.source
        for name in (
            "location",
            "planning_area",
            "party",
            "budget_per_person",
            "max_distance_km",
            "exact_stop_count",
            "required_stop_roles",
            "total_distance_km",
        ):
            value = getattr(request, name)
            if value is not None:
                source_key = "party" if name == "party" else name
                if source_key in {
                    "location",
                    "planning_area",
                    "party",
                    "budget_per_person",
                    "max_distance_km",
                    "exact_stop_count",
                    "required_stop_roles",
                    "total_distance_km",
                }:
                    sources[source_key] = value.source

        requested_count = (
            request.exact_stop_count.value
            if request.exact_stop_count is not None
            else None
        )
        required_roles = (
            request.required_stop_roles.value
            if request.required_stop_roles is not None
            else ()
        )
        route_finding_count = sum(
            bool(finding & _ROUTE_FINDING_FIELDS) for finding in route_failure_sets
        )
        availability_count = sum(
            "availability" in finding for finding in route_failure_sets
        )
        rejected_by_time = sum(
            rejected_by.get(reason, 0) for reason in _TIME_REJECTION_KEYS
        )
        rejected_by_route = sum(
            rejected_by.get(reason, 0) for reason in _ROUTE_REJECTION_KEYS
        )
        return RecoveryDiagnostics(
            current_max_distance_km=(
                request.max_distance_km.value
                if request.max_distance_km is not None
                and "max_distance_km" in fields
                else None
            ),
            requested_stop_count=requested_count,
            required_role_count=len(required_roles) if required_roles else None,
            available_minutes=available_minutes,
            rejected_by_time_window=rejected_by_time,
            rejected_by_route=max(rejected_by_route, route_finding_count),
            rejected_by_availability=availability_count,
            structures_considered=len(traces),
            combinations_expanded=sum(trace.expansions for trace in traces),
            route_candidates=sum(trace.route_candidates for trace in traces),
            route_provider_requests=sum(trace.route_provider_requests for trace in traces),
            route_verification_failures=len(route_failure_sets),
            rejected_by_hard_filter=len(violations),
            constraint_sources=sources,
        )

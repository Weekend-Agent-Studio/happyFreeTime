"""Pure whole-plan feasibility checks applied after route reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.domain.catalog import StopCandidate
from app.domain.constraints import NormalizedConstraints
from app.domain.providers import AvailabilityFact, AvailabilityStatus
from app.domain.planning import Plan, StopRole
from app.services.itinerary_scheduler import (
    DEFAULT_TEMPORAL_POLICY,
    MEAL_ANCHOR_WINDOWS,
)
from app.services.opening_hours import visit_fits_opening_hours


# Backward-compatible alias for callers that imported the old private name.
# The policy itself is now owned by itinerary_scheduler and is shared by the
# timeline builder and this verifier.
_MEAL_ANCHOR_WINDOWS = MEAL_ANCHOR_WINDOWS


@dataclass(frozen=True)
class VerificationFinding:
    """One deterministic plan violation or non-blocking warning."""

    code: str
    field: str
    message: str
    resource_id: str | None = None
    route_leg_index: int | None = None


@dataclass(frozen=True)
class VerificationResult:
    violations: tuple[VerificationFinding, ...]
    warnings: tuple[VerificationFinding, ...] = ()

    @property
    def is_feasible(self) -> bool:
        return not self.violations


class PlanVerifier:
    """Verify facts that only become decidable for a complete routed plan."""

    def verify(
        self,
        plan: Plan,
        constraints: NormalizedConstraints,
        resources: Mapping[str, StopCandidate],
        *,
        availability_facts: tuple[AvailabilityFact, ...] = (),
    ) -> VerificationResult:
        violations: list[VerificationFinding] = []
        plan_end_minutes = _elapsed_minutes(plan.stops[-1].end)
        window_end_minutes = _elapsed_minutes(constraints.time_window.value.end)
        if (
            plan_end_minutes is None
            or window_end_minutes is None
            or plan_end_minutes > window_end_minutes
        ):
            violations.append(
                VerificationFinding(
                    code="plan_ends_after_window",
                    field="time_window",
                    message="路线复核后的行程结束时间晚于可用时间窗。",
                )
            )
        if (
            constraints.duration_minutes
            and plan.total_duration_minutes > constraints.duration_minutes.value
        ):
            violations.append(
                VerificationFinding(
                    code="plan_exceeds_duration",
                    field="duration_minutes",
                    message="路线复核后的总时长超过用户指定时长。",
                )
            )
        if constraints.max_distance_km:
            for index, leg in enumerate(plan.route_legs):
                if leg.distance_km > constraints.max_distance_km.value:
                    violations.append(
                        VerificationFinding(
                            code="route_leg_exceeds_distance",
                            field="max_distance_km",
                            message="至少一个路线分段超过最大距离限制。",
                            route_leg_index=index,
                        )
                    )
        if constraints.total_distance_km:
            total_distance = sum(leg.distance_km for leg in plan.route_legs)
            if total_distance > constraints.total_distance_km.value:
                violations.append(
                    VerificationFinding(
                        code="total_distance_exceeds_limit",
                        field="total_distance_km",
                        message="全程累计距离超过用户限制。",
                    )
                )
        if constraints.return_by:
            return_by_minutes = _elapsed_minutes(constraints.return_by.value)
            return_leg = plan.route_legs[-1] if plan.route_legs else None
            if return_leg is None or return_leg.destination_name != "出发地":
                violations.append(
                    VerificationFinding(
                        code="return_route_missing",
                        field="return_by",
                        message="用户要求最晚到家，但路线中没有以出发地为终点的返程段。",
                    )
                )
            else:
                return_arrival = _elapsed_minutes(return_leg.end)
                if (
                    return_by_minutes is None
                    or return_arrival is None
                    or return_arrival > return_by_minutes
                ):
                    violations.append(
                        VerificationFinding(
                        code="return_after_deadline",
                        field="return_by",
                        message="返程到家时间晚于用户要求的最晚到家时间。",
                        route_leg_index=len(plan.route_legs) - 1,
                        )
                    )
        for stop in plan.stops:
            anchor = DEFAULT_TEMPORAL_POLICY.window_for(stop.role)
            if anchor is not None:
                arrival = _elapsed_minutes(stop.start)
                window_start, window_end = anchor
                if arrival is None or not (
                    window_start <= arrival <= window_end
                ):
                    violations.append(
                        VerificationFinding(
                            code="meal_off_anchor_window",
                            field="meal_window",
                            message=(
                                f"{stop.name} 的到店时间不在"
                                f" {stop.role.value} 的锚点时段内。"
                            ),
                            resource_id=stop.resource_id,
                        )
                    )

        weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[
            constraints.date.value.weekday()
        ]
        for stop in plan.stops:
            resource = resources[stop.resource_id]
            hours = resource.open_hours.get(weekday)
            if not hours:
                continue
            fits = visit_fits_opening_hours(hours, stop.start, stop.end)
            if fits is False:
                violations.append(
                    VerificationFinding(
                        code="visit_outside_opening_hours",
                        field="opening_hours",
                        message=f"{stop.name} 在计划到店和停留时段内并非全程营业。",
                        resource_id=stop.resource_id,
                    )
                )
        warnings: list[VerificationFinding] = []
        facts_by_resource = {fact.resource_id: fact for fact in availability_facts}
        for stop in plan.stops:
            fact = facts_by_resource.get(stop.resource_id)
            if fact is None:
                finding = VerificationFinding(
                    code=(
                        "availability_confirmation_required"
                        if constraints.require_availability_confirmation
                        else "availability_unconfirmed"
                    ),
                    field="availability",
                    message=(
                        f"{stop.name} 没有本轮动态可用性事实，无法确认有位。"
                        if constraints.require_availability_confirmation
                        else f"{stop.name} 的动态可用性本轮未返回事实，建议出发前再次确认。"
                    ),
                    resource_id=stop.resource_id,
                )
                (violations if constraints.require_availability_confirmation else warnings).append(
                    finding
                )
                continue
            if fact.status == AvailabilityStatus.UNAVAILABLE and fact.verified and not fact.stale and not fact.degraded:
                violations.append(
                    VerificationFinding(
                        code="availability_verified_unavailable",
                        field="availability",
                        message=f"{stop.name} 在计划时段已被动态事实确认不可用。",
                        resource_id=stop.resource_id,
                    )
                )
                continue
            confirmed_available = (
                fact.status == AvailabilityStatus.AVAILABLE
                and fact.verified
                and not fact.stale
                and not fact.degraded
            )
            if not confirmed_available:
                finding = VerificationFinding(
                    code=(
                        "availability_confirmation_required"
                        if constraints.require_availability_confirmation
                        else "availability_unconfirmed"
                    ),
                    field="availability",
                    message=(
                        f"{stop.name} 的动态可用性不是可确认的 AVAILABLE，不能确认有位。"
                        if constraints.require_availability_confirmation
                        else f"{stop.name} 的动态可用性尚未得到完整确认。"
                    ),
                    resource_id=stop.resource_id,
                )
                (violations if constraints.require_availability_confirmation else warnings).append(
                    finding
                )
        for index, leg in enumerate(plan.route_legs):
            if leg.degraded:
                warnings.append(
                    VerificationFinding(
                        code="route_fact_degraded",
                        field="route",
                        message="该路线段使用了降级或本地估算事实。",
                        route_leg_index=index,
                    )
                )
        return VerificationResult(violations=tuple(violations), warnings=tuple(warnings))


def _elapsed_minutes(value: str) -> int | None:
    """Parse generated timeline values, whose hour may exceed 23 after overrun."""
    try:
        hour_text, minute_text = value.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError):
        return None
    if hour < 0 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute

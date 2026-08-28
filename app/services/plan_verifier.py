"""Pure whole-plan feasibility checks applied after route reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.domain.catalog import StopCandidate
from app.domain.constraints import NormalizedConstraints
from app.domain.planning import Plan
from app.services.opening_hours import visit_fits_opening_hours


@dataclass(frozen=True)
class VerificationFinding:
    """One deterministic plan violation or non-blocking warning."""

    code: str
    field: str
    message: str
    resource_id: str | None = None


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
        if constraints.max_distance_km and any(
            leg.distance_km > constraints.max_distance_km.value
            for leg in plan.route_legs
        ):
            violations.append(
                VerificationFinding(
                    code="route_leg_exceeds_distance",
                    field="max_distance_km",
                    message="至少一个路线分段超过最大距离限制。",
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
        return VerificationResult(violations=tuple(violations))


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

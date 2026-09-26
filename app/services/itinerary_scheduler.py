"""Deterministic time-axis construction shared by planning and verification.

The planner and verifier must agree on the same temporal vocabulary.  This
module owns the small amount of policy needed to construct a timeline (for
example, waiting for a lunch or dinner anchor); callers still remain
responsible for checking the finished timeline against user constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.domain.constraints import StopRole


MEAL_ANCHOR_WINDOWS: Mapping[StopRole, tuple[int, int]] = MappingProxyType(
    {
        StopRole.LUNCH: (11 * 60, 14 * 60),
        StopRole.DINNER: (17 * 60, 20 * 60 + 30),
    }
)


@dataclass(frozen=True)
class TemporalPolicy:
    """Shared temporal rules used by the scheduler and the verifier."""

    meal_anchor_windows: Mapping[StopRole, tuple[int, int]] = MEAL_ANCHOR_WINDOWS

    def window_for(self, role: StopRole | None) -> tuple[int, int] | None:
        if role is None:
            return None
        return self.meal_anchor_windows.get(role)


DEFAULT_TEMPORAL_POLICY = TemporalPolicy()


@dataclass(frozen=True)
class ScheduledStop:
    """One stop after applying travel, anchor waiting and dwell time."""

    arrival_minutes: int
    start_minutes: int
    end_minutes: int
    wait_minutes: int


@dataclass(frozen=True)
class SchedulingRequest:
    """Inputs shared by estimated and provider-backed timeline construction."""

    start_minutes: int
    roles: tuple[StopRole | None, ...]
    travel_minutes: tuple[int, ...]
    stop_durations: tuple[int, ...]
    return_travel_minutes: int = 0


@dataclass(frozen=True)
class SchedulingResult:
    """A deterministic schedule for an ordered sequence of stops."""

    start_minutes: int
    stops: tuple[ScheduledStop, ...]
    end_minutes: int
    total_wait_minutes: int

    @property
    def elapsed_minutes(self) -> int:
        return self.end_minutes - self.start_minutes


# Older internal callers used this descriptive name; keep it as an alias while
# exposing the task-level SchedulingResult contract.
TimelineSchedule = SchedulingResult


@dataclass(frozen=True)
class SchedulingFailure:
    """Reserved for deterministic scheduling failures before verification."""

    code: str
    field: str
    message: str


class TimelineScheduler:
    """Build a timeline without deciding whether it satisfies hard limits.

    The scheduler may insert deterministic waiting before a meal stop.  It
    never relaxes a user constraint and never decides feasibility; the caller
    must pass its result to ``PlanVerifier``.
    """

    def __init__(self, policy: TemporalPolicy | None = None) -> None:
        self._policy = policy or DEFAULT_TEMPORAL_POLICY

    @property
    def policy(self) -> TemporalPolicy:
        return self._policy

    def schedule_stop(
        self,
        *,
        arrival_minutes: int,
        role: StopRole | None,
        duration_minutes: int,
    ) -> ScheduledStop:
        if arrival_minutes < 0:
            raise ValueError("arrival_minutes must be non-negative")
        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")
        anchor = self._policy.window_for(role)
        start_minutes = max(arrival_minutes, anchor[0]) if anchor else arrival_minutes
        return ScheduledStop(
            arrival_minutes=arrival_minutes,
            start_minutes=start_minutes,
            end_minutes=start_minutes + duration_minutes,
            wait_minutes=start_minutes - arrival_minutes,
        )

    def schedule(
        self,
        *,
        start_minutes: int,
        roles: tuple[StopRole | None, ...],
        travel_minutes: tuple[int, ...],
        stop_durations: tuple[int, ...],
        return_travel_minutes: int = 0,
    ) -> SchedulingResult:
        """Schedule an ordered route using estimated or provider durations."""

        if len(roles) != len(travel_minutes) or len(roles) != len(stop_durations):
            raise ValueError("roles, travel_minutes and stop_durations must align")
        if start_minutes < 0 or return_travel_minutes < 0:
            raise ValueError("timeline minutes must be non-negative")

        current_minutes = start_minutes
        scheduled: list[ScheduledStop] = []
        total_wait = 0
        for role, travel, duration in zip(
            roles,
            travel_minutes,
            stop_durations,
            strict=True,
        ):
            if travel < 0:
                raise ValueError("travel_minutes must be non-negative")
            arrival = current_minutes + travel
            item = self.schedule_stop(
                arrival_minutes=arrival,
                role=role,
                duration_minutes=duration,
            )
            scheduled.append(item)
            total_wait += item.wait_minutes
            current_minutes = item.end_minutes

        return SchedulingResult(
            start_minutes=start_minutes,
            stops=tuple(scheduled),
            end_minutes=current_minutes + return_travel_minutes,
            total_wait_minutes=total_wait,
        )

    def schedule_request(self, request: SchedulingRequest) -> SchedulingResult:
        """Convenience seam for callers that want an explicit request object."""

        return self.schedule(
            start_minutes=request.start_minutes,
            roles=request.roles,
            travel_minutes=request.travel_minutes,
            stop_durations=request.stop_durations,
            return_travel_minutes=request.return_travel_minutes,
        )

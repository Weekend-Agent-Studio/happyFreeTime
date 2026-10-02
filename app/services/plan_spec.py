"""Bounded executable structures accepted by the deterministic planner."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.constraints import StopRole, TimeScope
from app.domain.planning import PlanPace


@dataclass(frozen=True)
class PlanSlot:
    """One ordered role slot in a bounded plan specification."""

    role: StopRole
    required: bool = True


@dataclass(frozen=True)
class RolePrecedence:
    """A user- or intent-derived ordering relation between roles."""

    before: StopRole
    after: StopRole


@dataclass(frozen=True)
class PlanSpec:
    """A validated role sequence and its deterministic planning constraints.

    A spec contains no concrete resources. Optional proposal slots are expanded
    by the compiler into a bounded set of concrete specs before search starts.
    """

    spec_id: str
    slots: tuple[PlanSlot, ...]
    precedence: tuple[RolePrecedence, ...] = ()
    min_stops: int = 1
    max_stops: int = 4
    pace: PlanPace = PlanPace.BALANCED
    coverage: TimeScope | None = None

    def __post_init__(self) -> None:
        if not self.spec_id:
            raise ValueError("spec_id is required")
        if not 1 <= len(self.slots) <= 4:
            raise ValueError("a PlanSpec must contain between 1 and 4 slots")
        if not 1 <= self.min_stops <= self.max_stops <= 4:
            raise ValueError("stop bounds must satisfy 1 <= min <= max <= 4")
        # Intent precedence may mention optional roles that are not present in
        # this particular closed template.  Eligibility checks apply the
        # relation only when both roles occur in the selected spec.

    @property
    def roles(self) -> tuple[StopRole, ...]:
        return tuple(slot.role for slot in self.slots)

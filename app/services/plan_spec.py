"""Bounded internal planning structure specifications.

The public planning models still expose ``Plan.skeleton_id`` for compatibility.
Inside the planner, however, a concrete skeleton is represented as a small
compiled specification.  This keeps role slots, ordering, stop-count bounds
and soft pacing in one place without opening an arbitrary LLM-defined grammar.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.constraints import StopRole, TimeScope
from app.domain.planning import PlanPace, PlanSkeleton


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
class CompiledPlanSpec:
    """A validated, closed-world structure ready for deterministic planning.

    ``slots`` is deliberately limited to four positions.  The compiler may
    choose among existing templates, but it cannot invent a new role or let a
    model supply resource identities.  ``coverage`` is an optional soft
    planning hint (for example ``ALL_DAY``), never a replacement for the
    normalized time window and its hard verifier.
    """

    skeleton_id: str
    slots: tuple[PlanSlot, ...]
    precedence: tuple[RolePrecedence, ...] = ()
    min_stops: int = 1
    max_stops: int = 4
    pace: PlanPace = PlanPace.BALANCED
    coverage: TimeScope | None = None

    def __post_init__(self) -> None:
        if not self.skeleton_id:
            raise ValueError("skeleton_id is required")
        if not 1 <= len(self.slots) <= 4:
            raise ValueError("a compiled plan must contain between 1 and 4 slots")
        if not 1 <= self.min_stops <= self.max_stops <= 4:
            raise ValueError("stop bounds must satisfy 1 <= min <= max <= 4")
        # Intent precedence may mention optional roles that are not present in
        # this particular closed template.  Eligibility checks apply the
        # relation only when both roles occur in the selected spec.

    @property
    def roles(self) -> tuple[StopRole, ...]:
        return tuple(slot.role for slot in self.slots)

    @property
    def skeleton(self) -> PlanSkeleton:
        """Compatibility view consumed by existing Plan/API code."""

        return PlanSkeleton(skeleton_id=self.skeleton_id, roles=self.roles)

    @classmethod
    def from_skeleton(
        cls,
        skeleton: PlanSkeleton,
        *,
        precedence: tuple[RolePrecedence, ...] = (),
        min_stops: int | None = None,
        max_stops: int | None = None,
        pace: PlanPace = PlanPace.BALANCED,
        coverage: TimeScope | None = None,
    ) -> "CompiledPlanSpec":
        return cls(
            skeleton_id=skeleton.skeleton_id,
            slots=tuple(PlanSlot(role=role) for role in skeleton.roles),
            precedence=precedence,
            min_stops=len(skeleton.roles) if min_stops is None else min_stops,
            max_stops=len(skeleton.roles) if max_stops is None else max_stops,
            pace=pace,
            coverage=coverage,
        )


# Short name for callers that do not need to emphasize compilation.
PlanSpec = CompiledPlanSpec


def coerce_plan_spec(
    value: CompiledPlanSpec | PlanSkeleton,
    *,
    pace: PlanPace = PlanPace.BALANCED,
) -> CompiledPlanSpec:
    """Keep internal helpers compatible with legacy skeleton-based tests."""

    if isinstance(value, CompiledPlanSpec):
        return value
    return CompiledPlanSpec.from_skeleton(value, pace=pace)

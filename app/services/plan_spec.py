"""Concrete ordered role sequences consumed by deterministic planning."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.constraints import StopRole


@dataclass(frozen=True)
class PlanSpec:
    """An executable, bounded sequence of roles with no optional slots.

    Optionality belongs to the wire proposal. The compiler expands it into
    concrete role sequences before a PlanSpec reaches retrieval or search.
    """

    spec_id: str
    roles: tuple[StopRole, ...]

    def __post_init__(self) -> None:
        if not self.spec_id:
            raise ValueError("spec_id is required")
        if not isinstance(self.roles, tuple):
            raise TypeError("PlanSpec.roles must be an immutable tuple")
        if not 1 <= len(self.roles) <= 4:
            raise ValueError("a PlanSpec must contain between 1 and 4 roles")
        if any(not isinstance(role, StopRole) for role in self.roles):
            raise TypeError("PlanSpec.roles must contain StopRole values")

"""Compile the bounded PlanningIntent structure proposal.

The model is allowed to describe an ordered role sequence, but it is not
allowed to decide feasibility or external facts.  This module is the narrow
deep seam between that wire proposal and the deterministic planner.  It does
not call a provider and it deliberately does not know about POIs or routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Literal, Sequence

from app.domain.constraints import NormalizedConstraints, StopRole
from app.domain.planning import (
    PlanPace,
    PlanSkeleton,
    PlanStructureProposal,
    PlanningIntent,
)
from app.services.plan_spec import CompiledPlanSpec, PlanSlot, RolePrecedence


MEAL_ROLES = frozenset({StopRole.MEAL, StopRole.LUNCH, StopRole.DINNER})


@dataclass(frozen=True)
class CompiledPlanChoices:
    """Preferred model structures and deterministic Rule fallback structures."""

    preferred_specs: tuple[CompiledPlanSpec, ...] = ()
    fallback_specs: tuple[CompiledPlanSpec, ...] = ()
    proposal_status: Literal["not_used", "compiled", "rejected"] = "not_used"
    diagnostic_code: str | None = None


class PlanSpecCompiler:
    """Compile a V2 proposal without making the registry a legality gate."""

    def __init__(self, registry: Sequence[PlanSkeleton] | None = None) -> None:
        if registry is None:
            # Local import avoids a module cycle: planning_intent owns the
            # compatibility registry, while this compiler is consumed by the
            # planner and the provider.
            from app.services.planning_intent import ALL_PLAN_SKELETONS

            registry = ALL_PLAN_SKELETONS
        self._registry = tuple(registry)

    def compile(
        self,
        constraints: NormalizedConstraints,
        rule_baseline: PlanningIntent,
        proposal: PlanStructureProposal | None,
    ) -> CompiledPlanChoices:
        fallback = self._rule_specs(constraints, rule_baseline)
        if proposal is None:
            return CompiledPlanChoices(
                preferred_specs=fallback,
                fallback_specs=(),
                proposal_status="not_used",
            )
        try:
            self._validate_proposal(proposal, constraints, rule_baseline)
            preferred = self._proposal_specs(proposal, constraints)
        except ValueError as error:
            return CompiledPlanChoices(
                preferred_specs=(),
                fallback_specs=fallback,
                proposal_status="rejected",
                diagnostic_code=_safe_code(error),
            )
        if not preferred:
            return CompiledPlanChoices(
                preferred_specs=(),
                fallback_specs=fallback,
                proposal_status="rejected",
                diagnostic_code="no_compiled_plan_spec",
            )
        return CompiledPlanChoices(
            preferred_specs=preferred,
            fallback_specs=fallback,
            proposal_status="compiled",
        )

    def _validate_proposal(
        self,
        proposal: PlanStructureProposal,
        constraints: NormalizedConstraints,
        baseline: PlanningIntent,
    ) -> None:
        slots = proposal.slots
        if not 1 <= len(slots) <= 4:
            raise ValueError("slot_count_out_of_bounds")
        roles = tuple(slot.role for slot in slots)
        if roles.count(StopRole.LUNCH) > 1 or roles.count(StopRole.DINNER) > 1:
            raise ValueError("duplicate_meal_role")
        lunch = _first_index(roles, StopRole.LUNCH)
        dinner = _first_index(roles, StopRole.DINNER)
        if lunch is not None and dinner is not None and lunch >= dinner:
            raise ValueError("lunch_before_dinner_required")

        required_roles = _explicit_roles(constraints)
        if required_roles and not _contains_compatible_subsequence(roles, required_roles):
            raise ValueError("explicit_roles_not_preserved")
        exact_count = (
            constraints.exact_stop_count.value
            if constraints.exact_stop_count is not None
            else None
        )
        if exact_count is not None:
            possible_counts = {
                sum(1 for slot in slots if slot.inclusion == "core")
                + optional_count
                for optional_count in range(
                    sum(1 for slot in slots if slot.inclusion == "optional") + 1
                )
            }
            if exact_count not in possible_counts:
                raise ValueError("explicit_stop_count_not_preserved")

        known_evidence = {item.evidence_id for item in baseline.semantic_request.evidence}
        if not set(proposal.evidence_refs).issubset(known_evidence):
            raise ValueError("proposal_evidence_ungrounded")
        slot_roles = set(roles)
        for raw_role, query in proposal.role_queries.items():
            try:
                role = StopRole(raw_role)
            except ValueError as error:
                raise ValueError("role_query_unknown_role") from error
            if role not in slot_roles:
                raise ValueError("role_query_role_not_in_slots")
            if not set(query.evidence_refs).issubset(known_evidence):
                raise ValueError("role_query_ungrounded")

    def _proposal_specs(
        self,
        proposal: PlanStructureProposal,
        constraints: NormalizedConstraints,
    ) -> tuple[CompiledPlanSpec, ...]:
        slots = proposal.slots
        optional_indexes = tuple(
            index for index, slot in enumerate(slots) if slot.inclusion == "optional"
        )
        exact_count = (
            constraints.exact_stop_count.value
            if constraints.exact_stop_count is not None
            else None
        )
        variants: list[CompiledPlanSpec] = []
        for optional_count in range(len(optional_indexes) + 1):
            for selected in combinations(optional_indexes, optional_count):
                selected_set = set(selected)
                concrete_slots = tuple(
                    PlanSlot(role=slot.role, required=True)
                    for index, slot in enumerate(slots)
                    if slot.inclusion == "core" or index in selected_set
                )
                if not concrete_slots:
                    continue
                if exact_count is not None and len(concrete_slots) != exact_count:
                    continue
                roles = tuple(slot.role for slot in concrete_slots)
                variants.append(
                    CompiledPlanSpec(
                        skeleton_id=_stable_skeleton_id(roles),
                        slots=concrete_slots,
                        precedence=_adjacent_precedence(roles),
                        min_stops=len(concrete_slots),
                        max_stops=len(concrete_slots),
                        pace=proposal.pace,
                        coverage=(
                            constraints.time_scope.value
                            if constraints.time_scope is not None
                            else None
                        ),
                    )
                )
        # Prefer the richest variant first; the planner may still rank all
        # feasible variants.  Stable IDs and role tuples remove duplicates.
        deduped: dict[tuple[str, tuple[StopRole, ...]], CompiledPlanSpec] = {}
        for spec in variants:
            deduped[(spec.skeleton_id, spec.roles)] = spec
        return tuple(
            sorted(
                deduped.values(),
                key=lambda spec: (-len(spec.roles), spec.skeleton_id),
            )
        )

    def _rule_specs(
        self,
        constraints: NormalizedConstraints,
        baseline: PlanningIntent,
    ) -> tuple[CompiledPlanSpec, ...]:
        """Build the old Rule candidate set without consulting the LLM shape."""

        maximum_minutes = _available_minutes(constraints)
        allowed = set(baseline.required_roles) | set(baseline.optional_roles)
        output: list[CompiledPlanSpec] = []
        for skeleton in self._registry:
            roles = skeleton.roles
            if not baseline.minimum_stops <= len(roles) <= baseline.maximum_stops:
                continue
            if not set(baseline.required_roles).issubset(roles):
                continue
            if not set(roles).issubset(allowed):
                continue
            if baseline.slots and not _contains_compatible_subsequence(
                roles,
                tuple(slot.role for slot in baseline.slots if slot.required),
            ):
                continue
            if not _precedence_holds(roles, baseline.precedence):
                continue
            minimum_capacity = {
                "lunch-activity-dinner-v1": 6 * 60,
                "activity-break-dinner-v1": 5 * 60,
                "activity-lunch-activity-dinner-v1": 8 * 60,
            }.get(skeleton.skeleton_id, len(roles) * 30)
            if minimum_capacity > maximum_minutes:
                continue
            output.append(
                CompiledPlanSpec.from_skeleton(
                    skeleton,
                    precedence=tuple(
                        RolePrecedence(before=before, after=after)
                        for before, after in baseline.precedence
                    ),
                    min_stops=len(roles),
                    max_stops=len(roles),
                    pace=baseline.pace,
                    coverage=(
                        constraints.time_scope.value
                        if constraints.time_scope is not None
                        else baseline.coverage
                    ),
                )
            )
        return tuple(output)


def _explicit_roles(constraints: NormalizedConstraints) -> tuple[StopRole, ...]:
    if constraints.required_stop_roles is None:
        return ()
    return tuple(constraints.required_stop_roles.value)


def _first_index(roles: tuple[StopRole, ...], role: StopRole) -> int | None:
    try:
        return roles.index(role)
    except ValueError:
        return None


def _contains_compatible_subsequence(
    actual: tuple[StopRole, ...],
    required: tuple[StopRole, ...],
) -> bool:
    cursor = 0
    for role in actual:
        if cursor < len(required) and roles_compatible(required[cursor], role):
            cursor += 1
    return cursor == len(required)


def roles_compatible(required: StopRole, actual: StopRole) -> bool:
    if required == actual:
        return True
    return (
        actual == StopRole.MEAL and required in {StopRole.LUNCH, StopRole.DINNER}
    ) or (
        required == StopRole.MEAL and actual in {StopRole.LUNCH, StopRole.DINNER}
    )


def _precedence_holds(
    roles: tuple[StopRole, ...],
    precedence: tuple[tuple[StopRole, StopRole], ...],
) -> bool:
    for before, after in precedence:
        if before in roles and after in roles and roles.index(before) >= roles.index(after):
            return False
    return True


def _adjacent_precedence(roles: tuple[StopRole, ...]) -> tuple[RolePrecedence, ...]:
    return tuple(
        RolePrecedence(before=left, after=right)
        for left, right in zip(roles, roles[1:])
        if left != right
    )


def _stable_skeleton_id(roles: tuple[StopRole, ...]) -> str:
    known = {
        (StopRole.ACTIVITY, StopRole.MEAL): "activity-meal-v1",
        (StopRole.ACTIVITY,): "activity-only-v1",
        (StopRole.DINNER,): "dinner-only-v1",
        (StopRole.LUNCH,): "lunch-only-v1",
        (StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER): "lunch-activity-dinner-v1",
        (StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER): "activity-break-dinner-v1",
        (
            StopRole.ACTIVITY,
            StopRole.LUNCH,
            StopRole.ACTIVITY,
            StopRole.DINNER,
        ): "activity-lunch-activity-dinner-v1",
    }
    if roles in known:
        return known[roles]
    return "llm-" + "-".join(role.value for role in roles) + "-v2"


def _available_minutes(constraints: NormalizedConstraints) -> int:
    if constraints.time_window is None:
        return 24 * 60
    window = constraints.time_window.value
    start_hour, start_minute = (int(part) for part in window.start.split(":"))
    end_hour, end_minute = (int(part) for part in window.end.split(":"))
    return max(0, (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute))


def _safe_code(error: Exception) -> str:
    value = str(error).strip()
    return value if value.replace("_", "").isalnum() else "proposal_rejected"

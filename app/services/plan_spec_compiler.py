"""Compile structural proposals into executable, bounded PlanSpecs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations, product
from typing import Literal

from app.domain.constraints import PlanRequest, StopRole
from app.domain.planning import (
    ConstraintConflict,
    PlanStructureProposal,
    PlanPace,
    PlanningIntent,
)
from app.services.plan_spec import PlanSpec


@dataclass(frozen=True)
class PlanSpecChoices:
    """Preferred model structures and deterministic Rule fallback structures."""

    preferred_specs: tuple[PlanSpec, ...] = ()
    fallback_specs: tuple[PlanSpec, ...] = ()
    proposal_status: Literal["not_used", "compiled", "rejected"] = "not_used"
    diagnostic_code: str | None = None
    conflict: ConstraintConflict | None = None
    explicit_structure: bool = False


_DYNAMIC_ROLE_POOL = (
    StopRole.ACTIVITY,
    StopRole.BREAK,
    StopRole.LUNCH,
    StopRole.DINNER,
    StopRole.MEAL,
)
_ROLE_MINIMUM_MINUTES = {
    StopRole.ACTIVITY: 30,
    StopRole.BREAK: 15,
    StopRole.LUNCH: 60,
    StopRole.DINNER: 60,
    StopRole.MEAL: 60,
}
_MAX_DYNAMIC_COMPLETIONS = 24


def _build_rule_plan_specs(
    constraints: PlanRequest,
    semantics: PlanningIntent,
    *,
    include_all_shapes: bool = False,
) -> tuple[PlanSpec, ...]:
    """Build the Rule search space directly as PlanSpecs.

    The role patterns below are the product's current deterministic policy,
    created locally in executable form. There is no separately addressable
    Skeleton registry or conversion step.
    """

    available_minutes = _available_minutes(constraints)
    exact_count = (
        constraints.exact_stop_count.value
        if constraints.exact_stop_count is not None
        else None
    )
    window = constraints.planning_window.clock_bounds
    if window is None:
        return ()
    start_minutes = _clock_minutes(window.start)
    end_minutes = _clock_minutes(window.end)
    includes_lunch = start_minutes <= 13 * 60 and end_minutes >= 12 * 60
    includes_dinner = start_minutes <= 19 * 60 and end_minutes >= 18 * 60
    includes_break = start_minutes <= 16 * 60 and end_minutes >= 18 * 60
    allowed_roles = {StopRole.ACTIVITY, StopRole.MEAL}
    if includes_lunch:
        allowed_roles.add(StopRole.LUNCH)
    if includes_break:
        allowed_roles.add(StopRole.BREAK)
    if includes_dinner:
        allowed_roles.add(StopRole.DINNER)

    explicit_roles = (
        constraints.required_stop_roles.value
        if constraints.required_stop_roles is not None
        else ()
    )
    required_roles = explicit_roles or (StopRole.ACTIVITY,)
    minimum_stops = 1 if exact_count == 1 and explicit_roles else 2
    maximum_stops = (
        exact_count
        if exact_count is not None
        else 2 if semantics.pace == PlanPace.RELAXED else 4
    )

    shapes = (
        ("activity-meal-v1", (StopRole.ACTIVITY, StopRole.MEAL)),
        ("activity-only-v1", (StopRole.ACTIVITY,)),
        ("dinner-only-v1", (StopRole.DINNER,)),
        ("lunch-only-v1", (StopRole.LUNCH,)),
        (
            "lunch-activity-dinner-v1",
            (StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
        ),
        (
            "activity-break-dinner-v1",
            (StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER),
        ),
        (
            "activity-lunch-activity-dinner-v1",
            (
                StopRole.ACTIVITY,
                StopRole.LUNCH,
                StopRole.ACTIVITY,
                StopRole.DINNER,
            ),
        ),
    )
    minimum_capacity_by_id = {
        "lunch-activity-dinner-v1": 6 * 60,
        "activity-break-dinner-v1": 5 * 60,
        "activity-lunch-activity-dinner-v1": 8 * 60,
    }

    output: list[PlanSpec] = []
    for spec_id, roles in shapes:
        role_set = set(roles)
        if exact_count is not None and len(roles) != exact_count:
            continue
        if not include_all_shapes:
            if not minimum_stops <= len(roles) <= maximum_stops:
                continue
            if not set(required_roles).issubset(role_set):
                continue
            if not role_set.issubset(allowed_roles):
                continue
            if not _contains_compatible_subsequence(roles, required_roles):
                continue
        if minimum_capacity_by_id.get(spec_id, len(roles) * 30) > available_minutes:
            continue
        output.append(PlanSpec(spec_id=spec_id, roles=roles))
    return tuple(output)


class PlanSpecCompiler:
    """Validate proposal semantics and compile a bounded set of PlanSpecs."""

    def compile_explicit_structure(
        self,
        constraints: PlanRequest,
        rule_baseline: PlanningIntent,
    ) -> PlanSpecChoices | None:
        """Preflight only complete or contradictory structure requests.

        A count with fewer role requirements is intentionally *not* compiled
        against the deterministic shapes here.  It is a partial constraint:
        the model may complete it, and the deterministic compiler can produce
        a bounded completion when no model proposal is available.
        """

        exact_stop_count = (
            constraints.exact_stop_count.value
            if constraints.exact_stop_count is not None
            else None
        )
        required_roles = _explicit_roles(constraints)
        if exact_stop_count is None and not required_roles:
            return None

        conflict = _explicit_structure_conflict(
            constraints,
            exact_stop_count=exact_stop_count,
            required_roles=required_roles,
        )
        if conflict is not None:
            return PlanSpecChoices(
                conflict=conflict,
                explicit_structure=True,
            )

        # ``three stations, including dinner`` (and even just ``three
        # stations``) leaves roles for the structure proposal/compiler to
        # complete.  Matching it to a registered shape here would make the
        # old registry an accidental legality gate again.
        if exact_stop_count is not None and len(required_roles) < exact_stop_count:
            return None

        candidates = _build_rule_plan_specs(
            constraints,
            rule_baseline,
            include_all_shapes=True,
        )
        matches = _explicit_matches(
            exact_stop_count=exact_stop_count,
            required_roles=required_roles,
            candidates=candidates,
        )
        if matches:
            return PlanSpecChoices(
                preferred_specs=matches,
                explicit_structure=True,
            )

        # A complete explicit sequence is valid even when it is not one of
        # the product's current Rule shapes.  The static shapes are a fallback
        # policy, not the domain's complete vocabulary.
        if required_roles and exact_stop_count == len(required_roles):
            if not _capacity_is_feasible(required_roles, constraints):
                return PlanSpecChoices(
                    conflict=_capacity_conflict(constraints),
                    explicit_structure=True,
                )
            roles = tuple(required_roles)
            return PlanSpecChoices(
                preferred_specs=(
                    PlanSpec(
                        spec_id=_stable_plan_spec_id(roles),
                        roles=roles,
                    ),
                ),
                explicit_structure=True,
            )

        # Without an explicit count, an otherwise valid unregistered ordered
        # role request is still a usable concrete sequence.  Do not turn the
        # Rule shape table into a hidden legality registry.
        if required_roles and exact_stop_count is None:
            if not _capacity_is_feasible(required_roles, constraints):
                return PlanSpecChoices(
                    conflict=_capacity_conflict(constraints),
                    explicit_structure=True,
                )
            roles = tuple(required_roles)
            return PlanSpecChoices(
                preferred_specs=(
                    PlanSpec(
                        spec_id=_stable_plan_spec_id(roles),
                        roles=roles,
                    ),
                ),
                explicit_structure=True,
            )

        fields = []
        if exact_stop_count is not None:
            fields.append("exact_stop_count")
        if required_roles:
            fields.append("required_stop_roles")
        fields.append("plan_structure")
        return PlanSpecChoices(
            conflict=ConstraintConflict(
                code="UNSUPPORTED_PLAN_STRUCTURE",
                message="当前请求指定的站点数量或角色组合无法编译为可执行结构。",
                fields=fields,
                relaxation_options=[
                    "调整站点数量或角色组合",
                    "移除明确的结构限制，交给系统选择默认骨架",
                ],
            ),
            explicit_structure=True,
        )

    def compile(
        self,
        constraints: PlanRequest,
        rule_baseline: PlanningIntent,
        proposal: PlanStructureProposal | None,
    ) -> PlanSpecChoices:
        explicit = self.compile_explicit_structure(constraints, rule_baseline)
        if explicit is not None:
            return explicit
        partial = _is_partial_structure(constraints)
        fallback = _build_rule_plan_specs(constraints, rule_baseline)
        if partial:
            # Include deterministic completions for the no-model/provider
            # fallback path.  Known Rule shapes are still useful, but they no
            # longer define which partial structures are legal.
            fallback = _merge_specs(
                _build_dynamic_completion_specs(constraints),
                fallback,
            )
        if proposal is None:
            if not fallback and partial:
                return PlanSpecChoices(
                    proposal_status="not_used",
                    diagnostic_code="structure_capacity_insufficient",
                    conflict=_capacity_conflict(constraints),
                )
            return PlanSpecChoices(
                preferred_specs=fallback,
                fallback_specs=(),
                proposal_status="not_used",
            )
        try:
            self._validate_proposal(proposal, constraints, rule_baseline)
            preferred = self._proposal_specs(proposal, constraints)
        except ValueError as error:
            return PlanSpecChoices(
                preferred_specs=(),
                fallback_specs=fallback,
                proposal_status="rejected",
                diagnostic_code=_safe_code(error),
            )
        if not preferred:
            capacity_issue = partial and not fallback
            return PlanSpecChoices(
                preferred_specs=(),
                fallback_specs=fallback,
                proposal_status="rejected",
                diagnostic_code=(
                    "structure_capacity_insufficient"
                    if partial
                    else "no_compiled_plan_spec"
                ),
                conflict=_capacity_conflict(constraints) if capacity_issue else None,
            )
        return PlanSpecChoices(
            preferred_specs=preferred,
            fallback_specs=fallback,
            proposal_status="compiled",
        )

    def _validate_proposal(
        self,
        proposal: PlanStructureProposal,
        constraints: PlanRequest,
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

        known_evidence = {item.evidence_id for item in baseline.semantic_request.evidence}
        seen_objectives: set[tuple[object, object | None]] = set()
        for objective in proposal.objectives:
            if not objective.evidence_refs or not set(objective.evidence_refs).issubset(
                known_evidence
            ):
                raise ValueError("objective_ungrounded")
            if objective.target_role is not None and objective.target_role not in roles:
                raise ValueError("objective_role_not_in_slots")
            objective_key = (objective.kind, objective.target_role)
            if objective_key in seen_objectives:
                raise ValueError("duplicate_objective")
            seen_objectives.add(objective_key)

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

        # A proposal may satisfy the role/count contract but still ask for a
        # structure whose optimistic lower bound cannot fit the request.  This
        # is a compiler rejection, not a route/verifier rejection; the caller
        # can then use the deterministic fallback completion.
        core_roles = tuple(
            slot.role for slot in slots if slot.inclusion == "core"
        )
        if not _capacity_is_feasible(core_roles, constraints):
            raise ValueError("structure_capacity_insufficient")

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
        constraints: PlanRequest,
    ) -> tuple[PlanSpec, ...]:
        slots = proposal.slots
        optional_indexes = tuple(
            index for index, slot in enumerate(slots) if slot.inclusion == "optional"
        )
        required_roles = _explicit_roles(constraints)
        exact_count = (
            constraints.exact_stop_count.value
            if constraints.exact_stop_count is not None
            else None
        )
        variants: list[PlanSpec] = []
        for optional_count in range(len(optional_indexes) + 1):
            for selected in combinations(optional_indexes, optional_count):
                selected_set = set(selected)
                concrete_roles = tuple(
                    slot.role
                    for index, slot in enumerate(slots)
                    if slot.inclusion == "core" or index in selected_set
                )
                if not concrete_roles:
                    continue
                if exact_count is not None and len(concrete_roles) != exact_count:
                    continue
                assignment = _required_role_assignment(required_roles, concrete_roles)
                if assignment is None:
                    continue
                if required_roles:
                    bound_roles = list(concrete_roles)
                    for required, index in zip(
                        required_roles,
                        assignment[1],
                        strict=True,
                    ):
                        if bound_roles[index] == StopRole.MEAL and required in {
                            StopRole.LUNCH,
                            StopRole.DINNER,
                        }:
                            bound_roles[index] = required
                    concrete_roles = tuple(bound_roles)
                if (
                    concrete_roles.count(StopRole.LUNCH) > 1
                    or concrete_roles.count(StopRole.DINNER) > 1
                ):
                    continue
                lunch = _first_index(concrete_roles, StopRole.LUNCH)
                dinner = _first_index(concrete_roles, StopRole.DINNER)
                if lunch is not None and dinner is not None and lunch >= dinner:
                    continue
                if not _capacity_is_feasible(concrete_roles, constraints):
                    continue
                variants.append(
                    PlanSpec(
                        spec_id=_stable_plan_spec_id(concrete_roles),
                        roles=concrete_roles,
                    )
                )
        deduped: dict[tuple[str, tuple[StopRole, ...]], PlanSpec] = {}
        for spec in variants:
            deduped[(spec.spec_id, spec.roles)] = spec
        return tuple(
            sorted(
                deduped.values(),
                key=lambda spec: (-len(spec.roles), spec.spec_id),
            )
        )


def _explicit_structure_conflict(
    constraints: PlanRequest,
    *,
    exact_stop_count: int | None,
    required_roles: tuple[StopRole, ...],
) -> ConstraintConflict | None:
    """Return only contradictions that no completion can repair."""

    fields: list[str] = []
    if exact_stop_count is not None:
        fields.append("exact_stop_count")
    if required_roles:
        fields.append("required_stop_roles")
    fields.append("plan_structure")

    if exact_stop_count is not None and len(required_roles) > exact_stop_count:
        return ConstraintConflict(
            code="UNSUPPORTED_PLAN_STRUCTURE",
            message="必需站点数量超过了用户指定的总站数。",
            fields=fields,
            relaxation_options=["增加站点数量", "减少必需站点"],
        )
    lunch = _first_index(required_roles, StopRole.LUNCH)
    dinner = _first_index(required_roles, StopRole.DINNER)
    if (
        required_roles.count(StopRole.LUNCH) > 1
        or required_roles.count(StopRole.DINNER) > 1
        or (lunch is not None and dinner is not None and lunch >= dinner)
    ):
        return ConstraintConflict(
            code="UNSUPPORTED_PLAN_STRUCTURE",
            message="午饭和晚饭的顺序或重复要求无法组成可执行结构。",
            fields=fields,
            relaxation_options=["调整餐食顺序", "减少重复餐食要求"],
        )

    # A lone break is not a meaningful outing.  Keep this deterministic
    # rejection, while allowing all other complete sequences to be dynamic.
    if exact_stop_count == 1 and required_roles == (StopRole.BREAK,):
        return ConstraintConflict(
            code="UNSUPPORTED_PLAN_STRUCTURE",
            message="单独安排休息站点无法组成可执行行程。",
            fields=fields,
            relaxation_options=["增加一个活动站点", "移除休息站点限制"],
        )

    if required_roles and not _capacity_is_feasible(required_roles, constraints):
        return _capacity_conflict(constraints)
    return None


def _is_partial_structure(constraints: PlanRequest) -> bool:
    exact_count = (
        constraints.exact_stop_count.value
        if constraints.exact_stop_count is not None
        else None
    )
    return exact_count is not None and len(_explicit_roles(constraints)) < exact_count


def _build_dynamic_completion_specs(
    constraints: PlanRequest,
) -> tuple[PlanSpec, ...]:
    """Build a small deterministic completion space for partial structure.

    This is deliberately bounded and role-only.  It is a safety fallback when
    the model is unavailable or its proposal is rejected, not a replacement
    for semantic PlanningIntent.
    """

    exact_count = (
        constraints.exact_stop_count.value
        if constraints.exact_stop_count is not None
        else None
    )
    if exact_count is None:
        return ()
    required_roles = _explicit_roles(constraints)
    pool = list(_DYNAMIC_ROLE_POOL)
    for role in required_roles:
        if role not in pool:
            pool.append(role)

    sequences: list[PlanSpec] = []
    if exact_count == 1 and not required_roles:
        raw_sequences = ((StopRole.ACTIVITY,),)
    else:
        raw_sequences = product(tuple(pool), repeat=exact_count)
    for roles in raw_sequences:
        roles = tuple(roles)
        if not _meaningful_sequence(roles):
            continue
        assignment = _required_role_assignment(required_roles, roles)
        if assignment is None:
            continue
        if required_roles:
            roles = list(roles)
            for required, index in zip(
                required_roles,
                assignment[1],
                strict=True,
            ):
                if roles[index] == StopRole.MEAL and required in {
                    StopRole.LUNCH,
                    StopRole.DINNER,
                }:
                    roles[index] = required
            roles = tuple(roles)
        if roles.count(StopRole.LUNCH) > 1 or roles.count(StopRole.DINNER) > 1:
            continue
        lunch = _first_index(roles, StopRole.LUNCH)
        dinner = _first_index(roles, StopRole.DINNER)
        if lunch is not None and dinner is not None and lunch >= dinner:
            continue
        if not _capacity_is_feasible(roles, constraints):
            continue
        sequences.append(
            PlanSpec(spec_id=_stable_plan_spec_id(roles), roles=roles)
        )

    sequences.sort(key=lambda spec: _dynamic_completion_sort_key(spec, required_roles))
    return _merge_specs(tuple(sequences[:_MAX_DYNAMIC_COMPLETIONS]))


def _dynamic_completion_sort_key(
    spec: PlanSpec,
    required_roles: tuple[StopRole, ...],
) -> tuple[object, ...]:
    roles = spec.roles
    dinner_required = StopRole.DINNER in required_roles
    dinner_last_penalty = (
        0 if not dinner_required or roles[-1] == StopRole.DINNER else 1
    )
    return (
        dinner_last_penalty,
        -sum(role == StopRole.ACTIVITY for role in roles),
        sum(role == StopRole.MEAL for role in roles),
        sum(role == StopRole.BREAK for role in roles),
        tuple(role.value for role in roles),
    )


def _meaningful_sequence(roles: tuple[StopRole, ...]) -> bool:
    return not (
        len(roles) == 1
        and roles[0] in {StopRole.BREAK, StopRole.MEAL}
    )


def _minimum_capacity_minutes(roles: tuple[StopRole, ...]) -> int:
    if not roles:
        return 0
    return sum(_ROLE_MINIMUM_MINUTES[role] for role in roles) + max(
        0,
        len(roles) - 1,
    ) * 15


def _capacity_is_feasible(
    roles: tuple[StopRole, ...],
    constraints: PlanRequest,
) -> bool:
    return _minimum_capacity_minutes(roles) <= _available_minutes(constraints)


def _capacity_conflict(constraints: PlanRequest) -> ConstraintConflict:
    return ConstraintConflict(
        code="NO_FEASIBLE_PLAN",
        message="指定时间范围内连满足站点结构的最短行程都无法安排。",
        fields=["time_window", "plan_structure"],
        relaxation_options=["扩大可用时间范围", "减少站点或餐食要求"],
    )


def _merge_specs(*groups: tuple[PlanSpec, ...]) -> tuple[PlanSpec, ...]:
    merged: dict[tuple[str, tuple[StopRole, ...]], PlanSpec] = {}
    for group in groups:
        for spec in group:
            merged.setdefault((spec.spec_id, spec.roles), spec)
    return tuple(merged.values())


def _explicit_roles(constraints: PlanRequest) -> tuple[StopRole, ...]:
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


def _explicit_matches(
    *,
    exact_stop_count: int | None,
    required_roles: tuple[StopRole, ...],
    candidates: tuple[PlanSpec, ...],
) -> tuple[PlanSpec, ...]:
    # A single unspecified stop is ambiguous: do not choose a role for the user.
    if exact_stop_count == 1 and not required_roles:
        return ()

    ranked: list[tuple[int, int, int, PlanSpec, tuple[int, ...]]] = []
    for candidate_index, spec in enumerate(candidates):
        if exact_stop_count is not None and len(spec.roles) != exact_stop_count:
            continue
        # A lone meal is a meal requirement, not an instruction to collapse the
        # whole outing into a single-stop plan.
        if (
            exact_stop_count is None
            and len(required_roles) == 1
            and required_roles[0] in {StopRole.LUNCH, StopRole.DINNER}
            and len(spec.roles) == 1
        ):
            continue
        if not required_roles:
            ranked.append((0, 0, candidate_index, spec, ()))
            continue
        assignment = _required_role_assignment(required_roles, spec.roles)
        if assignment is None:
            continue
        penalty, matched_indices = assignment
        # Prefer the smallest structure preserving the user's ordered roles;
        # an exact meal role beats mapping a generic MEAL slot when tied.
        ranked.append(
            (
                len(spec.roles) - len(required_roles),
                penalty,
                candidate_index,
                spec,
                matched_indices,
            )
        )
    ranked.sort(key=lambda item: item[:3])
    if required_roles and ranked:
        best_key = ranked[0][:2]
        return tuple(
            _bind_explicit_roles(item[3], required_roles, item[4])
            for item in ranked
            if item[:2] == best_key
        )
    return tuple(item[3] for item in ranked)


def _required_role_assignment(
    required_roles: tuple[StopRole, ...],
    actual_roles: tuple[StopRole, ...],
) -> tuple[int, tuple[int, ...]] | None:
    """Find the cheapest ordered subsequence assignment."""

    if len(required_roles) > len(actual_roles):
        return None

    def search(
        required_index: int,
        slot_index: int,
    ) -> tuple[int, tuple[int, ...]] | None:
        if required_index == len(required_roles):
            return 0, ()
        if slot_index == len(actual_roles):
            return None
        required = required_roles[required_index]
        options: list[tuple[int, tuple[int, ...]]] = []
        skipped = search(required_index, slot_index + 1)
        if skipped is not None:
            options.append(skipped)
        actual = actual_roles[slot_index]
        if roles_compatible(required, actual):
            remainder = search(required_index + 1, slot_index + 1)
            if remainder is not None:
                options.append(
                    (
                        remainder[0] + (0 if required == actual else 1),
                        (slot_index, *remainder[1]),
                    )
                )
        return min(options, key=lambda item: (item[0], item[1])) if options else None

    return search(0, 0)


def _bind_explicit_roles(
    spec: PlanSpec,
    required_roles: tuple[StopRole, ...],
    matched_indices: tuple[int, ...],
) -> PlanSpec:
    roles = list(spec.roles)
    for required, index in zip(required_roles, matched_indices, strict=True):
        if roles[index] == StopRole.MEAL and required in {
            StopRole.LUNCH,
            StopRole.DINNER,
        }:
            roles[index] = required
    return replace(spec, roles=tuple(roles))


def _stable_plan_spec_id(roles: tuple[StopRole, ...]) -> str:
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


def _available_minutes(constraints: PlanRequest) -> int:
    window = constraints.planning_window.clock_bounds
    if window is None:
        return 24 * 60
    start_hour, start_minute = (int(part) for part in window.start.split(":"))
    end_hour, end_minute = (int(part) for part in window.end.split(":"))
    return max(0, (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute))


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _safe_code(error: Exception) -> str:
    value = str(error).strip()
    return value if value.replace("_", "").isalnum() else "proposal_rejected"

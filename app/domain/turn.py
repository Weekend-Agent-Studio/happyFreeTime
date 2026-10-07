"""Semantic turn proposals and their deterministic execution actions.

The model-facing contract in this module answers one question only: what did
the user ask the system to do this turn? Constraint values remain raw until
the existing request compilers and ``ConstraintEngine`` normalize them.

``Interpretation`` is still used by execution services as a projection of the
proposal's extracted constraints. It is deliberately not the action
discriminator anymore; callers route on ``CompiledNextAction.kind``.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from app.domain.catalog import ResourceType
from app.domain.decision_context import DecisionContext
from app.domain.constraints import (
    ConstraintPatch,
    ConversationCommand,
    PendingModification,
    RawConstraints,
    ReplacementCriterion,
    StopRole,
    TargetReference,
    TimeProposal,
    Intent,
    Interpretation,
    UserActKind,
)


class TurnTargetProposal(BaseModel):
    """Model-facing target reference; application-owned IDs are impossible."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: StopRole | None = None
    resource_type: ResourceType | None = None
    stop_index: int | None = Field(default=None, ge=0)
    raw_text: str = Field(min_length=1)


class CreatePlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["create_plan"] = "create_plan"
    raw_constraints: RawConstraints = Field(default_factory=RawConstraints)
    time_proposals: tuple[TimeProposal, ...] = ()
    evidence_map: dict[str, str] = Field(default_factory=dict)


class PatchConstraintsProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["patch_constraints"] = "patch_constraints"
    constraint_patch: ConstraintPatch = Field(default_factory=ConstraintPatch)
    evidence_map: dict[str, str] = Field(default_factory=dict)


class ReplaceStopProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["replace_stop"] = "replace_stop"
    target: TurnTargetProposal
    locked_targets: tuple[TurnTargetProposal, ...] = ()
    replacement_criteria: tuple[ReplacementCriterion, ...] = ()
    evidence: dict[str, str] = Field(default_factory=dict)


class CheckWeatherProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["check_weather"] = "check_weather"
    raw_constraints: RawConstraints = Field(default_factory=RawConstraints)
    time_proposals: tuple[TimeProposal, ...] = ()
    evidence_map: dict[str, str] = Field(default_factory=dict)


class QueryPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["query_plan"] = "query_plan"
    query: str = Field(min_length=1)


class ChitchatProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["chitchat"] = "chitchat"


UserAct = Annotated[
    Union[
        CreatePlanProposal,
        PatchConstraintsProposal,
        ReplaceStopProposal,
        CheckWeatherProposal,
        QueryPlanProposal,
        ChitchatProposal,
    ],
    Field(discriminator="kind"),
]


class TurnProposal(BaseModel):
    """Single discriminated model output for one natural-language turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    act: UserAct


class ApplyRequestPatch(BaseModel):
    """Apply a create/update request patch, then let the Engine decide."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["apply_request_patch"] = "apply_request_patch"
    mode: Literal["create", "update"]
    raw_constraints: RawConstraints | None = None
    time_proposals: tuple[TimeProposal, ...] = ()
    evidence_map: dict[str, str] = Field(default_factory=dict)
    constraint_patch: ConstraintPatch | None = None
    condition_requests_plan: bool = False


class ModifySelectedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["modify_selected_plan"] = "modify_selected_plan"
    command: ConversationCommand


class AnswerQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["answer_query"] = "answer_query"
    query_kind: Literal["weather", "plan"]
    raw_constraints: RawConstraints | None = None
    time_proposals: tuple[TimeProposal, ...] = ()
    query: str | None = None
    condition_requests_plan: bool = False


class NoAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["no_action"] = "no_action"
    reason: Literal["chitchat", "unsupported", "empty"]


class NeedsClarification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["needs_clarification"] = "needs_clarification"
    field: str = Field(min_length=1)
    issue_kind: Literal["constraint", "target", "selection", "action"] = "target"
    raw_text: str | None = None
    pending_modification: PendingModification | None = None


CompiledNextAction = Annotated[
    Union[
        ApplyRequestPatch,
        ModifySelectedPlan,
        AnswerQuery,
        NoAction,
        NeedsClarification,
    ],
    Field(discriminator="kind"),
]


class TurnCompilation(BaseModel):
    """Execution projection produced by ``TurnCompiler``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interpretation: Interpretation
    action: CompiledNextAction


def intent_for_action(action: CompiledNextAction) -> Intent:
    """Return the legacy policy intent without using it as a route key."""

    if isinstance(action, ApplyRequestPatch):
        return Intent.PLAN_OUTING if action.mode == "create" else Intent.REFINE_PLAN
    if isinstance(action, ModifySelectedPlan):
        return Intent.REFINE_PLAN
    if isinstance(action, AnswerQuery):
        return Intent.CHECK_WEATHER if action.query_kind == "weather" else Intent.QUERY_PLAN
    if isinstance(action, NeedsClarification):
        return Intent.REFINE_PLAN
    return Intent.CHITCHAT


def user_act_kind_for_action(action: CompiledNextAction | None) -> UserActKind | None:
    """Project a compiled action to the bounded UserAct vocabulary."""

    if isinstance(action, ApplyRequestPatch):
        return "create_plan" if action.mode == "create" else "patch_constraints"
    if isinstance(action, ModifySelectedPlan):
        return "replace_stop"
    if isinstance(action, AnswerQuery):
        return "check_weather" if action.query_kind == "weather" else "query_plan"
    if isinstance(action, NoAction) and action.reason == "chitchat":
        return "chitchat"
    return None


class TurnCompiler:
    """Compile a bounded semantic proposal into one executable action."""

    @classmethod
    def compile(
        cls,
        proposal: TurnProposal,
        *,
        context: DecisionContext | None = None,
    ) -> TurnCompilation:
        """Compile one proposal against the bounded state projection."""
        act = proposal.act
        selected_plan = cls._selected_plan_available(context)
        if isinstance(act, CreatePlanProposal):
            interpretation = cls._interpretation(
                intent=Intent.PLAN_OUTING,
                raw_constraints=act.raw_constraints,
                time_proposals=act.time_proposals,
                evidence_map=act.evidence_map,
            )
            if not cls._allowed(context, "create_plan"):
                return cls._unsupported(interpretation)
            return TurnCompilation(
                interpretation=interpretation,
                action=ApplyRequestPatch(
                    mode="create",
                    raw_constraints=act.raw_constraints,
                    time_proposals=act.time_proposals,
                    evidence_map=dict(act.evidence_map),
                    condition_requests_plan=cls._condition_requests_plan(act.raw_constraints, act.time_proposals),
                ),
            )

        if isinstance(act, PatchConstraintsProposal):
            interpretation = cls._interpretation(
                intent=Intent.REFINE_PLAN,
                conversation_command=ConversationCommand(
                    operation="patch_constraints",
                    constraint_patch=act.constraint_patch,
                    evidence=dict(act.evidence_map),
                ),
                evidence_map=act.evidence_map,
            )
            if (
                context is not None
                and context.request_lifecycle.value == "empty"
                and context.current_request is None
            ):
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="request_lifecycle",
                        issue_kind="action",
                        raw_text="patch_constraints",
                    ),
                )
            if context is not None and context.current_request is None:
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="active_request",
                        issue_kind="constraint",
                    ),
                )
            if not cls._allowed(context, "patch_constraints"):
                return cls._unsupported(interpretation)
            return TurnCompilation(
                interpretation=interpretation,
                action=ApplyRequestPatch(
                    mode="update",
                    constraint_patch=act.constraint_patch,
                    evidence_map=dict(act.evidence_map),
                ),
            )

        if isinstance(act, ReplaceStopProposal):
            # Lifecycle permissions are checked before resolving model text.
            # An empty/draft session must not enter the modification workflow
            # merely because a target string happens to be parseable.
            if not cls._allowed(context, "replace_stop"):
                return TurnCompilation(
                    interpretation=cls._interpretation(
                        intent=Intent.REFINE_PLAN,
                        target_reference=act.target.raw_text,
                        evidence_map=act.evidence,
                    ),
                    action=NoAction(reason="unsupported"),
                )
            command, unresolved = cls._command(act, context=context)
            if unresolved is not None:
                pending = PendingModification(
                    operation="replace",
                    target_raw_text=unresolved,
                    locked_targets=command.locked_targets if command else (),
                    replacement_criteria=command.replacement_criteria if command else act.replacement_criteria,
                    evidence=dict(act.evidence),
                )
                return TurnCompilation(
                    interpretation=cls._interpretation(
                        intent=Intent.REFINE_PLAN,
                        target_reference=unresolved,
                        evidence_map=act.evidence,
                    ),
                    action=NeedsClarification(
                        field="target_reference",
                        issue_kind="target",
                        raw_text=unresolved,
                        pending_modification=pending,
                    ),
                )
            assert command is not None
            interpretation = cls._interpretation(
                intent=Intent.REFINE_PLAN,
                target_reference=act.target.raw_text,
                conversation_command=command,
                evidence_map=act.evidence,
            )
            if not selected_plan:
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="selected_plan_id",
                        issue_kind="selection",
                        pending_modification=PendingModification(
                            operation="replace",
                            target_raw_text=act.target.raw_text,
                            locked_targets=command.locked_targets,
                            constraint_patch=command.constraint_patch,
                            replacement_criteria=command.replacement_criteria,
                            evidence=dict(act.evidence),
                        ),
                    ),
                )
            if not cls._allowed(context, "replace_stop"):
                return cls._unsupported(interpretation)
            return TurnCompilation(
                interpretation=interpretation,
                action=ModifySelectedPlan(command=command),
            )

        if isinstance(act, CheckWeatherProposal):
            interpretation = cls._interpretation(
                intent=Intent.CHECK_WEATHER,
                raw_constraints=act.raw_constraints,
                time_proposals=act.time_proposals,
                evidence_map=act.evidence_map,
            )
            if not cls._allowed(context, "check_weather"):
                return cls._unsupported(interpretation)
            condition_requests_plan = cls._condition_requests_plan(
                act.raw_constraints,
                act.time_proposals,
            )
            return TurnCompilation(
                interpretation=interpretation,
                action=AnswerQuery(
                    query_kind="weather",
                    raw_constraints=act.raw_constraints,
                    time_proposals=act.time_proposals,
                    condition_requests_plan=condition_requests_plan,
                ),
            )

        if isinstance(act, QueryPlanProposal):
            interpretation = cls._interpretation(intent=Intent.QUERY_PLAN)
            if not cls._allowed(context, "query_plan"):
                return cls._unsupported(interpretation)
            return TurnCompilation(
                interpretation=interpretation,
                action=AnswerQuery(query_kind="plan", query=act.query),
            )

        interpretation = cls._interpretation(intent=Intent.CHITCHAT)
        if not cls._allowed(context, "chitchat"):
            return cls._unsupported(interpretation)
        return TurnCompilation(
            interpretation=interpretation,
            action=NoAction(reason="chitchat"),
        )

    @classmethod
    def compile_command(
        cls,
        command: ConversationCommand,
        *,
        context: DecisionContext | None = None,
    ) -> TurnCompilation:
        """Compile an already validated UI command without a legacy projection."""

        selected_plan = cls._selected_plan_available(context)
        operation = command.operation.value
        intent = (
            Intent.REFINE_PLAN
            if operation in {"replace", "patch_constraints"}
            else Intent.CLARIFY
        )
        interpretation = cls._interpretation(
            intent=intent,
            conversation_command=command,
            evidence_map=command.evidence,
        )
        if operation == "patch_constraints":
            if (
                context is not None
                and context.request_lifecycle.value == "empty"
                and context.current_request is None
            ):
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="request_lifecycle",
                        issue_kind="action",
                        raw_text="patch_constraints",
                    ),
                )
            if context is not None and context.current_request is None:
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="active_request",
                        issue_kind="constraint",
                    ),
                )
            if not cls._allowed(context, "patch_constraints"):
                return cls._unsupported(interpretation)
            return TurnCompilation(
                interpretation=interpretation,
                action=ApplyRequestPatch(
                    mode="update",
                    constraint_patch=command.constraint_patch,
                    evidence_map=dict(command.evidence),
                ),
            )
        if operation == "replace":
            if not cls._allowed(context, "replace_stop"):
                return cls._unsupported(interpretation)
            if not selected_plan:
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="selected_plan_id",
                        issue_kind="selection",
                        pending_modification=PendingModification(
                            operation="replace",
                            target_raw_text=(
                                command.target.raw_text if command.target else None
                            ),
                            locked_targets=command.locked_targets,
                            constraint_patch=command.constraint_patch,
                            replacement_criteria=command.replacement_criteria,
                            evidence=command.evidence,
                        ),
                    ),
                )
            invalid_target = cls._invalid_command_target(command, context)
            if invalid_target is not None:
                return TurnCompilation(
                    interpretation=interpretation,
                    action=NeedsClarification(
                        field="target_reference",
                        issue_kind="target",
                        raw_text=invalid_target,
                        pending_modification=PendingModification(
                            operation="replace",
                            target_raw_text=invalid_target,
                            locked_targets=command.locked_targets,
                            constraint_patch=command.constraint_patch,
                            replacement_criteria=command.replacement_criteria,
                            evidence=command.evidence,
                        ),
                    ),
                )
            return TurnCompilation(
                interpretation=interpretation,
                action=ModifySelectedPlan(command=command),
            )
        return cls._unsupported(interpretation)

    @staticmethod
    def _selected_plan_available(
        context: DecisionContext | None,
    ) -> bool:
        return context is not None and context.selected_plan is not None

    @staticmethod
    def _allowed(context: DecisionContext | None, action: str) -> bool:
        if context is None or not context.allowed_actions:
            return True
        return action in context.allowed_actions

    @staticmethod
    def _unsupported(interpretation: Interpretation) -> TurnCompilation:
        return TurnCompilation(
            interpretation=interpretation,
            action=NoAction(reason="unsupported"),
        )

    @staticmethod
    def _action_name(action: CompiledNextAction) -> str | None:
        if isinstance(action, ApplyRequestPatch):
            return "create_plan" if action.mode == "create" else "patch_constraints"
        if isinstance(action, ModifySelectedPlan):
            return "replace_stop"
        if isinstance(action, AnswerQuery):
            return "check_weather" if action.query_kind == "weather" else "query_plan"
        if isinstance(action, NoAction):
            return "chitchat" if action.reason == "chitchat" else None
        return None

    @staticmethod
    def _invalid_command_target(
        command: ConversationCommand,
        context: DecisionContext | None,
    ) -> str | None:
        if context is None or context.selected_plan is None:
            return None
        stop_count = len(context.selected_plan.stops)
        references = tuple(
            reference
            for reference in (command.target, *command.locked_targets)
            if reference is not None
        )
        for reference in references:
            if reference.stop_index is not None and reference.stop_index >= stop_count:
                return reference.raw_text
        return None

    @staticmethod
    def _interpretation(
        *,
        intent: Intent,
        raw_constraints: RawConstraints | None = None,
        time_proposals: tuple[TimeProposal, ...] = (),
        target_reference: str | None = None,
        conversation_command: ConversationCommand | None = None,
        evidence_map: dict[str, str] | None = None,
    ) -> Interpretation:
        return Interpretation(
            primary_intent=intent,
            intent_scores={intent: 1.0},
            raw_constraints=raw_constraints or RawConstraints(),
            time_proposals=time_proposals,
            target_reference=target_reference,
            conversation_command=conversation_command,
            evidence_map=dict(evidence_map or {}),
        )

    @staticmethod
    def _condition_requests_plan(
        raw_constraints: RawConstraints,
        time_proposals: tuple[TimeProposal, ...],
    ) -> bool:
        return bool(
            raw_constraints.preferences
            or raw_constraints.scene_tags
            or raw_constraints.diet_tags
            or raw_constraints.avoid
            or raw_constraints.required_stop_roles
            or raw_constraints.exact_stop_count is not None
            or raw_constraints.duration_minutes is not None
            or time_proposals
        )

    @classmethod
    def _command(
        cls,
        proposal: ReplaceStopProposal,
        *,
        context: DecisionContext | None = None,
    ) -> tuple[ConversationCommand | None, str | None]:
        target, unresolved = cls._target(proposal.target, context=context)
        if unresolved is not None:
            return None, unresolved
        locked: list[TargetReference] = []
        for item in proposal.locked_targets:
            reference, item_unresolved = cls._target(item, context=context)
            if item_unresolved is not None or reference is None:
                return None, item_unresolved or item.raw_text
            locked.append(reference)
        return (
            ConversationCommand(
                operation="replace",
                target=target,
                locked_targets=tuple(locked),
                replacement_criteria=proposal.replacement_criteria,
                evidence=dict(proposal.evidence),
            ),
            None,
        )

    @staticmethod
    def _target(
        target: TurnTargetProposal,
        *,
        context: DecisionContext | None = None,
    ) -> tuple[TargetReference | None, str | None]:
        if (
            target.stop_index is not None
            and context is not None
            and context.selected_plan is not None
            and target.stop_index >= len(context.selected_plan.stops)
        ):
            return None, target.raw_text
        if target.role is not None or target.resource_type is not None or target.stop_index is not None:
            return (
                TargetReference(
                    role=target.role,
                    resource_type=target.resource_type,
                    stop_index=target.stop_index,
                    raw_text=target.raw_text,
                ),
                None,
            )
        aliases = {
            "活动": {"role": StopRole.ACTIVITY},
            "活动站": {"role": StopRole.ACTIVITY},
            "景点": {"role": StopRole.ACTIVITY},
            "晚饭": {"role": StopRole.DINNER},
            "晚餐": {"role": StopRole.DINNER},
            "午饭": {"role": StopRole.LUNCH},
            "午餐": {"role": StopRole.LUNCH},
            "用餐": {"role": StopRole.MEAL},
            "餐厅": {"resource_type": ResourceType.RESTAURANT},
            "饭店": {"resource_type": ResourceType.RESTAURANT},
            "第一站": {"stop_index": 0},
            "第1站": {"stop_index": 0},
            "第二站": {"stop_index": 1},
            "第2站": {"stop_index": 1},
            "第三站": {"stop_index": 2},
            "第3站": {"stop_index": 2},
            "第四站": {"stop_index": 3},
            "第4站": {"stop_index": 3},
        }
        normalized = target.raw_text.strip(" ，。！？；：")
        values = aliases.get(normalized)
        if values is None:
            return None, target.raw_text
        return TargetReference(raw_text=target.raw_text, **values), None

"""Deep workflow modules used by the entry graph.

The graph owns sequencing, interrupts and checkpointing.  These workflows own
the business conversion from a typed action/answer to a state delta.  Keeping
that conversion behind a small result interface prevents route functions from
reconstructing meaning from several boolean flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Mapping
import uuid

from app.domain.constraints import (
    ActorContext,
    Assumption,
    ClarificationAction,
    ClarificationReply,
    ConstraintSource,
    ConversationCommand,
    ConstraintValue,
    ClarificationIssue,
    Interpretation,
    PendingModification,
    PlanRequest,
    QuestionDecision,
    RequestPatch,
    Intent,
)
from app.domain.planning import CandidateSet, ConstraintConflict
from app.domain.recovery import (
    ApplyRequestPatchAction,
    CancelTurnAction,
    FieldClarificationInteraction,
    KeepCurrentPlanAction,
    OpenConstraintEditorAction,
    RecoveryAction,
    RecoveryActionResponse,
    RecoveryChoiceInteraction,
    RecoveryReason,
    RecoveryStage,
    ReplanCurrentRequestAction,
    RequestFieldAction,
    StartNewRequestAction,
)
from app.domain.runtime import RuntimeDecision
from app.domain.run_trace import RunObserver
from app.services.clarification import ClarificationResolution, ClarificationResolver
from app.services.clarification_patch import ClarificationPatchCompiler, merge_request_patches
from app.services.constraint_engine import (
    ConflictedRequest,
    ConstraintEngine,
    NeedsClarification as ConstraintNeedsClarification,
    ResolvedRequest,
)
from app.services.enrichment import EnvironmentContext, EnrichmentService
from app.services.planning import PlanningService
from app.services.question_policy import QuestionPolicy, QuestionPolicyContext
from app.services.request_patch_compiler import RequestPatchProposalCompiler
from app.services.request_patch_update import RequestPatchUpdateCompiler
from app.services.recovery_reason_adapter import RecoveryReasonAdapter
from app.services.recovery_policy import RecoveryPolicy
from app.domain.turn import (
    ApplyRequestPatch,
    CompiledNextAction,
    ModifySelectedPlan,
    NeedsClarification as ActionNeedsClarification,
    intent_for_action,
)


class WorkflowRoute(StrEnum):
    """The only route decisions exposed by a workflow to the graph."""

    ROUTER = "router"
    APPLY_REQUEST_PATCH = "apply_request_patch"
    REPLAN_CURRENT_REQUEST = "replan_current_request"
    COMPILE_REQUEST = "compile_request"
    COMPILE_PATCH = "compile_patch"
    MODIFY_PLAN = "modify_plan"
    GATE = "gate"
    ASK_QUESTION = "ask_question"
    DECIDE_RECOVERY = "decide_recovery"
    INTERRUPT_FOR_RECOVERY = "interrupt_for_recovery"
    APPLY_RECOVERY_ACTION = "apply_recovery_action"
    PLANNING = "planning"
    END = "__end__"


class WorkflowOutcome(StrEnum):
    """Business outcome; unlike a route this is useful for response assembly."""

    CREATE = "create"
    CONSTRAINT_PATCH = "constraint_patch"
    CONSTRAINT_PATCH_NOOP = "constraint_patch_noop"
    CONSTRAINT_PATCH_CONFLICT = "constraint_patch_conflict"
    MODIFICATION = "modification"
    CLARIFICATION = "clarification"
    CONFLICT = "conflict"
    RECOVERY = "recovery"
    RECOVERY_ACTION_APPLIED = "recovery_action_applied"
    RECOVERY_ACTION_REJECTED = "recovery_action_rejected"
    RECOVERY_ACTION_FINISHED = "recovery_action_finished"


def _candidate_set_for_conflict(
    conflict: ConstraintConflict,
    request: PlanRequest,
    *,
    stage: RecoveryStage = RecoveryStage.CONSTRAINT_COMPILATION,
    plan_version_id: str | None = None,
) -> CandidateSet:
    return CandidateSet(
        conflict=conflict,
        recovery_reason=RecoveryReasonAdapter().from_conflict(
            conflict,
            request,
            stage=stage,
            plan_version_id=plan_version_id,
        ),
    )


@dataclass(frozen=True)
class WorkflowResult:
    """Small graph-facing interface for a workflow execution."""

    updates: dict[str, object] = field(default_factory=dict)
    route: WorkflowRoute = WorkflowRoute.END
    outcome: WorkflowOutcome | None = None

    def as_state(self) -> dict[str, object]:
        result = dict(self.updates)
        if "pending_issue" in result:
            decision = result.get("pending_issue")
            result["pending_interaction"] = (
                FieldClarificationInteraction(
                    interaction_id=decision.clarification_id,
                    request_revision=decision.request_revision,
                    decision=decision,
                )
                if isinstance(decision, QuestionDecision)
                and decision.need_question
                and decision.clarification_id is not None
                and decision.request_revision is not None
                else None
            )
        result["workflow_route"] = self.route.value
        if self.outcome is not None:
            result["workflow_outcome"] = self.outcome.value
        return result


# The graph state remains flat for LangGraph/checkpoint compatibility.  These
# aliases document lifecycle ownership without creating nested "god objects".
DurableState = Mapping[str, object]
StatePatch = dict[str, object]
PrepareQuestion = Callable[[QuestionDecision], QuestionDecision]
EnvironmentProvider = Callable[[ActorContext], EnvironmentContext]


class RequestWorkflow:
    """Compile/apply request changes and return explicit graph state deltas."""

    def __init__(
        self,
        *,
        environment_provider: EnvironmentProvider,
        enrichment_service: EnrichmentService,
        proposal_compiler: RequestPatchProposalCompiler,
        update_compiler: RequestPatchUpdateCompiler,
        constraint_engine: ConstraintEngine,
        question_policy: QuestionPolicy,
        prepare_question: PrepareQuestion,
    ) -> None:
        self._environment_provider = environment_provider
        self._enrichment = enrichment_service
        self._proposal_compiler = proposal_compiler
        self._update_compiler = update_compiler
        self._engine = constraint_engine
        self._question_policy = question_policy
        self._prepare_question = prepare_question

    def compile_create(
        self,
        state: DurableState,
        *,
        interpretation: Interpretation,
        action: CompiledNextAction,
    ) -> WorkflowResult:
        actor = state["actor"]
        environment = self._environment_provider(actor)
        enrichment = self._enrichment.enrich(interpretation, actor, environment)
        compilation = self._proposal_compiler.compile(
            interpretation,
            enrichment.request_patch,
            environment,
        )
        outcome = self._engine.apply(
            PlanRequest(),
            compilation.patch,
            issues=compilation.issues,
        )
        common = {
            "assumptions": tuple((*enrichment.assumptions, *compilation.assumptions)),
            "geocoding_fact": enrichment.geocoding_fact,
            "condition_requests_plan": compilation.condition_requests_plan,
        }
        if isinstance(outcome, ConstraintNeedsClarification):
            issues = compilation.issues or (outcome.issue,)
            question = self._prepare_question(
                self._question_policy.decide(
                    intent_for_action(action),
                    outcome.request or PlanRequest(),
                    QuestionPolicyContext(
                        has_plans=bool(state.get("has_plans", False)),
                        selected_plan_index=interpretation.selected_plan_index,
                    ),
                    issue=outcome.issue,
                )
            )
            return WorkflowResult(
                updates={
                    **common,
                    "active_request": outcome.request or PlanRequest(),
                    "pending_patch": None if outcome.request is not None else compilation.patch,
                    "pending_issues": () if outcome.request is not None else issues,
                    "pending_issue": question,
                },
                route=WorkflowRoute.GATE,
                outcome=WorkflowOutcome.CREATE,
            )
        if isinstance(outcome, ConflictedRequest):
            # Keep the committed request unchanged.  A fully parsed candidate
            # is retained separately as a draft so the user can repair the
            # conflicting field without losing the other supplied values.
            conflict_request = outcome.candidate_request or PlanRequest()
            return WorkflowResult(
                updates={
                    **common,
                    "candidate_set": _candidate_set_for_conflict(
                        outcome.conflict, conflict_request
                    ),
                    "active_request": PlanRequest(),
                    "recovery_base_request": outcome.candidate_request,
                    "pending_patch": None,
                    "pending_issues": (),
                    "pending_issue": None,
                },
                route=WorkflowRoute.GATE,
                outcome=WorkflowOutcome.CONFLICT,
            )
        return WorkflowResult(
            updates={
                **common,
                "active_request": outcome.request,
                "pending_patch": None,
                "pending_issues": (),
                "pending_issue": None,
            },
            route=WorkflowRoute.GATE,
            outcome=WorkflowOutcome.CREATE,
        )

    def compile_update(
        self,
        state: DurableState,
        *,
        action: ApplyRequestPatch,
    ) -> WorkflowResult:
        active_request = state.get("recovery_base_request") or state.get("active_request")
        if active_request is None:
            question = self._prepare_question(
                QuestionDecision(
                    need_question=True,
                    field="active_plan_version_id",
                    question="当前方案缺少可恢复的约束快照，请重新生成并选择方案。",
                    severity="blocking",
                    issue_kind="target",
                    request_revision=0,
                    rule_id="question.patch.active_constraints.v1",
                )
            )
            return WorkflowResult(
                updates={"pending_issue": question},
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.CONSTRAINT_PATCH,
            )
        if action.constraint_patch is None:
            raise ValueError("compile_patch requires a constraint patch")
        actor = state["actor"]
        compilation = self._update_compiler.compile_update_proposal(
            base=active_request,
            proposal=action.constraint_patch,
            actor=actor,
            environment=self._environment_provider(actor),
            evidence_map=action.evidence_map,
        )
        result = self._engine.apply(
            active_request,
            compilation.patch,
            issues=compilation.issues,
        )
        if isinstance(result, ConstraintNeedsClarification):
            candidate_request = result.request or active_request
            question = self._prepare_question(
                self._question_policy.decide(
                    intent_for_action(action),
                    candidate_request,
                    QuestionPolicyContext(
                        has_plans=bool(state.get("has_plans", False)),
                        selected_plan_index=state["interpretation"].selected_plan_index,
                    ),
                    issue=result.issue,
                )
            )
            return WorkflowResult(
                updates={
                    **(
                        {"active_request": result.request}
                        if result.request is not None
                        and state.get("recovery_base_request") is None
                        else {}
                    ),
                    **(
                        {"recovery_base_request": candidate_request}
                        if state.get("recovery_base_request") is not None
                        else {}
                    ),
                    **(
                        {"pending_request_base": candidate_request}
                        if state.get("recovery_base_request") is not None
                        else {}
                    ),
                    "pending_issue": question,
                    "pending_patch": None if result.request is not None else compilation.patch,
                    "pending_issues": () if result.request is not None else compilation.issues or (result.issue,),
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.CONSTRAINT_PATCH,
            )
        if isinstance(result, ConflictedRequest):
            recovery_request = result.candidate_request or active_request
            return WorkflowResult(
                updates={
                    "candidate_set": _candidate_set_for_conflict(
                        result.conflict, recovery_request
                    ),
                    "recovery_base_request": result.candidate_request,
                    "pending_issue": None,
                    "pending_patch": None,
                    "pending_issues": (),
                },
                route=WorkflowRoute.END,
                outcome=WorkflowOutcome.CONSTRAINT_PATCH_CONFLICT,
            )
        return WorkflowResult(
            updates={
                "active_request": result.request,
                "recovery_base_request": None,
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
            },
            route=(
                WorkflowRoute.END
                if state.get("defer_planning", False)
                else WorkflowRoute.PLANNING
            ),
            outcome=WorkflowOutcome.CONSTRAINT_PATCH,
        )

    def apply_patch(
        self,
        state: DurableState,
        *,
        request: PlanRequest,
        patch: RequestPatch,
        issues: tuple[ClarificationIssue, ...] = (),
        existing_decision: QuestionDecision | None = None,
        resumed: bool = False,
    ) -> WorkflowResult:
        result = self._engine.apply(request, patch, issues=issues)
        if isinstance(result, ConstraintNeedsClarification):
            candidate = result.request or request
            if (
                existing_decision is not None
                and existing_decision.field == result.issue.field
                and existing_decision.request_revision == candidate.revision
            ):
                question = existing_decision
            else:
                interpretation = state.get("interpretation") or Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    reply="正在按编辑后的条件规划。",
                )
                question = self._prepare_question(
                    self._question_policy.decide(
                        interpretation.primary_intent,
                        candidate,
                        QuestionPolicyContext(
                            has_plans=bool(state.get("has_plans", False)),
                            selected_plan_index=interpretation.selected_plan_index,
                        ),
                        issue=result.issue,
                    )
                )
            return WorkflowResult(
                updates={
                    **(
                        {"active_request": result.request}
                        if result.request is not None
                        and state.get("recovery_base_request") is None
                        and state.get("pending_request_base") is None
                        else {}
                    ),
                    **(
                        {"recovery_base_request": candidate}
                        if state.get("recovery_base_request") is not None
                        else {}
                    ),
                    **(
                        {"pending_request_base": candidate}
                        if state.get("pending_request_base") is not None
                        or state.get("recovery_base_request") is not None
                        else {}
                    ),
                    "pending_issue": question,
                    "pending_patch": patch if result.request is None else None,
                    "pending_issues": issues or (result.issue,),
                    "clarification_resolution": (
                        ClarificationResolution.UNRESOLVED.value if resumed else None
                    ),
                    "ready_for_planning": False,
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.CLARIFICATION,
            )
        if isinstance(result, ConflictedRequest):
            recovery_request = result.candidate_request or request
            return WorkflowResult(
                updates={
                    "candidate_set": _candidate_set_for_conflict(
                        result.conflict, recovery_request
                    ),
                    "recovery_base_request": result.candidate_request,
                    "pending_request_base": None,
                    "recovery_continuation": None,
                    "pending_issue": None,
                    "pending_patch": None,
                    "pending_issues": (),
                    "clarification_resolution": "conflict" if resumed else None,
                    "ready_for_planning": False,
                },
                route=WorkflowRoute.END,
                outcome=WorkflowOutcome.CONSTRAINT_PATCH_CONFLICT,
            )
        changed = bool(result.changed_fields)
        return WorkflowResult(
            updates={
                "active_request": result.request,
                "recovery_base_request": None,
                "pending_request_base": None,
                "recovery_continuation": None,
                "candidate_set": None,
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "clarification_resolution": (
                    ClarificationResolution.RESOLVED.value if resumed else None
                ),
                "ready_for_planning": changed,
            },
            route=(
                WorkflowRoute.END
                if state.get("defer_planning", False) or not changed
                else WorkflowRoute.MODIFY_PLAN
                if state.get("recovery_continuation") == "modify"
                else WorkflowRoute.PLANNING
            ),
            outcome=(
                WorkflowOutcome.CONSTRAINT_PATCH
                if changed
                else WorkflowOutcome.CONSTRAINT_PATCH_NOOP
            ),
        )

    def replan(self) -> WorkflowResult:
        return WorkflowResult(
            updates={
                "interpretation": Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    reply="正在按已保存的规划条件重新生成方案。",
                ),
                "candidate_set": None,
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "ready_for_planning": True,
            },
            route=WorkflowRoute.PLANNING,
            outcome=WorkflowOutcome.CONSTRAINT_PATCH,
        )


class ModificationWorkflow:
    """Own selected-plan preconditions and modification outcome projection."""

    def __init__(self, planning_service: PlanningService, prepare_question: PrepareQuestion) -> None:
        self._planning = planning_service
        self._prepare_question = prepare_question

    def execute(
        self,
        state: DurableState,
        *,
        observer: RunObserver | None = None,
    ) -> WorkflowResult:
        action = state.get("next_action")
        active_request = state.get("active_request")
        if isinstance(action, ActionNeedsClarification):
            field = action.field
            if field == "selected_plan_id":
                question_text = "请先选择一个方案，再告诉我要保留和替换哪一站。"
                issue_kind = "selection"
                rule_id = "question.selected_plan.v1"
            else:
                field = "target_reference"
                question_text = "要替换哪一站？可以输入“活动”“晚餐”或“第二站”。"
                issue_kind = "target"
                rule_id = "question.target_reference.v1"
            return WorkflowResult(
                updates={
                    "pending_issue": self._prepare_question(QuestionDecision(
                        need_question=True,
                        field=field,
                        question=question_text,
                        severity="blocking",
                        issue_kind=issue_kind,
                        request_revision=active_request.revision if active_request else 0,
                        rule_id=rule_id,
                    )),
                    "pending_modification": action.pending_modification or PendingModification(),
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.MODIFICATION,
            )
        if not isinstance(action, ModifySelectedPlan):
            raise ValueError("modify_plan requires a compiled modification action")
        selected_plan = state.get("selected_plan")
        if selected_plan is None:
            return WorkflowResult(
                updates={
                    "pending_issue": self._prepare_question(QuestionDecision(
                        need_question=True,
                        field="selected_plan_id",
                        question="请先选择一个方案，再告诉我要保留和替换哪一站。",
                        severity="blocking",
                        issue_kind="selection",
                        request_revision=active_request.revision if active_request else 0,
                    ))
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.MODIFICATION,
            )
        if active_request is None:
            return WorkflowResult(
                updates={
                    "pending_issue": self._prepare_question(QuestionDecision(
                        need_question=True,
                        field="active_plan_version_id",
                        question="当前方案缺少可恢复的约束快照，请重新生成并选择方案。",
                        severity="blocking",
                        issue_kind="selection",
                        allow_free_text=False,
                        request_revision=0,
                    ))
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.MODIFICATION,
            )
        outcome = self._planning.modify_selected_plan(
            selected_plan=selected_plan,
            constraints=active_request,
            command=action.command,
            observer=observer,
        )
        candidate_set = outcome.candidate_set
        if candidate_set is not None and candidate_set.conflict is not None:
            candidate_set = candidate_set.model_copy(
                update={
                    "recovery_reason": RecoveryReasonAdapter().from_conflict(
                        candidate_set.conflict,
                        active_request,
                        stage=RecoveryStage.MODIFICATION,
                        plan_version_id=state.get("active_plan_version_id"),
                        search_traces=candidate_set.search_traces,
                        catalog_violations=candidate_set.catalog_violations,
                    )
                }
            )
        question = outcome.question
        if question is not None:
            question = question.model_copy(update={
                "request_revision": active_request.revision,
                **(
                    {"issue_kind": "target"}
                    if question.field in {"target_reference", "locked_stop"}
                    else {}
                ),
            })
        prepared = self._prepare_question(question) if question is not None else None
        pending = None
        if prepared is not None and prepared.field in {"target_reference", "locked_stop"}:
            command = action.command
            pending = PendingModification(
                target_raw_text=command.target.raw_text if command.target else None,
                locked_targets=command.locked_targets,
                constraint_patch=command.constraint_patch,
                replacement_criteria=command.replacement_criteria,
                evidence=command.evidence,
            )
        updates = {
            "candidate_set": candidate_set,
            "plan_diffs": outcome.plan_diffs,
            "pending_issue": prepared,
            "pending_modification": pending,
            "runtime_decisions": (
                *state.get("runtime_decisions", ()),
                outcome.runtime_decision,
                *(
                    (candidate_set.retrieval_runtime_decision,)
                    if (
                        candidate_set is not None
                        and candidate_set.retrieval_runtime_decision is not None
                    )
                    else ()
                ),
            ),
        }
        return WorkflowResult(
            updates=updates,
            route=WorkflowRoute.ASK_QUESTION if prepared is not None else WorkflowRoute.END,
            outcome=WorkflowOutcome.MODIFICATION,
        )


class ClarificationWorkflow:
    """Resume one interrupt and delegate request changes to RequestWorkflow."""

    def __init__(
        self,
        *,
        request_workflow: RequestWorkflow,
        clarification_resolver: ClarificationResolver,
        clarification_patch_compiler: ClarificationPatchCompiler,
        environment_provider: EnvironmentProvider,
        prepare_question: PrepareQuestion,
        coerce_reply: Callable[[object], ClarificationReply],
        runtime_decision: Callable[[QuestionDecision, ClarificationReply, ClarificationResolution], RuntimeDecision],
        resolved_request_value: Callable[[PlanRequest, str], object | None],
        patch_resolves_issue: Callable[[RequestPatch, ClarificationIssue, PlanRequest], bool],
    ) -> None:
        self._request_workflow = request_workflow
        self._resolver = clarification_resolver
        self._patch_compiler = clarification_patch_compiler
        self._environment_provider = environment_provider
        self._prepare_question = prepare_question
        self._coerce_reply = coerce_reply
        self._runtime_decision = runtime_decision
        self._resolved_request_value = resolved_request_value
        self._patch_resolves_issue = patch_resolves_issue

    def resume(self, state: DurableState, *, answer: object) -> WorkflowResult:
        decision = state.get("pending_issue")
        if decision is None:
            raise ValueError("ask_question requires a QuestionDecision")
        context_patch_answer = (
            answer
            if isinstance(answer, dict) and answer.get("kind") == "planning_context_patch"
            else None
        )
        if context_patch_answer is not None:
            reply = ClarificationReply(
                clarification_id=str(context_patch_answer.get("clarification_id") or ""),
                action=ClarificationAction.ANSWER,
                request_revision=context_patch_answer.get("request_revision"),
            )
        else:
            reply = self._coerce_reply(answer)
        request = (
            state.get("pending_request_base")
            or state.get("active_request")
            or PlanRequest()
        )
        expected_revision = decision.request_revision
        if decision.clarification_id and reply.clarification_id != decision.clarification_id:
            return self._stale(
                "STALE_CLARIFICATION_ID",
                "clarification_id",
                state.get("active_request") or PlanRequest(),
                state.get("active_plan_version_id"),
            )
        if expected_revision is not None and (
            request.revision != expected_revision
            or reply.request_revision != expected_revision
        ):
            return self._stale(
                "STALE_CLARIFICATION_REVISION",
                "request_revision",
                state.get("active_request") or PlanRequest(),
                state.get("active_plan_version_id"),
            )

        if context_patch_answer is not None:
            patch = RequestPatch.model_validate(context_patch_answer.get("request_patch"))
            issues = tuple(
                ClarificationIssue.model_validate(item)
                for item in context_patch_answer.get("request_patch_issues", ())
            )
            pending_patch = state.get("pending_patch")
            if pending_patch is not None:
                patch = merge_request_patches(pending_patch, patch)
                unresolved = tuple(
                    issue for issue in state.get("pending_issues", ())
                    if not self._patch_resolves_issue(patch, issue, request)
                )
                issues = (*unresolved, *issues)
            return self._request_workflow.apply_patch(
                state,
                request=request,
                patch=patch,
                issues=issues,
                existing_decision=decision,
                resumed=True,
            )

        value = (reply.value or "").strip()
        if reply.action == ClarificationAction.ANSWER and value in {"取消", "算了", "先不规划了"}:
            reply = reply.model_copy(update={"action": ClarificationAction.CANCEL})
        elif reply.action == ClarificationAction.ANSWER and value in {
            "按默认来吧", "按默认", "用默认", "使用默认出发地", "默认地点",
        }:
            reply = reply.model_copy(update={"action": ClarificationAction.USE_DEFAULT})

        if reply.action in {ClarificationAction.CANCEL, ClarificationAction.NEW_REQUEST}:
            outcome = self._resolver.resolve(
                pending_question=decision,
                reply=reply,
                base_interpretation=state["interpretation"],
                pending_modification=state.get("pending_modification"),
            )
            trace = self._runtime_decision(decision, reply, outcome.status)
            if outcome.status == ClarificationResolution.NEW_REQUEST:
                return WorkflowResult(
                    updates={
                        "user_input": outcome.value or "",
                        "interpretation": None,
                        "assumptions": (),
                        "geocoding_fact": None,
                        "active_request": None,
                        "pending_issue": None,
                        "pending_patch": None,
                        "pending_issues": (),
                        "candidate_set": None,
                        "plan_diffs": (),
                        "pending_modification": None,
                        "pending_request_base": None,
                        "recovery_continuation": None,
                        "recovery_base_request": None,
                        "clarification_resolution": outcome.status.value,
                        "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
                    },
                    route=WorkflowRoute.ROUTER,
                    outcome=WorkflowOutcome.CLARIFICATION,
                )
            return WorkflowResult(
                updates={
                    "pending_issue": None,
                    "pending_patch": None,
                    "pending_issues": (),
                    "pending_request_base": None,
                    "recovery_continuation": None,
                    "clarification_resolution": outcome.status.value,
                    "interpretation": outcome.interpretation,
                    "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
                },
                route=WorkflowRoute.END,
                outcome=WorkflowOutcome.CLARIFICATION,
            )

        if decision.issue_kind == "constraint":
            actor = state["actor"]
            environment = self._environment_provider(actor)
            if reply.action == ClarificationAction.USE_DEFAULT:
                field_patch = self._patch_compiler.compile_default(
                    field=decision.field or "", request=request, environment=environment
                )
            elif reply.action == ClarificationAction.ANSWER and decision.allow_free_text and value:
                field_patch = self._patch_compiler.compile_answer(
                    field=decision.field or "",
                    value=value,
                    request=request,
                    actor=actor,
                    environment=environment,
                )
            else:
                field_patch = None
            next_attempt = min(decision.max_attempts, decision.attempt + 1)
            if field_patch is None:
                retry = self._prepare_question(decision.model_copy(update={
                    "attempt": next_attempt,
                    "allow_free_text": next_attempt < decision.max_attempts,
                }))
                status = ClarificationResolution.UNRESOLVED
                return WorkflowResult(
                    updates={
                        "pending_issue": retry,
                        "clarification_resolution": status.value,
                        "ready_for_planning": False,
                        "runtime_decisions": (
                            *state.get("runtime_decisions", ()),
                            self._runtime_decision(decision, reply, status),
                        ),
                    },
                    route=WorkflowRoute.ASK_QUESTION,
                    outcome=WorkflowOutcome.CLARIFICATION,
                )
            pending_patch = state.get("pending_patch") or RequestPatch(
                base_revision=request.revision,
                source=ConstraintSource.USER_EXPLICIT,
            )
            merged_patch = merge_request_patches(pending_patch, field_patch)
            remaining_issues = tuple(
                issue for issue in state.get("pending_issues", ())
                if issue.field != decision.field
            )
            result = self._request_workflow.apply_patch(
                state,
                request=request,
                patch=merged_patch,
                issues=remaining_issues,
                existing_decision=decision,
                resumed=True,
            )
            is_default = reply.action == ClarificationAction.USE_DEFAULT
            assumptions = list(state.get("assumptions", ()))
            if is_default and result.updates.get("active_request") is not None:
                assumption_value = self._resolved_request_value(
                    result.updates["active_request"], decision.field or ""
                )
                if assumption_value is not None:
                    assumptions.append(Assumption(
                        field=decision.field or "unknown",
                        value=assumption_value,
                        reason="用户选择使用系统默认值",
                        rule_id=f"clarification.default.{decision.field}.v1",
                    ))
            status = (
                ClarificationResolution.USE_DEFAULT.value
                if is_default
                else result.updates.get(
                    "clarification_resolution", ClarificationResolution.RESOLVED.value
                )
            )
            updates = dict(result.updates)
            if result.route == WorkflowRoute.ASK_QUESTION:
                updates["pending_request_base"] = (
                    result.updates.get("active_request")
                    or state.get("pending_request_base")
                    or request
                )
            else:
                updates["pending_request_base"] = None
            updates["assumptions"] = tuple(assumptions)
            updates["clarification_resolution"] = status
            updates["runtime_decisions"] = (
                *state.get("runtime_decisions", ()),
                self._runtime_decision(
                    decision,
                    reply,
                    ClarificationResolution.USE_DEFAULT
                    if is_default
                    else ClarificationResolution.RESOLVED,
                ),
            )
            route = result.route
            continuation = state.get("recovery_continuation")
            if (
                route != WorkflowRoute.ASK_QUESTION
                and continuation in {"plan", "modify"}
                and result.outcome != WorkflowOutcome.CONSTRAINT_PATCH_CONFLICT
                and result.updates.get("candidate_set") is None
            ):
                route = (
                    WorkflowRoute.MODIFY_PLAN
                    if continuation == "modify"
                    else WorkflowRoute.PLANNING
                )
                updates["recovery_continuation"] = None
                updates["pending_request_base"] = None
            # A clarification raised during CREATE must return to QuestionPolicy
            # before planning; an update to an existing request may plan
            # immediately once the patch is resolved.
            if (
                state.get("workflow_outcome") == WorkflowOutcome.CREATE.value
                and route in {WorkflowRoute.PLANNING, WorkflowRoute.END}
            ):
                route = WorkflowRoute.GATE
            # RequestWorkflow already chose ASK_QUESTION/PLANNING/END based on
            # the engine result; keep that route and only replace its outcome.
            return WorkflowResult(
                updates=updates,
                route=route,
                outcome=(
                    WorkflowOutcome.CREATE
                    if state.get("workflow_outcome") == WorkflowOutcome.CREATE.value
                    else result.outcome
                ),
            )

        outcome = self._resolver.resolve(
            pending_question=decision,
            reply=reply,
            base_interpretation=state["interpretation"],
            pending_modification=state.get("pending_modification"),
        )
        trace = self._runtime_decision(decision, reply, outcome.status)
        if outcome.status == ClarificationResolution.UNRESOLVED:
            return WorkflowResult(
                updates={
                    "pending_issue": self._prepare_question(outcome.question),
                    "clarification_resolution": outcome.status.value,
                    "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.CLARIFICATION,
            )
        return WorkflowResult(
            updates={
                "pending_issue": None,
                "interpretation": outcome.interpretation,
                "pending_modification": (
                    None
                    if outcome.status == ClarificationResolution.MODIFICATION_RESOLVED
                    else state.get("pending_modification")
                ),
                "clarification_resolution": outcome.status.value,
                "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
            },
            route=WorkflowRoute.MODIFY_PLAN,
            outcome=WorkflowOutcome.CLARIFICATION,
        )

    @staticmethod
    def _stale(
        code: str,
        field: str,
        request: PlanRequest,
        plan_version_id: str | None = None,
    ) -> WorkflowResult:
        conflict = ConstraintConflict(
            code=code,
            message=(
                "这条回复对应的问题已过期，请使用当前问题重新回答。"
                if field == "clarification_id"
                else "这条补充对应的问题已过期，请根据当前条件重新确认。"
            ),
            fields=[field],
        )
        return WorkflowResult(
            updates={
                "candidate_set": _candidate_set_for_conflict(
                    conflict,
                    request,
                    stage=RecoveryStage.CONSTRAINT_COMPILATION,
                    plan_version_id=plan_version_id,
                ),
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "clarification_resolution": "stale_rejected",
                "ready_for_planning": False,
            },
            route=WorkflowRoute.END,
            outcome=WorkflowOutcome.CONSTRAINT_PATCH_CONFLICT,
        )


class RecoveryWorkflow:
    """Apply one typed recovery decision/action and return a graph state delta.

    LangGraph owns interrupts and edges. This module owns the deterministic
    business transition behind recovery: validating the offered action,
    applying its typed patch, and projecting conflicts or field questions.
    """

    def __init__(
        self,
        *,
        policy: RecoveryPolicy,
        constraint_engine: ConstraintEngine,
        clarification_patch_compiler: ClarificationPatchCompiler,
        question_policy: QuestionPolicy,
        environment_provider: EnvironmentProvider,
        prepare_question: PrepareQuestion,
    ) -> None:
        self._policy = policy
        self._engine = constraint_engine
        self._patch_compiler = clarification_patch_compiler
        self._question_policy = question_policy
        self._environment_provider = environment_provider
        self._prepare_question = prepare_question
        self._reason_adapter = RecoveryReasonAdapter()

    def decide(self, state: DurableState) -> WorkflowResult:
        candidate_set = state.get("candidate_set")
        reason = state.get("active_recovery_reason") or (
            candidate_set.recovery_reason if candidate_set is not None else None
        )
        request = self._request_for(state)
        if not isinstance(reason, RecoveryReason):
            return WorkflowResult(
                updates={"active_recovery_reason": None},
                route=WorkflowRoute.END,
                outcome=WorkflowOutcome.RECOVERY,
            )

        plan_version_id = state.get("active_plan_version_id")
        if reason.plan_version_id is None and plan_version_id is not None:
            reason = reason.model_copy(update={"plan_version_id": plan_version_id})
        decision = self._policy.decide(
            reason,
            request,
            current_plan_version_id=plan_version_id,
        )
        used_actions = tuple(state.get("used_recovery_action_ids", ()))
        decision = decision.model_copy(
            update={
                "actions": tuple(
                    action for action in decision.actions
                    if action.action_id not in used_actions
                ),
                "auto_action": (
                    None
                    if state.get("auto_recovery_attempted", False)
                    else decision.auto_action
                ),
            }
        )
        if decision.stale:
            conflict = ConstraintConflict(
                code="STALE_RECOVERY_REASON",
                message="规划状态已更新，这组恢复操作已失效，请基于当前条件重新规划。",
                fields=["request_revision"],
            )
            return WorkflowResult(
                updates={
                    "candidate_set": CandidateSet(conflict=conflict),
                    "active_recovery_reason": None,
                    "pending_interaction": None,
                    "workflow_outcome": WorkflowOutcome.CONFLICT.value,
                },
                route=WorkflowRoute.END,
                outcome=WorkflowOutcome.CONFLICT,
            )

        auto_action = decision.auto_action
        auto_attempted = False
        auto_failed = False
        if auto_action is not None and auto_action.auto_eligible:
            auto_attempted = True
            checked = self._engine.apply(request, auto_action.patch)
            if isinstance(checked, ResolvedRequest):
                return WorkflowResult(
                    updates={
                        "active_recovery_reason": reason,
                        "recovery_auto_action": auto_action,
                        "recovery_prevalidated_request": checked.request,
                        "recovery_auto_failed": False,
                        "auto_recovery_attempted": True,
                        "used_recovery_action_ids": (*used_actions, auto_action.action_id),
                    },
                    route=WorkflowRoute.APPLY_RECOVERY_ACTION,
                    outcome=WorkflowOutcome.RECOVERY,
                )
            auto_failed = True
            decision = decision.model_copy(update={"auto_action": None})

        interaction = RecoveryChoiceInteraction(
            interaction_id=uuid.uuid4().hex,
            request_revision=request.revision,
            plan_version_id=plan_version_id,
            reason=reason,
            actions=decision.actions,
        )
        return WorkflowResult(
            updates={
                "active_recovery_reason": reason,
                "recovery_auto_action": None,
                "recovery_prevalidated_request": None,
                "recovery_auto_failed": auto_failed,
                "pending_interaction": interaction,
                "auto_recovery_attempted": (
                    state.get("auto_recovery_attempted", False) or auto_attempted
                ),
            },
            route=WorkflowRoute.INTERRUPT_FOR_RECOVERY,
            outcome=WorkflowOutcome.RECOVERY,
        )

    def apply_action(self, state: DurableState) -> WorkflowResult:
        automatic = state.get("recovery_auto_action")
        reason = state.get("active_recovery_reason")
        request = self._request_for(state)
        plan_version_id = state.get("active_plan_version_id")
        response = state.get("recovery_response")
        action: RecoveryAction | None

        if automatic is not None:
            if not isinstance(automatic, ApplyRequestPatchAction) or not automatic.auto_eligible:
                raise ValueError("automatic recovery action is not eligible")
            if automatic.request_revision != request.revision:
                raise ValueError("stale automatic recovery action reached execution")
            action = automatic
        else:
            interaction = state.get("pending_interaction")
            if (
                not isinstance(reason, RecoveryReason)
                or not isinstance(response, RecoveryActionResponse)
                or not isinstance(interaction, RecoveryChoiceInteraction)
            ):
                raise ValueError("recovery action requires a current response")
            if (
                response.request_revision != request.revision
                or response.plan_version_id != plan_version_id
                or response.plan_version_id != reason.plan_version_id
                or interaction.interaction_id != response.interaction_id
                or interaction.request_revision != response.request_revision
                or interaction.plan_version_id != response.plan_version_id
            ):
                raise ValueError("stale recovery action reached workflow execution")
            action = next(
                (item for item in interaction.actions if item.action_id == response.action_id),
                None,
            )
            if action is None:
                raise ValueError("recovery action id is not available")
            if (
                action.request_revision != request.revision
                or action.plan_version_id != plan_version_id
            ):
                raise ValueError("offered recovery action is stale")

        if isinstance(action, ApplyRequestPatchAction):
            checked_request = state.get("recovery_prevalidated_request")
            result = (
                ResolvedRequest(request=checked_request)
                if automatic is not None and isinstance(checked_request, PlanRequest)
                else self._engine.apply(request, action.patch)
            )
            return self._apply_patch_result(
                state,
                result,
                request=request,
                continuation=action.continuation,
                action_id=action.action_id,
                automatic=automatic is not None,
            )

        if isinstance(action, RequestFieldAction):
            if automatic is not None or not isinstance(response, RecoveryActionResponse):
                raise ValueError("field recovery action requires a user-provided value")
            if response.field_value is None:
                raise ValueError("field recovery action requires a field value")
            if action.field == "availability":
                patch = (
                    RequestPatch(
                        base_revision=request.revision,
                        set_fields={
                            "require_availability_confirmation": response.field_value == "必须确认"
                        },
                        source=ConstraintSource.USER_EXPLICIT,
                    )
                    if response.field_value in action.choices
                    else None
                )
            else:
                patch = self._patch_compiler.compile_answer(
                    field=action.field,
                    value=response.field_value,
                    request=request,
                    actor=state["actor"],
                    environment=self._environment_provider(state["actor"]),
                )
            if patch is None:
                return self._ask_for_field(state, action, request)
            return self._apply_patch_result(
                state,
                self._engine.apply(request, patch),
                request=request,
                continuation=action.continuation,
                action_id=action.action_id,
                automatic=False,
            )

        used = tuple(state.get("used_recovery_action_ids", ()))
        common = {
            "pending_interaction": None,
            "recovery_response": None,
            "recovery_auto_action": None,
            "recovery_prevalidated_request": None,
            "recovery_auto_failed": False,
            "active_recovery_reason": None,
            "pending_issue": None,
            "pending_patch": None,
            "pending_issues": (),
            "pending_request_base": None,
            "recovery_continuation": None,
            "used_recovery_action_ids": (
                used if automatic is not None else (*used, action.action_id)
            ),
        }
        if isinstance(action, ReplanCurrentRequestAction):
            return WorkflowResult(
                updates={
                    **common,
                    "candidate_set": None,
                    "recovery_base_request": None,
                    "recovery_resolution": action.kind,
                },
                route=WorkflowRoute.PLANNING,
                outcome=WorkflowOutcome.RECOVERY_ACTION_APPLIED,
            )
        if isinstance(action, OpenConstraintEditorAction):
            # The failed request remains an uncommitted draft. The next typed
            # edit uses it as its base; only a resolved Engine result promotes it.
            return WorkflowResult(
                updates={
                    **common,
                    "candidate_set": None,
                    "recovery_resolution": action.kind,
                },
                route=WorkflowRoute.END,
                outcome=WorkflowOutcome.RECOVERY_ACTION_FINISHED,
            )

        updates: dict[str, object] = {
            **common,
            "candidate_set": None,
            "recovery_base_request": None,
            "recovery_resolution": action.kind,
        }
        if isinstance(action, StartNewRequestAction):
            updates.update(
                {
                    "active_request": PlanRequest(),
                    "has_plans": False,
                    "selected_plan": None,
                    "active_plan_version_id": None,
                    "new_request_pending": True,
                }
            )
        elif not isinstance(action, (KeepCurrentPlanAction, CancelTurnAction)):
            raise ValueError("unsupported recovery action")
        return WorkflowResult(
            updates=updates,
            route=WorkflowRoute.END,
            outcome=WorkflowOutcome.RECOVERY_ACTION_FINISHED,
        )

    def _apply_patch_result(
        self,
        state: DurableState,
        result: object,
        *,
        request: PlanRequest,
        continuation: str,
        action_id: str,
        automatic: bool,
    ) -> WorkflowResult:
        used = tuple(state.get("used_recovery_action_ids", ()))
        used_actions = used if automatic else (*used, action_id)
        common = {
            "recovery_response": None,
            "recovery_auto_action": None,
            "recovery_prevalidated_request": None,
            "pending_interaction": None,
            "used_recovery_action_ids": used_actions,
        }
        if isinstance(result, ResolvedRequest):
            return WorkflowResult(
                updates={
                    **common,
                    "active_request": result.request,
                    "recovery_base_request": None,
                    "active_recovery_reason": None,
                    "candidate_set": None,
                    "pending_issue": None,
                    "pending_patch": None,
                    "pending_issues": (),
                    "pending_request_base": None,
                    "recovery_continuation": None,
                    "recovery_resolution": None,
                },
                route=(
                    WorkflowRoute.MODIFY_PLAN
                    if continuation == "modify"
                    else WorkflowRoute.PLANNING
                ),
                outcome=WorkflowOutcome.RECOVERY_ACTION_APPLIED,
            )

        if isinstance(result, ConstraintNeedsClarification):
            return self._ask_for_field(
                state,
                RequestFieldAction(
                    action_id=action_id,
                    kind="request_field",
                    label="补充规划条件",
                    description="",
                    request_revision=request.revision,
                    plan_version_id=state.get("active_plan_version_id"),
                    continuation=continuation,
                    field=result.issue.field,
                    input_type="text",
                ),
                result.request or request,
                issue=result.issue,
                action_consumed=not automatic,
            )

        conflict = (
            result.conflict
            if isinstance(result, ConflictedRequest)
            else ConstraintConflict(
                code="RECOVERY_PATCH_REJECTED",
                message="这项调整未通过约束检查，原条件保持不变。",
                fields=[],
            )
        )
        candidate_request = (
            result.candidate_request
            if isinstance(result, ConflictedRequest)
            else None
        )
        reason = self._reason_adapter.from_conflict(
            conflict,
            candidate_request or request,
            stage=RecoveryStage.CONSTRAINT_COMPILATION,
            plan_version_id=state.get("active_plan_version_id"),
        )
        return WorkflowResult(
            updates={
                **common,
                "candidate_set": CandidateSet(conflict=conflict, recovery_reason=reason),
                "recovery_base_request": candidate_request or request,
                "active_recovery_reason": reason,
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "pending_request_base": None,
                "recovery_continuation": None,
            },
            route=WorkflowRoute.DECIDE_RECOVERY,
            outcome=WorkflowOutcome.RECOVERY_ACTION_REJECTED,
        )

    def _ask_for_field(
        self,
        state: DurableState,
        action: RequestFieldAction,
        request: PlanRequest,
        *,
        issue: ClarificationIssue | None = None,
        action_consumed: bool = True,
    ) -> WorkflowResult:
        field_issue = issue or ClarificationIssue(
            field=action.field,
            code="RECOVERY_FIELD_VALUE_REQUIRED",
            reason="recovery_field_value_not_resolved",
            expected_value_type=action.input_type,
            request_revision=request.revision,
            allow_free_text=action.input_type != "choice",
        )
        question = self._question_policy.decide(
            Intent.REFINE_PLAN,
            request,
            QuestionPolicyContext(has_plans=bool(state.get("has_plans", False))),
            issue=field_issue,
        ).model_copy(
            update={
                "max_attempts": 2,
                "allow_free_text": (
                    field_issue.allow_free_text and action.input_type != "choice"
                ),
            }
        )
        decision = self._prepare_question(question)
        used = tuple(state.get("used_recovery_action_ids", ()))
        updates: dict[str, object] = {
            "recovery_base_request": state.get("recovery_base_request"),
            "pending_request_base": request,
            "recovery_continuation": action.continuation,
            "candidate_set": None,
            "active_recovery_reason": None,
            "recovery_response": None,
            "recovery_auto_action": None,
            "recovery_prevalidated_request": None,
            "pending_interaction": None,
            "pending_issue": decision,
            "pending_patch": RequestPatch(
                base_revision=request.revision,
                source=ConstraintSource.USER_EXPLICIT,
            ),
            "pending_issues": (issue,) if issue is not None else (),
            "used_recovery_action_ids": (
                used if not action_consumed else (*used, action.action_id)
            ),
        }
        return WorkflowResult(
            updates=updates,
            route=WorkflowRoute.ASK_QUESTION,
            outcome=WorkflowOutcome.CLARIFICATION,
        )

    @staticmethod
    def _request_for(state: DurableState) -> PlanRequest:
        request = (
            state.get("recovery_base_request")
            or state.get("active_request")
        )
        return request if isinstance(request, PlanRequest) else PlanRequest()

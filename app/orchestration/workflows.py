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

from langgraph.types import interrupt

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
from app.domain.runtime import RuntimeDecision
from app.services.clarification import ClarificationResolution, ClarificationResolver
from app.services.clarification_patch import ClarificationPatchCompiler, merge_request_patches
from app.services.constraint_engine import (
    ConflictedRequest,
    ConstraintEngine,
    NeedsClarification as ConstraintNeedsClarification,
)
from app.services.enrichment import EnvironmentContext, EnrichmentService
from app.services.planning import PlanningService
from app.services.question_policy import QuestionPolicy, QuestionPolicyContext
from app.services.request_patch_compiler import RequestPatchProposalCompiler
from app.services.request_patch_update import RequestPatchUpdateCompiler
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


@dataclass(frozen=True)
class WorkflowResult:
    """Small graph-facing interface for a workflow execution."""

    updates: dict[str, object] = field(default_factory=dict)
    route: WorkflowRoute = WorkflowRoute.END
    outcome: WorkflowOutcome | None = None

    def as_state(self) -> dict[str, object]:
        result = dict(self.updates)
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
            return WorkflowResult(
                updates={
                    **common,
                    "candidate_set": CandidateSet(conflict=outcome.conflict),
                    "active_request": PlanRequest(),
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
        active_request = state.get("active_request")
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
        )
        result = self._engine.apply(
            active_request,
            compilation.patch,
            issues=compilation.issues,
        )
        if isinstance(result, ConstraintNeedsClarification):
            question = self._prepare_question(
                self._question_policy.decide(
                    intent_for_action(action),
                    result.request or active_request,
                    QuestionPolicyContext(
                        has_plans=bool(state.get("has_plans", False)),
                        selected_plan_index=state["interpretation"].selected_plan_index,
                    ),
                    issue=result.issue,
                )
            )
            return WorkflowResult(
                updates={
                    **({"active_request": result.request} if result.request is not None else {}),
                    "pending_issue": question,
                    "pending_patch": None if result.request is not None else compilation.patch,
                    "pending_issues": () if result.request is not None else compilation.issues or (result.issue,),
                },
                route=WorkflowRoute.ASK_QUESTION,
                outcome=WorkflowOutcome.CONSTRAINT_PATCH,
            )
        if isinstance(result, ConflictedRequest):
            return WorkflowResult(
                updates={
                    "candidate_set": CandidateSet(conflict=result.conflict),
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
                    **({"active_request": result.request} if result.request is not None else {}),
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
            return WorkflowResult(
                updates={
                    "candidate_set": CandidateSet(conflict=result.conflict),
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

    def execute(self, state: DurableState) -> WorkflowResult:
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
            "candidate_set": outcome.candidate_set,
            "plan_diffs": outcome.plan_diffs,
            "pending_issue": prepared,
            "pending_modification": pending,
            "runtime_decisions": (
                *state.get("runtime_decisions", ()),
                outcome.runtime_decision,
                *(
                    (outcome.candidate_set.retrieval_runtime_decision,)
                    if (
                        outcome.candidate_set is not None
                        and outcome.candidate_set.retrieval_runtime_decision is not None
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
        question_payload: Callable[[QuestionDecision], dict[str, object]],
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
        self._question_payload = question_payload
        self._coerce_reply = coerce_reply
        self._runtime_decision = runtime_decision
        self._resolved_request_value = resolved_request_value
        self._patch_resolves_issue = patch_resolves_issue

    def resume(self, state: DurableState) -> WorkflowResult:
        decision = state.get("pending_issue")
        if decision is None:
            raise ValueError("ask_question requires a QuestionDecision")
        answer = interrupt(self._question_payload(decision))
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
        request = state.get("active_request") or PlanRequest()
        expected_revision = decision.request_revision
        if decision.clarification_id and reply.clarification_id != decision.clarification_id:
            return self._stale("STALE_CLARIFICATION_ID", "clarification_id")
        if expected_revision is not None and (
            request.revision != expected_revision
            or reply.request_revision != expected_revision
        ):
            return self._stale("STALE_CLARIFICATION_REVISION", "request_revision")

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
    def _stale(code: str, field: str) -> WorkflowResult:
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
                "candidate_set": CandidateSet(conflict=conflict),
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "clarification_resolution": "stale_rejected",
                "ready_for_planning": False,
            },
            route=WorkflowRoute.END,
            outcome=WorkflowOutcome.CONSTRAINT_PATCH_CONFLICT,
        )

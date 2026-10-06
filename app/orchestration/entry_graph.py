"""HappyFreeTime V2 的 LangGraph 编排层。

创建链路为：TurnInterpreter -> compile_request -> QuestionPolicy -> Planning；定向修改链路从
TurnInterpreter 进入单个 modify_plan 节点。Gate 判断需要反问时，进入
ask_question 并通过 interrupt 暂停；用户下一条消息通过 Command(resume) 恢复，
约束反问由字段级 RequestPatch 更新 PlanRequest；非约束目标澄清仍由专用解析器处理。
Graph 只负责节点顺序和状态，具体业务逻辑仍位于可独立测试的 service 中。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol, TypedDict
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from app.domain.constraints import (
    ActorContext,
    Assumption,
    ClarificationAction,
    ClarificationOption,
    ClarificationReply,
    CommandOperation,
    ConstraintPatch,
    ConstraintValue,
    ClarificationIssue,
    CriterionStrength,
    ConversationCommand,
    ConstraintSource,
    DateReference,
    IdentityType,
    Intent,
    Interpretation,
    PlanRequest,
    PlanningWindow,
    RequestPatch,
    PendingModification,
    QuestionDecision,
    RouteObjective,
    SemanticCriterion,
    StopRole,
    TargetReference,
    TimeScope,
    TimeProposal,
    Weekday,
)
from app.domain.planning import (
    CandidateSet,
    ConstraintConflict,
    LockedStop,
    Plan,
    PlanDiff,
    PlanPace,
    PlanSlotProposal,
    PlanStructureProposal,
    PlanningIntent,
    PlanningIntentDecision,
    PlanPriceStatus,
    PlanWarning,
    PlanStrategy,
    StopReplacement,
    StopType,
)


CHECKPOINT_SCHEMA_VERSION = "planner-core2e-v1"


def checkpoint_config(session_id: str) -> dict[str, dict[str, str]]:
    """Use a versioned thread key after the intentional checkpoint schema break."""

    return {
        "configurable": {
            "thread_id": f"{CHECKPOINT_SCHEMA_VERSION}:{session_id}",
        }
    }
from app.domain.providers import (
    AvailabilityFact,
    AvailabilityStatus,
    GeocodeRequest,
    GeocodeResolution,
    GeocodingFact,
    GeoPoint,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteMode,
    RouteSource,
    WeatherFact,
)
from app.domain.runtime import RuntimeDecision
from app.domain.decision_context import DecisionContextBuilder
from app.domain.semantics import (
    EvidenceRef,
    SemanticQuery,
    SemanticRequest,
    SoftObjective,
    SoftObjectiveKind,
)
from app.domain.catalog import (
    CatalogWarningCode,
    CatalogSource,
    ConstraintViolation,
    PriceKind,
    ResourceType,
    VerificationStatus,
    ViolationCode,
)
from app.providers.route import RouteProvider
from app.providers.availability import AvailabilityProvider
from app.providers.weather import WeatherProvider
from app.providers.geocoding import GeocodingProvider
from app.services.catalog import Catalog
from app.services.enrichment import EnvironmentContext, EnrichmentService
from app.services.planning import PlanningService
from app.services.candidate_retriever import CandidateRetriever
from app.services.planning_intent import PlanningIntentProvider
from app.services.question_policy import QuestionPolicyContext, QuestionPolicy
from app.services.clarification import ClarificationResolution, ClarificationResolver
from app.services.clarification_patch import ClarificationPatchCompiler, merge_request_patches
from app.services.request_patch_update import RequestPatchUpdateCompiler
from app.services.constraint_engine import (
    ConflictedRequest,
    ConstraintEngine,
    NeedsClarification,
    ResolvedRequest,
)
from app.services.request_patch_compiler import RequestPatchProposalCompiler
from app.services.router_extractor import RouterContext


class TurnInterpreter(Protocol):
    """真实 LLM 与离线 Demo Adapter 共同满足的语义解释接口。"""
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        ...


# Compatibility name for existing composition roots and third-party callers.
Router = TurnInterpreter


class EntryState(TypedDict, total=False):
    """Graph 运行状态。

    ``total=False`` 是因为不同节点只拥有部分字段；每个节点返回增量更新，
    LangGraph 将其合并进同一 thread 的 checkpoint。
    """
    user_input: str
    actor: ActorContext
    interpretation: Interpretation | None
    assumptions: tuple[Assumption, ...]
    geocoding_fact: GeocodingFact | None
    active_request: PlanRequest | None
    pending_patch: RequestPatch | None
    pending_issues: tuple[ClarificationIssue, ...]
    pending_issue: QuestionDecision | None
    candidate_set: CandidateSet | None
    ready_for_planning: bool
    condition_requests_plan: bool
    has_plans: bool
    active_plan_version_id: str | None
    selected_plan: Plan | None
    plan_diffs: tuple[PlanDiff, ...]
    conversation_command_override: ConversationCommand | None
    request_patch_override: RequestPatch | None
    request_patch_issues: tuple[ClarificationIssue, ...]
    defer_planning: bool
    replan_current_request: bool
    runtime_decisions: tuple[RuntimeDecision, ...]
    clarification_resolution: str | None
    pending_modification: PendingModification | None
    mutation_kind: str | None


EnvironmentProvider = Callable[[ActorContext], EnvironmentContext]


def build_entry_graph(
    *,
    router: TurnInterpreter,
    environment_provider: EnvironmentProvider,
    weather_provider: WeatherProvider | None = None,
    route_provider: RouteProvider | None = None,
    geocoding_provider: GeocodingProvider | None = None,
    availability_provider: AvailabilityProvider | None = None,
    catalog: Catalog | None = None,
    planning_intent_provider: PlanningIntentProvider | None = None,
    candidate_retriever: CandidateRetriever | None = None,
    checkpointer: object | None = None,
):
    """组装 M1 控制流，并允许注入 Router、环境提供器和 checkpointer。

    依赖注入让测试可以使用规则 Router、固定时间和内存 checkpoint；生产 API
    则使用真实 Router、系统时间和 SQLite checkpoint，但 Graph 本身无需分叉。
    """
    enrichment_service = EnrichmentService(geocoding_provider=geocoding_provider)
    request_patch_compiler = RequestPatchProposalCompiler()
    constraint_engine = ConstraintEngine()
    question_policy = QuestionPolicy()
    clarification_resolver = ClarificationResolver()
    request_patch_update_compiler = RequestPatchUpdateCompiler(
        geocoding_provider=geocoding_provider,
    )
    clarification_patch_compiler = ClarificationPatchCompiler(request_patch_update_compiler)
    planning_service = PlanningService(
        weather_provider=weather_provider,
        route_provider=route_provider,
        availability_provider=availability_provider,
        catalog=catalog,
        planning_intent_provider=planning_intent_provider,
        candidate_retriever=candidate_retriever,
    )

    def router_node(state: EntryState) -> dict[str, object]:
        actor = state["actor"]
        structured_command = state.get("conversation_command_override")
        if structured_command is not None:
            # UI commands already passed Pydantic validation and carry an
            # explicit selected-plan target.  They do not need an LLM round trip;
            # the command still goes through the same service authorization and
            # verification checks as a natural-language command.
            interpretation = Interpretation(
                primary_intent=(
                    Intent.REFINE_PLAN
                    if structured_command.operation in {
                        CommandOperation.REPLACE,
                        CommandOperation.PATCH_CONSTRAINTS,
                    }
                    else Intent.CLARIFY
                ),
                intent_scores={
                    (
                        Intent.REFINE_PLAN
                        if structured_command.operation in {
                            CommandOperation.REPLACE,
                            CommandOperation.PATCH_CONSTRAINTS,
                        }
                        else Intent.CLARIFY
                    ): 1.0
                },
                conversation_command=structured_command,
                reply=(
                    "正在按选中方案替换一个站点。"
                    if structured_command.operation == CommandOperation.REPLACE
                    else "当前结构化操作暂不支持。"
                ),
                requires_clarification=(
                    structured_command.operation != CommandOperation.REPLACE
                ),
            )
            runtime_decision = RuntimeDecision(
                stage="turn_interpreter",
                adapter="bypassed",
                model_invoked=False,
                model_name=None,
                attempts=0,
                fallback_reason="structured_command",
                latency_ms=0,
            )
        else:
            environment = environment_provider(actor)
            previous_interpretation = state.get("interpretation")
            decision_context = DecisionContextBuilder.build(
                request=state.get("active_request"),
                selected_plan=state.get("selected_plan"),
                active_plan_version_id=state.get("active_plan_version_id"),
                pending_issue=state.get("pending_issue"),
                pending_modification=state.get("pending_modification"),
                has_plans=state.get("has_plans", False),
                previous_intent=(
                    previous_interpretation.primary_intent
                    if previous_interpretation is not None
                    else None
                ),
                last_system_outcome=state.get("mutation_kind"),
            )
            interpretation, runtime_decision = _interpret_with_runtime(
                router,
                state["user_input"],
                RouterContext(
                    current_date=environment.now.date(),
                    timezone=actor.timezone,
                    has_plans=state.get("has_plans", False),
                    has_selected_plan=state.get("selected_plan") is not None,
                    previous_intent=(
                        previous_interpretation.primary_intent
                        if previous_interpretation
                        else None
                    ),
                    decision_context=decision_context,
                ),
            )
        # 这些派生值只属于当前解释轮次。反问恢复或用户修改需求时必须清空，
        # 否则新请求可能误用上一轮的假设、Gate 决策或候选方案。
        return {
            "interpretation": interpretation,
            "assumptions": (),
            "geocoding_fact": None,
            "pending_patch": None,
            "pending_issues": (),
            "pending_issue": None,
            "candidate_set": None,
            "ready_for_planning": False,
            "condition_requests_plan": False,
            "plan_diffs": (),
            "conversation_command_override": None,
            "runtime_decisions": (runtime_decision,),
            "clarification_resolution": None,
            "pending_modification": None,
            "mutation_kind": None,
        }

    def route_after_router(state: EntryState) -> str:
        """闲聊和无法可靠理解的输入直接结束，不浪费后续规划计算。"""
        interpretation = state["interpretation"]
        intent = interpretation.primary_intent
        if intent in {Intent.CHITCHAT, Intent.CLARIFY}:
            return END
        if intent == Intent.CHECK_WEATHER:
            return "compile_request"
        command = state["interpretation"].conversation_command
        if command is not None and command.operation == CommandOperation.PATCH_CONSTRAINTS:
            return "compile_patch"
        if intent == Intent.REFINE_PLAN or (
            command is not None
            and command.operation in {
                CommandOperation.REPLACE,
            }
        ):
            return "modify_plan"
        return "compile_request"

    def modify_plan_node(state: EntryState) -> dict[str, object]:
        interpretation = state["interpretation"]
        command = interpretation.conversation_command
        if command is None or command.operation != CommandOperation.REPLACE:
            if interpretation.target_reference:
                question = _prepare_question_decision(
                    QuestionDecision(
                        need_question=True,
                        field="target_reference",
                        question="要替换哪一站？可以输入“活动”“晚餐”或“第二站”。",
                        severity="blocking",
                        issue_kind="target",
                        request_revision=(
                            state["active_request"].revision
                            if state.get("active_request") is not None
                            else 0
                        ),
                        rule_id="question.target_reference.v1",
                    )
                )
                return {
                    "pending_issue": question,
                    "pending_modification": PendingModification(
                        target_raw_text=interpretation.target_reference,
                        evidence=interpretation.evidence_map,
                    ),
                }
            question = _prepare_question_decision(
                    QuestionDecision(
                        need_question=True,
                        field="conversation_command",
                        question="要替换哪一站？可以输入“活动”“晚餐”或“第二站”。",
                        severity="blocking",
                        issue_kind="target",
                        request_revision=(
                            state["active_request"].revision
                            if state.get("active_request") is not None
                            else 0
                        ),
                        rule_id="question.target_reference.v1",
                )
            )
            return {
                "pending_issue": question,
                "pending_modification": PendingModification(),
            }
        selected_plan = state.get("selected_plan")
        active_constraints = state.get("active_request")
        if selected_plan is None:
            return {
                "pending_issue": _prepare_question_decision(QuestionDecision(
                    need_question=True,
                    field="selected_plan_id",
                    question="请先选择一个方案，再告诉我要保留和替换哪一站。",
                    severity="blocking",
                    issue_kind="selection",
                    request_revision=active_constraints.revision if active_constraints else 0,
                ))
            }
        if active_constraints is None:
            return {
                "pending_issue": _prepare_question_decision(QuestionDecision(
                    need_question=True,
                    field="active_plan_version_id",
                    question="当前方案缺少可恢复的约束快照，请重新生成并选择方案。",
                    severity="blocking",
                    issue_kind="selection",
                    allow_free_text=False,
                    request_revision=0,
                ))
            }
        outcome = planning_service.modify_selected_plan(
            selected_plan=selected_plan,
            constraints=active_constraints,
            command=command,
        )
        question = outcome.question
        if question is not None:
            updates = {"request_revision": active_constraints.revision}
            if question.field in {"target_reference", "locked_stop"}:
                updates["issue_kind"] = "target"
            question = question.model_copy(update=updates)
        prepared_question = _prepare_question_decision(question) if question is not None else None
        pending = None
        if prepared_question is not None and prepared_question.field in {"target_reference", "locked_stop"}:
            pending = PendingModification(
                target_raw_text=command.target.raw_text if command.target else None,
                locked_targets=command.locked_targets,
                constraint_patch=command.constraint_patch,
                replacement_criteria=command.replacement_criteria,
                evidence=command.evidence,
            )
        return {
            "candidate_set": outcome.candidate_set,
            "plan_diffs": outcome.plan_diffs,
            "pending_issue": prepared_question,
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

    def compile_patch_node(state: EntryState) -> dict[str, object]:
        """Compile one PATCH_CONSTRAINTS proposal before re-entering planning."""
        command = state["interpretation"].conversation_command
        active_constraints = state.get("active_request")
        if command is None or command.operation != CommandOperation.PATCH_CONSTRAINTS:
            raise ValueError("compile_patch requires PATCH_CONSTRAINTS")
        if active_constraints is None:
            question = _prepare_question_decision(
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
            return {
                "pending_issue": question,
            }
        actor = state["actor"]
        compilation = request_patch_update_compiler.compile_update_proposal(
            base=active_constraints,
            proposal=command.constraint_patch,
            actor=actor,
            environment=environment_provider(actor),
        )
        outcome = constraint_engine.apply(
            active_constraints,
            compilation.patch,
            issues=compilation.issues,
        )
        if isinstance(outcome, NeedsClarification):
            prepared = _prepare_question_decision(
                question_policy.decide(
                    state["interpretation"].primary_intent,
                    outcome.request or active_constraints,
                    QuestionPolicyContext(
                        has_plans=state.get("has_plans", False),
                        selected_plan_index=state["interpretation"].selected_plan_index,
                    ),
                    issue=outcome.issue,
                )
            )
            return {
                **(
                    {"active_request": outcome.request}
                    if outcome.request is not None
                    else {}
                ),
                "pending_issue": prepared,
                "pending_patch": (
                    None if outcome.request is not None else compilation.patch
                ),
                "pending_issues": (
                    () if outcome.request is not None
                    else compilation.issues or (outcome.issue,)
                ),
                "mutation_kind": "constraint_patch",
            }
        if isinstance(outcome, ConflictedRequest):
            return {
                "candidate_set": CandidateSet(conflict=outcome.conflict),
                "mutation_kind": "constraint_patch_conflict",
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
            }
        return {
            "active_request": outcome.request,
            "mutation_kind": "constraint_patch",
            "pending_issue": None,
            "pending_patch": None,
            "pending_issues": (),
        }

    def apply_request_patch_state(
        state: EntryState,
        request: PlanRequest,
        patch: RequestPatch,
        issues: tuple[ClarificationIssue, ...] = (),
        *,
        existing_decision: QuestionDecision | None = None,
        resumed: bool = False,
    ) -> dict[str, object]:
        """Apply a typed UI edit through the same Engine as Router/Patch paths."""
        outcome = constraint_engine.apply(request, patch, issues=issues)
        if isinstance(outcome, NeedsClarification):
            candidate = outcome.request or request
            if (
                existing_decision is not None
                and existing_decision.field == outcome.issue.field
                and existing_decision.request_revision == candidate.revision
            ):
                question = existing_decision
            else:
                interpretation = state.get("interpretation") or Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    reply="正在按编辑后的条件规划。",
                )
                question = _prepare_question_decision(
                    question_policy.decide(
                        interpretation.primary_intent,
                        candidate,
                        QuestionPolicyContext(
                            has_plans=state.get("has_plans", False),
                            selected_plan_index=interpretation.selected_plan_index,
                        ),
                        issue=outcome.issue,
                    )
                )
            return {
                **({"active_request": outcome.request} if outcome.request is not None else {}),
                "pending_issue": question,
                "pending_patch": patch if outcome.request is None else None,
                "pending_issues": issues or (outcome.issue,),
                "mutation_kind": "constraint_patch",
                "clarification_resolution": (
                    ClarificationResolution.UNRESOLVED.value if resumed else None
                ),
                "ready_for_planning": False,
            }
        if isinstance(outcome, ConflictedRequest):
            return {
                "candidate_set": CandidateSet(conflict=outcome.conflict),
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "mutation_kind": "constraint_patch_conflict",
                "clarification_resolution": "conflict" if resumed else None,
                "ready_for_planning": False,
            }
        changed = bool(outcome.changed_fields)
        return {
            "active_request": outcome.request,
            "candidate_set": None,
            "pending_issue": None,
            "pending_patch": None,
            "pending_issues": (),
            "mutation_kind": "constraint_patch" if changed else "constraint_patch_noop",
            "clarification_resolution": (
                ClarificationResolution.RESOLVED.value if resumed else None
            ),
            "ready_for_planning": changed,
        }

    def apply_request_patch_node(state: EntryState) -> dict[str, object]:
        patch = state.get("request_patch_override")
        if patch is None:
            raise ValueError("apply_request_patch requires a typed RequestPatch")
        return {
            "request_patch_override": None,
            "interpretation": state.get("interpretation") or Interpretation(
                primary_intent=Intent.REFINE_PLAN,
                intent_scores={Intent.REFINE_PLAN: 1.0},
                reply="条件没有变化，无需重新规划。",
            ),
            **apply_request_patch_state(
                state,
                state.get("active_request") or PlanRequest(),
                patch,
                state.get("request_patch_issues", ()),
            ),
        }

    def replan_current_request_node(state: EntryState) -> dict[str, object]:
        if state.get("active_request") is None:
            raise ValueError("replan_current_request requires an active PlanRequest")
        return {
            "interpretation": Interpretation(
                primary_intent=Intent.REFINE_PLAN,
                intent_scores={Intent.REFINE_PLAN: 1.0},
                reply="正在按已保存的规划条件重新生成方案。",
            ),
            "candidate_set": None,
            "pending_issue": None,
            "pending_patch": None,
            "pending_issues": (),
            "mutation_kind": "constraint_patch",
            "ready_for_planning": True,
        }

    def patch_resolves_issue(
        patch: RequestPatch,
        issue: ClarificationIssue,
        request: PlanRequest,
    ) -> bool:
        """Whether a typed edit supplied the value required by a pending issue."""
        fields = patch.set_fields
        if issue.field == "location":
            return fields.get("location") is not None
        if issue.field == "date":
            return fields.get("planning_window.date") is not None
        if issue.field == "departure_at":
            return (
                fields.get("planning_window.start_at") is not None
                and fields.get("planning_window.start_kind", request.planning_window.start_kind)
                == "departure"
            )
        if issue.field == "return_by":
            return (
                fields.get("planning_window.end_at") is not None
                and fields.get("planning_window.end_kind", request.planning_window.end_kind)
                == "return_deadline"
            )
        if issue.field == "time_window":
            start_present = (
                "planning_window.start_at" in fields
                or (
                    request.planning_window.start_at is not None
                    and "planning_window.start_at" not in patch.clear_fields
                )
            )
            end_present = (
                "planning_window.end_at" in fields
                or (
                    request.planning_window.end_at is not None
                    and "planning_window.end_at" not in patch.clear_fields
                )
            )
            return start_present and end_present
        if issue.field == "budget_per_person":
            return fields.get("budget_per_person") is not None
        if issue.field in {"party", "child_age"}:
            return fields.get("party") is not None
        return False

    def compile_request_node(state: EntryState) -> dict[str, object]:
        environment = environment_provider(state["actor"])
        enrichment = enrichment_service.enrich(
            state["interpretation"],
            state["actor"],
            environment,
        )
        compilation = request_patch_compiler.compile(
            state["interpretation"],
            enrichment.request_patch,
            environment,
        )
        outcome = constraint_engine.apply(
            PlanRequest(),
            compilation.patch,
            issues=compilation.issues,
        )
        assumptions = tuple((*enrichment.assumptions, *compilation.assumptions))
        compiled_context = {
            "assumptions": assumptions,
            "geocoding_fact": enrichment.geocoding_fact,
            "condition_requests_plan": compilation.condition_requests_plan,
        }
        if isinstance(outcome, NeedsClarification):
            issues = compilation.issues or (outcome.issue,)
            question = _prepare_question_decision(
                question_policy.decide(
                    state["interpretation"].primary_intent,
                    outcome.request or PlanRequest(),
                    QuestionPolicyContext(
                        has_plans=state.get("has_plans", False),
                        selected_plan_index=state["interpretation"].selected_plan_index,
                    ),
                    issue=outcome.issue,
                )
            )
            return {
                **compiled_context,
                "active_request": outcome.request or PlanRequest(),
                "pending_patch": None if outcome.request is not None else compilation.patch,
                "pending_issues": () if outcome.request is not None else issues,
                "pending_issue": question,
                "mutation_kind": "create",
            }
        if isinstance(outcome, ConflictedRequest):
            return {
                **compiled_context,
                "candidate_set": CandidateSet(conflict=outcome.conflict),
                "active_request": PlanRequest(),
                "pending_patch": None,
                "pending_issues": (),
                "pending_issue": None,
            }
        return {
            **compiled_context,
            "active_request": outcome.request,
            "pending_patch": None,
            "pending_issues": (),
            "pending_issue": None,
            "mutation_kind": "create",
        }

    def gate_node(state: EntryState) -> dict[str, object]:
        existing_issue = state.get("pending_issue")
        if existing_issue is not None and existing_issue.need_question:
            return {"ready_for_planning": False}
        decision = question_policy.decide(
            state["interpretation"].primary_intent,
            state.get("active_request") or PlanRequest(),
            QuestionPolicyContext(
                has_plans=state.get("has_plans", False),
                selected_plan_index=state["interpretation"].selected_plan_index,
            ),
        )
        # 只有真正需要产出/修改方案的意图才能进入 Planner。天气查询、执行和
        # 取消等意图即使字段完整，也不应错误触发双站规划。
        planning_intents = {
            Intent.PLAN_OUTING,
            Intent.FIND_ACTIVITY,
            Intent.REFINE_PLAN,
        }
        weather_condition_planning = (
            state["interpretation"].primary_intent == Intent.CHECK_WEATHER
            and state.get("condition_requests_plan", False)
        )
        return {
            "pending_issue": (
                _prepare_question_decision(decision)
                if decision.need_question
                else None
            ),
            "pending_patch": (
                RequestPatch(
                    base_revision=(state.get("active_request") or PlanRequest()).revision,
                    source=ConstraintSource.USER_EXPLICIT,
                )
                if decision.need_question
                else None
            ),
            "pending_issues": (),
            "ready_for_planning": (
                not decision.need_question
                and not (state.get("candidate_set") and state["candidate_set"].conflict)
                and (
                    state["interpretation"].primary_intent in planning_intents
                    or weather_condition_planning
                )
            ),
        }

    def route_after_gate(state: EntryState) -> str:
        decision = state.get("pending_issue")
        if decision is not None and decision.need_question:
            return "ask_question"
        if state.get("ready_for_planning", False):
            return "planning"
        return END

    def planning_node(state: EntryState) -> dict[str, object]:
        constraints = state["active_request"]
        candidate_set = planning_service.plan(constraints)
        geocoding_fact = state.get("geocoding_fact")
        if geocoding_fact is not None:
            candidate_set = candidate_set.model_copy(
                update={"provider_facts": [geocoding_fact, *candidate_set.provider_facts]}
            )
        return {
            "candidate_set": candidate_set,
            "ready_for_planning": True,
            "runtime_decisions": (
                *state.get("runtime_decisions", ()),
                *(
                    (candidate_set.runtime_decision,)
                    if candidate_set.runtime_decision is not None
                    else ()
                ),
                *(
                    (candidate_set.retrieval_runtime_decision,)
                    if candidate_set.retrieval_runtime_decision is not None
                    else ()
                ),
            ),
        }

    def ask_question_node(state: EntryState) -> dict[str, object]:
        decision = state["pending_issue"]
        if decision is None:
            raise ValueError("ask_question requires a QuestionDecision")
        answer = interrupt(_question_payload(decision))
        context_patch_answer = (
            answer if isinstance(answer, dict) and answer.get("kind") == "planning_context_patch" else None
        )
        if context_patch_answer is not None:
            reply = ClarificationReply(
                clarification_id=str(context_patch_answer.get("clarification_id") or ""),
                action=ClarificationAction.ANSWER,
                request_revision=context_patch_answer.get("request_revision"),
            )
        else:
            reply = _coerce_clarification_reply(answer)
        request = state.get("active_request") or PlanRequest()
        expected_revision = decision.request_revision
        if decision.clarification_id and reply.clarification_id != decision.clarification_id:
            conflict = ConstraintConflict(
                code="STALE_CLARIFICATION_ID",
                message="这条回复对应的问题已过期，请使用当前问题重新回答。",
                fields=["clarification_id"],
            )
            return {
                "candidate_set": CandidateSet(conflict=conflict),
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "clarification_resolution": "stale_rejected",
                "ready_for_planning": False,
            }
        if (
            expected_revision is not None
            and (
                request.revision != expected_revision
                or reply.request_revision != expected_revision
            )
        ):
            conflict = ConstraintConflict(
                code="STALE_CLARIFICATION_REVISION",
                message="这条补充对应的问题已过期，请根据当前条件重新确认。",
                fields=["request_revision"],
            )
            return {
                "candidate_set": CandidateSet(conflict=conflict),
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "clarification_resolution": "stale_rejected",
                "ready_for_planning": False,
            }

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
                    issue
                    for issue in state.get("pending_issues", ())
                    if not patch_resolves_issue(patch, issue, request)
                )
                issues = (*unresolved, *issues)
            return apply_request_patch_state(
                state,
                request,
                patch,
                issues,
                existing_decision=decision,
                resumed=True,
            )

        value = (reply.value or "").strip()
        if reply.action == ClarificationAction.ANSWER and value in {
            "取消", "算了", "先不规划了",
        }:
            reply = reply.model_copy(update={"action": ClarificationAction.CANCEL})
        elif reply.action == ClarificationAction.ANSWER and value in {
            "按默认来吧", "按默认", "用默认", "使用默认出发地", "默认地点",
        }:
            reply = reply.model_copy(update={"action": ClarificationAction.USE_DEFAULT})

        if reply.action in {ClarificationAction.CANCEL, ClarificationAction.NEW_REQUEST}:
            outcome = clarification_resolver.resolve(
                pending_question=decision,
                reply=reply,
                base_interpretation=state["interpretation"],
                pending_modification=state.get("pending_modification"),
            )
            trace = _clarification_runtime_decision(decision, reply, outcome.status)
            if outcome.status == ClarificationResolution.NEW_REQUEST:
                return {
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
                }
            return {
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "clarification_resolution": outcome.status.value,
                "interpretation": outcome.interpretation,
                "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
            }

        if decision.issue_kind == "constraint":
            actor = state["actor"]
            environment = environment_provider(actor)
            if reply.action == ClarificationAction.USE_DEFAULT:
                field_patch = clarification_patch_compiler.compile_default(
                    field=decision.field or "",
                    request=request,
                    environment=environment,
                )
            elif reply.action == ClarificationAction.ANSWER and decision.allow_free_text and value:
                field_patch = clarification_patch_compiler.compile_answer(
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
                retry = _prepare_question_decision(
                    decision.model_copy(
                        update={
                            "attempt": next_attempt,
                            "allow_free_text": next_attempt < decision.max_attempts,
                        }
                    )
                )
                return {
                    "pending_issue": retry,
                    "clarification_resolution": ClarificationResolution.UNRESOLVED.value,
                    "ready_for_planning": False,
                    "runtime_decisions": (
                        *state.get("runtime_decisions", ()),
                        _clarification_runtime_decision(
                            decision, reply, ClarificationResolution.UNRESOLVED
                        ),
                    ),
                }

            pending_patch = state.get("pending_patch") or RequestPatch(
                base_revision=request.revision,
                source=ConstraintSource.USER_EXPLICIT,
            )
            merged_patch = merge_request_patches(pending_patch, field_patch)
            remaining_issues = tuple(
                issue for issue in state.get("pending_issues", ())
                if issue.field != decision.field
            )
            result = constraint_engine.apply(
                request,
                merged_patch,
                issues=remaining_issues,
            )
            trace_status = ClarificationResolution.RESOLVED
            if isinstance(result, NeedsClarification):
                issue = result.issue
                remaining_issues = remaining_issues or (issue,)
                next_question = _prepare_question_decision(
                    question_policy.decide(
                        state["interpretation"].primary_intent,
                        result.request or request,
                        QuestionPolicyContext(
                            has_plans=state.get("has_plans", False),
                            selected_plan_index=state["interpretation"].selected_plan_index,
                        ),
                        issue=issue,
                    )
                )
                trace_status = ClarificationResolution.UNRESOLVED
                return {
                    **(
                        {"active_request": result.request}
                        if result.request is not None
                        else {}
                    ),
                    "pending_issue": next_question,
                    "pending_patch": (
                        None if result.request is not None else merged_patch
                    ),
                    "pending_issues": remaining_issues,
                    "clarification_resolution": trace_status.value,
                    "ready_for_planning": False,
                    "runtime_decisions": (
                        *state.get("runtime_decisions", ()),
                        _clarification_runtime_decision(decision, reply, trace_status),
                    ),
                }
            if isinstance(result, ConflictedRequest):
                return {
                    "candidate_set": CandidateSet(conflict=result.conflict),
                    "active_request": request,
                    "pending_issue": None,
                    "pending_patch": None,
                    "pending_issues": (),
                    "clarification_resolution": "conflict",
                    "ready_for_planning": False,
                    "runtime_decisions": (
                        *state.get("runtime_decisions", ()),
                        _clarification_runtime_decision(decision, reply, ClarificationResolution.RESOLVED),
                    ),
                }
            is_default = reply.action == ClarificationAction.USE_DEFAULT
            assumptions = list(state.get("assumptions", ()))
            if is_default:
                assumption_value = _resolved_request_value(
                    result.request,
                    decision.field or "",
                )
                if assumption_value is not None:
                    assumption = Assumption(
                        field=decision.field or "unknown",
                        value=assumption_value,
                        reason="用户选择使用系统默认值",
                        rule_id=f"clarification.default.{decision.field}.v1",
                    )
                    assumptions.append(assumption)
            return {
                "active_request": result.request,
                "pending_issue": None,
                "pending_patch": None,
                "pending_issues": (),
                "assumptions": tuple(assumptions),
                "clarification_resolution": (
                    ClarificationResolution.USE_DEFAULT.value
                    if is_default else ClarificationResolution.RESOLVED.value
                ),
                "ready_for_planning": False,
                "runtime_decisions": (
                    *state.get("runtime_decisions", ()),
                    _clarification_runtime_decision(
                        decision, reply,
                        ClarificationResolution.USE_DEFAULT if is_default else ClarificationResolution.RESOLVED,
                    ),
                ),
            }

        outcome = clarification_resolver.resolve(
            pending_question=decision,
            reply=reply,
            base_interpretation=state["interpretation"],
            pending_modification=state.get("pending_modification"),
        )
        trace = _clarification_runtime_decision(decision, reply, outcome.status)
        if outcome.status == ClarificationResolution.UNRESOLVED:
            return {
                "pending_issue": _prepare_question_decision(outcome.question),
                "clarification_resolution": outcome.status.value,
                "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
            }
        return {
            "pending_issue": None,
            "interpretation": outcome.interpretation,
            "pending_modification": None if outcome.status == ClarificationResolution.MODIFICATION_RESOLVED else state.get("pending_modification"),
            "clarification_resolution": outcome.status.value,
            "runtime_decisions": (*state.get("runtime_decisions", ()), trace),
        }

    graph = StateGraph(EntryState)
    graph.add_node("router", router_node)
    graph.add_node("apply_request_patch", apply_request_patch_node)
    graph.add_node("replan_current_request", replan_current_request_node)
    graph.add_node("compile_patch", compile_patch_node)
    graph.add_node("modify_plan", modify_plan_node)
    graph.add_node("compile_request", compile_request_node)
    graph.add_node("gate", gate_node)
    graph.add_node("ask_question", ask_question_node)
    graph.add_node("planning", planning_node)
    graph.add_conditional_edges(
        START,
        route_after_start,
        {
            "apply_request_patch": "apply_request_patch",
            "replan_current_request": "replan_current_request",
            "router": "router",
        },
    )
    graph.add_edge("replan_current_request", "planning")
    graph.add_conditional_edges(
        "apply_request_patch",
        route_after_patch,
        {"ask_question": "ask_question", "planning": "planning", END: END},
    )
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {
            "compile_request": "compile_request",
            "compile_patch": "compile_patch",
            "modify_plan": "modify_plan",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "compile_patch",
        route_after_patch,
        {"ask_question": "ask_question", "planning": "planning", END: END},
    )
    graph.add_conditional_edges(
        "modify_plan",
        route_after_modify,
        {"ask_question": "ask_question", "planning": "planning", END: END},
    )
    graph.add_edge("compile_request", "gate")
    graph.add_conditional_edges(
        "gate",
        route_after_gate,
        {"ask_question": "ask_question", "planning": "planning", END: END},
    )
    graph.add_conditional_edges(
        "ask_question",
        route_after_clarification,
        {
            "gate": "gate",
            "router": "router",
            "modify_plan": "modify_plan",
            "planning": "planning",
            "ask_question": "ask_question",
            END: END,
        },
    )
    graph.add_edge("planning", END)
    if checkpointer is None:
        checkpointer = MemorySaver(
            serde=checkpoint_serializer()
        )
    return graph.compile(checkpointer=checkpointer)


def _prepare_question_decision(decision: QuestionDecision) -> QuestionDecision:
    """Persist stable interruption metadata before entering ``interrupt``."""

    if not decision.need_question:
        return decision
    if decision.request_revision is None:
        raise ValueError("blocking questions must be bound to a request revision")
    clarification_id = decision.clarification_id or uuid.uuid4().hex
    options = decision.options or _clarification_options(decision.field)
    return decision.model_copy(
        update={
            "clarification_id": clarification_id,
            "options": options,
            "allow_free_text": decision.allow_free_text and decision.attempt < decision.max_attempts,
        }
    )


def _clarification_options(field: str | None) -> tuple[ClarificationOption, ...]:
    options: list[ClarificationOption] = []
    if field in {"location", "date", "time_window", "departure_at", "max_distance_km", "total_distance_km"}:
        option_id = "use-default-location" if field == "location" else f"use-default-{field}"
        label = "使用默认出发地" if field == "location" else "按系统默认处理"
        options.append(
            ClarificationOption(
                id=option_id,
                label=label,
                action=ClarificationAction.USE_DEFAULT,
            )
        )
    if field == "constraint_patch":
        options.extend(
            (
                ClarificationOption(id="patch-time", label="调整时间", action=ClarificationAction.ANSWER),
                ClarificationOption(id="patch-budget", label="调整预算", action=ClarificationAction.ANSWER),
                ClarificationOption(id="patch-location", label="调整地点", action=ClarificationAction.ANSWER),
            )
        )
    options.extend(
        (
            ClarificationOption(id="cancel", label="取消本轮", action=ClarificationAction.CANCEL),
            ClarificationOption(id="new-request", label="开始新需求", action=ClarificationAction.NEW_REQUEST),
        )
    )
    return tuple(options)


def _question_payload(decision: QuestionDecision) -> dict[str, object]:
    """Serialize only safe, bounded interruption data to the client."""

    return {
        "field": decision.field,
        "issue_kind": decision.issue_kind,
        "request_revision": decision.request_revision,
        "question": decision.question,
        "severity": decision.severity,
        "rule_id": decision.rule_id,
        "clarification_id": decision.clarification_id,
        "attempt": decision.attempt,
        "max_attempts": decision.max_attempts,
        "allow_free_text": decision.allow_free_text,
        "options": [item.model_dump(mode="json") for item in decision.options],
    }


def _coerce_clarification_reply(
    answer: object,
) -> ClarificationReply:
    """Parse the typed reply; stale identity is handled as a domain conflict."""

    if isinstance(answer, ClarificationReply):
        return answer
    if isinstance(answer, dict):
        return ClarificationReply.model_validate(answer)
    raise TypeError("clarification resume must be a typed reply or validated payload")


def _resolved_request_value(request: PlanRequest, field: str) -> object | None:
    """Return the user-visible value for a defaulted request field."""

    window_fields = {
        "date": request.planning_window.date,
        "time_window": None,
        "departure_at": request.planning_window.start_at,
        "return_by": request.planning_window.end_at,
    }
    if field == "time_window":
        if request.planning_window.start_at is None or request.planning_window.end_at is None:
            return None
        return {
            "start_at": request.planning_window.start_at.value,
            "end_at": request.planning_window.end_at.value,
        }
    if field in window_fields:
        wrapped = window_fields[field]
        return wrapped.value if wrapped is not None else None
    value = getattr(request, field, None)
    return value.value if isinstance(value, ConstraintValue) else value


def route_after_clarification(state: EntryState) -> str:
    status = state.get("clarification_resolution")
    if status in {ClarificationResolution.RESOLVED.value, ClarificationResolution.USE_DEFAULT.value}:
        if state.get("mutation_kind") == "constraint_patch":
            return "planning"
        if state.get("mutation_kind") == "create":
            return "gate"
        return END
    if status == ClarificationResolution.MODIFICATION_RESOLVED.value:
        return "modify_plan"
    if status == ClarificationResolution.NEW_REQUEST.value:
        return "router"
    if status == ClarificationResolution.UNRESOLVED.value:
        return "ask_question"
    return END


def route_after_patch(state: EntryState) -> str:
    if state.get("mutation_kind") == "constraint_patch":
        if state.get("pending_issue") is not None:
            return "ask_question"
        if state.get("defer_planning", False):
            return END
        return "planning"
    if state.get("mutation_kind") == "constraint_patch_conflict":
        return END
    question = state.get("pending_issue")
    if question is not None and question.need_question:
        return "ask_question"
    return END


def route_after_start(state: EntryState) -> str:
    if state.get("request_patch_override") is not None:
        return "apply_request_patch"
    if state.get("replan_current_request", False):
        return "replan_current_request"
    return "router"


def route_after_modify(state: EntryState) -> str:
    """Only incomplete mutations enter the interrupt/resume path."""
    question = state.get("pending_issue")
    if question is not None and question.need_question:
        return "ask_question"
    return END


def _clarification_runtime_decision(
    decision: QuestionDecision,
    reply: ClarificationReply,
    status: ClarificationResolution,
) -> RuntimeDecision:
    """Record bounded clarification diagnostics without answer text."""

    return RuntimeDecision(
        stage="turn_interpreter",
        adapter=(
            "clarification_patch_compiler"
            if decision.issue_kind == "constraint"
            else "clarification_resolver"
        ),
        model_invoked=False,
        model_name=None,
        attempts=0,
        fallback_reason=None,
        diagnostic_code=f"clarification_{status.value}",
        diagnostic_paths=((decision.field,) if decision.field else ()),
        requested_mode=reply.action.value,
        wire_schema_version="clarification.v1",
    )


def checkpoint_serializer() -> JsonPlusSerializer:
    """限制 checkpoint 可反序列化的自定义类型，避免任意对象被加载。"""
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ActorContext,
            CandidateSet,
            ConstraintConflict,
            PlanWarning,
            CatalogWarningCode,
            CatalogSource,
            ConstraintViolation,
            ConstraintSource,
            DateReference,
            Assumption,
            IdentityType,
            Intent,
            Interpretation,
            CommandOperation,
            ConstraintPatch,
            CriterionStrength,
            ConversationCommand,
            PlanRequest,
            PlanningWindow,
            RequestPatch,
            ConstraintValue,
            ClarificationIssue,
            PendingModification,
            LockedStop,
            Plan,
            PlanDiff,
            PlanPace,
            PlanSlotProposal,
            PlanStructureProposal,
            PlanningIntent,
            PlanningIntentDecision,
            PlanPriceStatus,
            PlanStrategy,
            PriceKind,
            ResourceType,
            QuestionDecision,
            ProviderMode,
            ProviderSource,
            AvailabilityFact,
            AvailabilityStatus,
            GeocodeRequest,
            GeocodeResolution,
            GeocodingFact,
            GeoPoint,
            RouteFact,
            RouteMode,
            RouteSource,
            RouteObjective,
            SemanticCriterion,
            StopRole,
            TimeScope,
            TimeProposal,
            Weekday,
            TargetReference,
            StopReplacement,
            StopType,
            WeatherFact,
            RuntimeDecision,
            ClarificationAction,
            ClarificationOption,
            ClarificationReply,
            EvidenceRef,
            SemanticQuery,
            SemanticRequest,
            SoftObjective,
            SoftObjectiveKind,
            VerificationStatus,
            ViolationCode,
        ],
    )


def _interpret_with_runtime(
    router: TurnInterpreter,
    user_input: str,
    context: RouterContext,
) -> tuple[Interpretation, RuntimeDecision]:
    """Use an adapter's optional runtime-aware seam without breaking old fakes."""

    interpret_with_runtime = getattr(router, "interpret_with_runtime", None)
    if callable(interpret_with_runtime):
        return interpret_with_runtime(user_input, context)
    return router.interpret(user_input, context), RuntimeDecision(
        stage="turn_interpreter",
        adapter="custom",
        model_invoked=False,
        model_name=None,
        attempts=0,
        fallback_reason=None,
        latency_ms=None,
    )


def default_environment_provider(actor: ActorContext) -> EnvironmentContext:
    """M1 默认环境；真实位置和天气工具接入后应由新的 provider 替换。"""
    from zoneinfo import ZoneInfo

    from app.domain.constraints import GeoLocation

    return EnvironmentContext(
        now=datetime.now(ZoneInfo(actor.timezone)),
        default_location=GeoLocation(
            city="北京市",
            district="朝阳区",
            address="北京市朝阳区",
            latitude=39.9219,
            longitude=116.4436,
        ),
    )

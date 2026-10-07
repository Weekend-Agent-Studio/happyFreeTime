"""HappyFreeTime V2 的 LangGraph 编排层。

创建链路为：TurnInterpreter -> compile_request -> QuestionPolicy -> Planning；定向修改链路从
TurnInterpreter 进入单个 modify_plan 节点。Gate 判断需要反问时，进入
ask_question 并通过 interrupt 暂停；用户下一条消息通过 Command(resume) 恢复，
约束反问由字段级 RequestPatch 更新 PlanRequest；非约束目标澄清仍由专用解析器处理。
Graph 只负责节点顺序、interrupt/resume 和 checkpoint；请求、修改与反问
的状态转换由 ``app.orchestration.workflows`` 提供的深模块负责。
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
    EventClockProposal,
    PeriodProposal,
    TripRangeProposal,
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


CHECKPOINT_SCHEMA_VERSION = "planner-core3e-v1"


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
from app.services.clarification_patch import ClarificationPatchCompiler
from app.services.request_patch_update import RequestPatchUpdateCompiler
from app.services.constraint_engine import ConstraintEngine, ResolvedRequest
from app.services.request_patch_compiler import RequestPatchProposalCompiler
from app.services.router_extractor import RouterContext, TurnInterpreterResult
from app.domain.turn import (
    AnswerQuery,
    ApplyRequestPatch,
    CompiledNextAction,
    ModifySelectedPlan,
    NeedsClarification as ActionNeedsClarification,
    NoAction,
    TurnCompiler,
    intent_for_action,
    user_act_kind_for_action,
)
from app.orchestration.workflows import (
    ClarificationWorkflow,
    ModificationWorkflow,
    RequestWorkflow,
    WorkflowOutcome,
    WorkflowRoute,
)


class TurnInterpreter(Protocol):
    """真实 LLM 与离线 Demo Adapter 共同满足的语义解释接口。

    The runtime path should prefer ``interpret_with_runtime`` because it
    returns the already compiled ``CompiledNextAction``.  ``interpret`` is
    retained only for older composition roots and custom adapters that still
    expose an ``Interpretation`` result.
    """

    def interpret_with_runtime(
        self,
        user_input: str,
        context: RouterContext,
    ) -> TurnInterpreterResult | tuple[Interpretation, RuntimeDecision]:
        ...

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        ...


class DurableSessionState(TypedDict, total=False):
    """Persisted session facts and the currently selected plan snapshot."""

    actor: ActorContext
    active_request: PlanRequest | None
    has_plans: bool
    active_plan_version_id: str | None
    selected_plan: Plan | None


class PendingInteractionState(TypedDict, total=False):
    """Only the information needed to resume an interrupt safely."""

    pending_patch: RequestPatch | None
    pending_issues: tuple[ClarificationIssue, ...]
    pending_issue: QuestionDecision | None
    pending_modification: PendingModification | None
    clarification_resolution: str | None


class TurnInputState(TypedDict, total=False):
    """One-turn inputs supplied by HTTP/UI or Command(resume)."""

    user_input: str
    conversation_command_override: ConversationCommand | None
    request_patch_override: RequestPatch | None
    request_patch_issues: tuple[ClarificationIssue, ...]
    defer_planning: bool
    replan_current_request: bool


class TurnResultState(TypedDict, total=False):
    """Derived output of the current workflow execution."""

    interpretation: Interpretation | None
    next_action: CompiledNextAction | None
    assumptions: tuple[Assumption, ...]
    geocoding_fact: GeocodingFact | None
    candidate_set: CandidateSet | None
    plan_diffs: tuple[PlanDiff, ...]
    runtime_decisions: tuple[RuntimeDecision, ...]
    ready_for_planning: bool
    condition_requests_plan: bool
    # Explicit workflow result consumed by conditional edges.  The old
    # ready field remains a response diagnostic, not a route input.
    workflow_route: str | None
    workflow_outcome: str | None


class EntryState(
    DurableSessionState,
    PendingInteractionState,
    TurnInputState,
    TurnResultState,
    total=False,
):
    """Flat LangGraph state grouped by lifecycle ownership.

    LangGraph merges flat deltas into checkpoints, so these groups are type
    ownership rather than nested runtime dictionaries.  This keeps the
    checkpoint shape stable while preventing route logic from treating every
    field as an independent control flag.
    """


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
    request_workflow = RequestWorkflow(
        environment_provider=environment_provider,
        enrichment_service=enrichment_service,
        proposal_compiler=request_patch_compiler,
        update_compiler=request_patch_update_compiler,
        constraint_engine=constraint_engine,
        question_policy=question_policy,
        prepare_question=_prepare_question_decision,
    )
    modification_workflow = ModificationWorkflow(
        planning_service=planning_service,
        prepare_question=_prepare_question_decision,
    )
    clarification_workflow = ClarificationWorkflow(
        request_workflow=request_workflow,
        clarification_resolver=clarification_resolver,
        clarification_patch_compiler=clarification_patch_compiler,
        environment_provider=environment_provider,
        prepare_question=_prepare_question_decision,
        question_payload=_question_payload,
        coerce_reply=_coerce_clarification_reply,
        runtime_decision=_clarification_runtime_decision,
        resolved_request_value=_resolved_request_value,
        patch_resolves_issue=lambda patch, issue, request: patch_resolves_issue(
            patch, issue, request
        ),
    )

    def router_node(state: EntryState) -> dict[str, object]:
        actor = state["actor"]
        environment = environment_provider(actor)
        previous_user_act = user_act_kind_for_action(state.get("next_action"))
        decision_context = DecisionContextBuilder.build(
            request=state.get("active_request"),
            selected_plan=state.get("selected_plan"),
            active_plan_version_id=state.get("active_plan_version_id"),
            pending_issue=state.get("pending_issue"),
            pending_modification=state.get("pending_modification"),
            has_plans=state.get("has_plans", False),
            previous_user_act=previous_user_act,
            last_system_outcome=state.get("workflow_outcome"),
        )
        structured_command = state.get("conversation_command_override")
        if structured_command is not None:
            # UI commands already passed Pydantic validation and carry an
            # explicit selected-plan target.  They do not need an LLM round trip;
            # the command still goes through the same service authorization and
            # verification checks as a natural-language command.
            compilation = TurnCompiler.compile_command(
                structured_command,
                context=decision_context,
            )
            interpretation = compilation.interpretation
            action = compilation.action
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
            turn_result = _interpret_with_runtime(
                router,
                state["user_input"],
                RouterContext(
                    current_date=environment.now.date(),
                    timezone=actor.timezone,
                    has_plans=state.get("has_plans", False),
                    has_selected_plan=state.get("selected_plan") is not None,
                    previous_user_act=previous_user_act,
                    decision_context=decision_context,
                ),
            )
            interpretation = turn_result.interpretation
            action = turn_result.action
            runtime_decision = turn_result.runtime
        # 这些派生值只属于当前解释轮次。反问恢复或用户修改需求时必须清空，
        # 否则新请求可能误用上一轮的假设、Gate 决策或候选方案。
        return {
            "interpretation": interpretation,
            "next_action": action,
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
            "workflow_route": None,
            "workflow_outcome": None,
        }

    def route_after_router(state: EntryState) -> str:
        """Route only on the compiled action, never on model intent fields."""
        action = state.get("next_action")
        if isinstance(action, ApplyRequestPatch):
            return (
                WorkflowRoute.COMPILE_PATCH.value
                if action.mode == "update"
                else WorkflowRoute.COMPILE_REQUEST.value
            )
        if isinstance(action, ModifySelectedPlan):
            return WorkflowRoute.MODIFY_PLAN.value
        if isinstance(action, ActionNeedsClarification):
            if action.field == "request_rephrase" or action.issue_kind == "action":
                # An illegal action proposal must not enter a patch workflow.
                # The Router's bounded repair attempt owns recovery; if it
                # still fails, the interpretation carries the user-facing
                # rephrase request and the turn ends safely.
                return WorkflowRoute.END.value
            return (
                WorkflowRoute.MODIFY_PLAN.value
                if action.issue_kind in {"target", "selection"}
                else WorkflowRoute.COMPILE_PATCH.value
            )
        if isinstance(action, AnswerQuery):
            return (
                WorkflowRoute.COMPILE_REQUEST.value
                if action.query_kind == "weather"
                else WorkflowRoute.END.value
            )
        return WorkflowRoute.END.value

    def modify_plan_node(state: EntryState) -> dict[str, object]:
        return modification_workflow.execute(state).as_state()

    def compile_patch_node(state: EntryState) -> dict[str, object]:
        """Compile one PATCH_CONSTRAINTS proposal before re-entering planning."""
        action = state.get("next_action")
        if not isinstance(action, ApplyRequestPatch) or action.mode != "update":
            raise ValueError("compile_patch requires an update action")
        return request_workflow.compile_update(state, action=action).as_state()

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
            **request_workflow.apply_patch(
                state,
                request=state.get("active_request") or PlanRequest(),
                patch=patch,
                issues=state.get("request_patch_issues", ()),
            ).as_state(),
        }

    def replan_current_request_node(state: EntryState) -> dict[str, object]:
        if state.get("active_request") is None:
            raise ValueError("replan_current_request requires an active PlanRequest")
        return request_workflow.replan().as_state()

    def patch_resolves_issue(
        patch: RequestPatch,
        issue: ClarificationIssue,
        request: PlanRequest,
    ) -> bool:
        """Whether a typed edit supplied the value required by a pending issue."""
        fields = patch.set_fields
        if issue.field == "location":
            return fields.get("location") is not None
        if issue.field == "planning_area":
            return fields.get("planning_area") is not None
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
        action = state.get("next_action")
        if not (
            isinstance(action, AnswerQuery)
            or (isinstance(action, ApplyRequestPatch) and action.mode == "create")
        ):
            raise ValueError("compile_request requires a create action")
        return request_workflow.compile_create(
            state,
            interpretation=state["interpretation"],
            action=action,
        ).as_state()

    def gate_node(state: EntryState) -> dict[str, object]:
        existing_issue = state.get("pending_issue")
        if existing_issue is not None and existing_issue.need_question:
            return {
                "ready_for_planning": False,
                "workflow_route": WorkflowRoute.ASK_QUESTION.value,
            }
        action = state.get("next_action")
        decision = question_policy.decide(
            intent_for_action(action),
            state.get("active_request") or PlanRequest(),
            QuestionPolicyContext(
                has_plans=state.get("has_plans", False),
                selected_plan_index=state["interpretation"].selected_plan_index,
            ),
        )
        # 只有真正需要产出/修改方案的意图才能进入 Planner。天气查询、执行和
        # 取消等意图即使字段完整，也不应错误触发双站规划。
        planning_request = isinstance(action, ApplyRequestPatch) and action.mode == "create"
        weather_condition_planning = (
            isinstance(action, AnswerQuery)
            and action.query_kind == "weather"
            and action.condition_requests_plan
        )
        ready = (
            not decision.need_question
            and not (state.get("candidate_set") and state["candidate_set"].conflict)
            and (
                planning_request or weather_condition_planning
            )
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
            "ready_for_planning": ready,
            "workflow_route": (
                WorkflowRoute.ASK_QUESTION.value
                if decision.need_question
                else WorkflowRoute.PLANNING.value
                if ready
                else WorkflowRoute.END.value
            ),
        }

    def route_after_gate(state: EntryState) -> str:
        return state.get("workflow_route") or END

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
            "workflow_route": WorkflowRoute.END.value,
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
        return clarification_workflow.resume(state).as_state()

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
    if field in {"location", "planning_area", "date", "time_window", "departure_at", "max_distance_km", "total_distance_km"}:
        option_id = "use-default-location" if field == "location" else f"use-default-{field}"
        if field == "location":
            options.append(
                ClarificationOption(
                    id=option_id,
                    label="使用默认出发地",
                    action=ClarificationAction.USE_DEFAULT,
                )
            )
        elif field != "planning_area":
            options.append(
                ClarificationOption(
                    id=option_id,
                    label="按系统默认处理",
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
    return state.get("workflow_route") or END


def route_after_patch(state: EntryState) -> str:
    return state.get("workflow_route") or END


def route_after_start(state: EntryState) -> str:
    if state.get("request_patch_override") is not None:
        return WorkflowRoute.APPLY_REQUEST_PATCH.value
    if state.get("replan_current_request", False):
        return WorkflowRoute.REPLAN_CURRENT_REQUEST.value
    return WorkflowRoute.ROUTER.value


def route_after_modify(state: EntryState) -> str:
    """Only incomplete mutations enter the interrupt/resume path."""
    return state.get("workflow_route") or END


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
            EventClockProposal,
            PeriodProposal,
            TripRangeProposal,
            Weekday,
            TargetReference,
            StopReplacement,
            StopType,
            WeatherFact,
            RuntimeDecision,
            ApplyRequestPatch,
            ModifySelectedPlan,
            AnswerQuery,
            NoAction,
            ActionNeedsClarification,
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
) -> TurnInterpreterResult:
    """Normalize real and deterministic Routers at the compiled-action seam."""

    interpret_with_runtime = getattr(router, "interpret_with_runtime", None)
    if callable(interpret_with_runtime):
        result = interpret_with_runtime(user_input, context)
        if isinstance(result, TurnInterpreterResult):
            return result
        interpretation, runtime = result
        compilation = TurnCompiler.from_interpretation(
            interpretation,
            context=context.decision_context,
            has_selected_plan=context.has_selected_plan,
        )
        return TurnInterpreterResult(
            interpretation=compilation.interpretation,
            action=compilation.action,
            runtime=runtime,
        )
    interpretation = router.interpret(user_input, context)
    compilation = TurnCompiler.from_interpretation(
        interpretation,
        context=context.decision_context,
        has_selected_plan=context.has_selected_plan,
    )
    return TurnInterpreterResult(
        interpretation=compilation.interpretation,
        action=compilation.action,
        runtime=RuntimeDecision(
            stage="turn_interpreter",
            adapter="custom",
            model_invoked=False,
            model_name=None,
            attempts=0,
            fallback_reason=None,
            latency_ms=None,
        ),
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

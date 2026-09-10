"""HappyFreeTime V2 的 LangGraph 编排层。

创建链路为：TurnInterpreter -> Enrichment -> Gate -> Planning；定向修改链路从
TurnInterpreter 进入单个 modify_plan 节点。Gate 判断需要反问时，
进入 ask_question 并通过 interrupt 暂停；用户下一条消息通过 Command(resume)
恢复后，重新从 TurnInterpreter 解释“原请求 + 补充答案”。Graph 只负责节点顺序和状态，
具体业务逻辑仍位于可独立测试的 service 中。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from app.domain.constraints import (
    ActorContext,
    CommandOperation,
    ConstraintPatch,
    CriterionStrength,
    ConversationCommand,
    ConstraintSource,
    EnrichmentResult,
    IdentityType,
    Intent,
    Interpretation,
    NormalizedConstraints,
    QuestionDecision,
    RouteObjective,
    SemanticCriterion,
    StopRole,
    TargetReference,
    TimeScope,
)
from app.domain.planning import (
    CandidateSet,
    LockedStop,
    Plan,
    PlanDiff,
    PlanPace,
    PlanningIntent,
    PlanningIntentDecision,
    PlanningIntentProposal,
    PlanPriceStatus,
    PlanWarning,
    PlanStrategy,
    StopReplacement,
    StopType,
)
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
from app.services.planning_intent import PlanningIntentProvider
from app.services.question_gate import GateContext, NeedQuestionGate
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
    interpretation: Interpretation
    enrichment: EnrichmentResult | None
    question_decision: QuestionDecision | None
    candidate_set: CandidateSet | None
    ready_for_planning: bool
    has_plans: bool
    active_plan_version_id: str | None
    active_constraints: NormalizedConstraints | None
    selected_plan: Plan | None
    plan_diff: PlanDiff | None
    plan_diffs: tuple[PlanDiff, ...]
    conversation_command_override: ConversationCommand | None
    modification_question: QuestionDecision | None
    runtime_decisions: tuple[RuntimeDecision, ...]


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
    checkpointer: object | None = None,
):
    """组装 M1 控制流，并允许注入 Router、环境提供器和 checkpointer。

    依赖注入让测试可以使用规则 Router、固定时间和内存 checkpoint；生产 API
    则使用真实 Router、系统时间和 SQLite checkpoint，但 Graph 本身无需分叉。
    """
    enrichment_service = EnrichmentService(geocoding_provider=geocoding_provider)
    question_gate = NeedQuestionGate()
    planning_service = PlanningService(
        weather_provider=weather_provider,
        route_provider=route_provider,
        availability_provider=availability_provider,
        catalog=catalog,
        planning_intent_provider=planning_intent_provider,
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
                    if structured_command.operation == CommandOperation.REPLACE
                    else Intent.CLARIFY
                ),
                intent_scores={
                    (
                        Intent.REFINE_PLAN
                        if structured_command.operation == CommandOperation.REPLACE
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
            interpretation, runtime_decision = _interpret_with_runtime(
                router,
                state["user_input"],
                RouterContext(
                    current_date=environment.now.date(),
                    timezone=actor.timezone,
                    has_plans=state.get("has_plans", False),
                    has_selected_plan=state.get("selected_plan") is not None,
                    previous_intent=(
                        state["interpretation"].primary_intent
                        if state.get("interpretation")
                        else None
                    ),
                ),
            )
        # 这些派生值只属于当前解释轮次。反问恢复或用户修改需求时必须清空，
        # 否则新请求可能误用上一轮的假设、Gate 决策或候选方案。
        return {
            "interpretation": interpretation,
            "enrichment": None,
            "question_decision": None,
            "candidate_set": None,
            "ready_for_planning": False,
            "plan_diff": None,
            "plan_diffs": (),
            "conversation_command_override": None,
            "modification_question": None,
            "runtime_decisions": (runtime_decision,),
        }

    def route_after_router(state: EntryState) -> str:
        """闲聊和无法可靠理解的输入直接结束，不浪费后续规划计算。"""
        intent = state["interpretation"].primary_intent
        if intent in {Intent.CHITCHAT, Intent.CLARIFY}:
            return END
        command = state["interpretation"].conversation_command
        if intent == Intent.REFINE_PLAN or (
            command is not None and command.operation == CommandOperation.REPLACE
        ):
            return "modify_plan"
        return "enrichment"

    def modify_plan_node(state: EntryState) -> dict[str, object]:
        interpretation = state["interpretation"]
        command = interpretation.conversation_command
        if command is None or command.operation != CommandOperation.REPLACE:
            return {
                "modification_question": QuestionDecision(
                    need_question=True,
                    field="conversation_command",
                    question="请明确要保留哪一站，以及要替换哪一站。",
                    severity="blocking",
                )
            }
        selected_plan = state.get("selected_plan")
        active_constraints = state.get("active_constraints")
        if selected_plan is None:
            return {
                "modification_question": QuestionDecision(
                    need_question=True,
                    field="selected_plan_id",
                    question="请先选择一个方案，再告诉我要保留和替换哪一站。",
                    severity="blocking",
                )
            }
        if active_constraints is None:
            return {
                "modification_question": QuestionDecision(
                    need_question=True,
                    field="active_plan_version_id",
                    question="当前方案缺少可恢复的约束快照，请重新生成并选择方案。",
                    severity="blocking",
                )
            }
        outcome = planning_service.modify_selected_plan(
            selected_plan=selected_plan,
            constraints=active_constraints,
            command=command,
        )
        return {
            "candidate_set": outcome.candidate_set,
            "plan_diff": outcome.plan_diff,
            "plan_diffs": outcome.plan_diffs,
            "modification_question": outcome.question,
            "runtime_decisions": (
                *state.get("runtime_decisions", ()),
                outcome.runtime_decision,
            ),
        }

    def enrichment_node(state: EntryState) -> dict[str, object]:
        environment = environment_provider(state["actor"])
        result = enrichment_service.enrich(
            state["interpretation"],
            state["actor"],
            environment,
        )
        return {"enrichment": result}

    def gate_node(state: EntryState) -> dict[str, object]:
        decision = question_gate.decide(
            state["interpretation"],
            state["enrichment"],
            GateContext(has_plans=state.get("has_plans", False)),
        )
        # 只有真正需要产出/修改方案的意图才能进入 Planner。天气查询、执行和
        # 取消等意图即使字段完整，也不应错误触发双站规划。
        planning_intents = {
            Intent.PLAN_OUTING,
            Intent.FIND_ACTIVITY,
            Intent.REFINE_PLAN,
        }
        return {
            "question_decision": decision,
            "ready_for_planning": (
                not decision.need_question
                and state["interpretation"].primary_intent in planning_intents
            ),
        }

    def route_after_gate(state: EntryState) -> str:
        decision = state["question_decision"]
        if decision is not None and decision.need_question:
            return "ask_question"
        if state.get("ready_for_planning", False):
            return "planning"
        return END

    def planning_node(state: EntryState) -> dict[str, object]:
        candidate_set = planning_service.plan(state["enrichment"].constraints)
        geocoding_fact = state["enrichment"].geocoding_fact
        if geocoding_fact is not None:
            candidate_set = candidate_set.model_copy(
                update={"provider_facts": [geocoding_fact, *candidate_set.provider_facts]}
            )
        return {
            "candidate_set": candidate_set,
            "runtime_decisions": (
                *state.get("runtime_decisions", ()),
                *(
                    (candidate_set.runtime_decision,)
                    if candidate_set.runtime_decision is not None
                    else ()
                ),
            ),
        }

    def ask_question_node(state: EntryState) -> dict[str, object]:
        decision = state["question_decision"]
        if decision is None:
            raise ValueError("ask_question requires a QuestionDecision")
        # interrupt 会保存当前节点位置和状态。API 下一次收到用户回答时，以
        # Command(resume=...) 恢复同一个 checkpoint，进程重启后仍可继续。
        answer = interrupt(
            {
                "field": decision.field,
                "question": decision.question,
                "severity": decision.severity,
            }
        )
        original = state["user_input"]
        # 把原请求和补充答案一起重新抽取，避免在 Graph 中为每个待补字段实现
        # 一套手工 merge 逻辑，也让真实 Router 和 DemoRouter 保持同一接口。
        return {
            "user_input": f"{original}\n用户补充：{answer}",
            "ready_for_planning": False,
        }

    graph = StateGraph(EntryState)
    graph.add_node("router", router_node)
    graph.add_node("modify_plan", modify_plan_node)
    graph.add_node("enrichment", enrichment_node)
    graph.add_node("gate", gate_node)
    graph.add_node("ask_question", ask_question_node)
    graph.add_node("planning", planning_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {"enrichment": "enrichment", "modify_plan": "modify_plan", END: END},
    )
    graph.add_edge("modify_plan", END)
    graph.add_edge("enrichment", "gate")
    graph.add_conditional_edges(
        "gate",
        route_after_gate,
        {"ask_question": "ask_question", "planning": "planning", END: END},
    )
    graph.add_edge("ask_question", "router")
    graph.add_edge("planning", END)
    if checkpointer is None:
        checkpointer = MemorySaver(
            serde=checkpoint_serializer()
        )
    return graph.compile(checkpointer=checkpointer)


def checkpoint_serializer() -> JsonPlusSerializer:
    """限制 checkpoint 可反序列化的自定义类型，避免任意对象被加载。"""
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ActorContext,
            CandidateSet,
            PlanWarning,
            CatalogWarningCode,
            CatalogSource,
            ConstraintViolation,
            ConstraintSource,
            EnrichmentResult,
            IdentityType,
            Intent,
            Interpretation,
            CommandOperation,
            ConstraintPatch,
            CriterionStrength,
            ConversationCommand,
            NormalizedConstraints,
            LockedStop,
            Plan,
            PlanDiff,
            PlanPace,
            PlanningIntent,
            PlanningIntentDecision,
            PlanningIntentProposal,
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
            TargetReference,
            StopReplacement,
            StopType,
            WeatherFact,
            RuntimeDecision,
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

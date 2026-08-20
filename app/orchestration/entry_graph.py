"""HappyFreeTime V2 的 LangGraph 编排层。

主链路为：Router -> Enrichment -> Gate -> Planning。Gate 判断需要反问时，
进入 ask_question 并通过 interrupt 暂停；用户下一条消息通过 Command(resume)
恢复后，重新从 Router 解释“原请求 + 补充答案”。Graph 只负责节点顺序和状态，
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
    ConstraintSource,
    EnrichmentResult,
    IdentityType,
    Intent,
    Interpretation,
    QuestionDecision,
)
from app.domain.planning import CandidateSet
from app.domain.providers import (
    GeoPoint,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteMode,
    RouteSource,
    WeatherFact,
)
from app.providers.route import RouteProvider
from app.providers.weather import WeatherProvider
from app.services.enrichment import EnvironmentContext, EnrichmentService
from app.services.planning import PlanningService
from app.services.question_gate import GateContext, NeedQuestionGate
from app.services.router_extractor import RouterContext


class Router(Protocol):
    """真实 LLM Router 与离线 DemoRouter 共同满足的接口。"""
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        ...


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


EnvironmentProvider = Callable[[ActorContext], EnvironmentContext]


def build_entry_graph(
    *,
    router: Router,
    environment_provider: EnvironmentProvider,
    weather_provider: WeatherProvider | None = None,
    route_provider: RouteProvider | None = None,
    checkpointer: object | None = None,
):
    """组装 M1 控制流，并允许注入 Router、环境提供器和 checkpointer。

    依赖注入让测试可以使用规则 Router、固定时间和内存 checkpoint；生产 API
    则使用真实 Router、系统时间和 SQLite checkpoint，但 Graph 本身无需分叉。
    """
    enrichment_service = EnrichmentService()
    question_gate = NeedQuestionGate()
    planning_service = PlanningService(
        weather_provider=weather_provider,
        route_provider=route_provider,
    )

    def router_node(state: EntryState) -> dict[str, object]:
        actor = state["actor"]
        environment = environment_provider(actor)
        interpretation = router.interpret(
            state["user_input"],
            RouterContext(
                current_date=environment.now.date(),
                timezone=actor.timezone,
                has_plans=state.get("has_plans", False),
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
        }

    def route_after_router(state: EntryState) -> str:
        """闲聊和无法可靠理解的输入直接结束，不浪费后续规划计算。"""
        intent = state["interpretation"].primary_intent
        if intent in {Intent.CHITCHAT, Intent.CLARIFY}:
            return END
        return "enrichment"

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
        return {"candidate_set": planning_service.plan(state["enrichment"].constraints)}

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
    graph.add_node("enrichment", enrichment_node)
    graph.add_node("gate", gate_node)
    graph.add_node("ask_question", ask_question_node)
    graph.add_node("planning", planning_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {"enrichment": "enrichment", END: END},
    )
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
            ConstraintSource,
            EnrichmentResult,
            IdentityType,
            Intent,
            Interpretation,
            QuestionDecision,
            ProviderMode,
            ProviderSource,
            GeoPoint,
            RouteFact,
            RouteMode,
            RouteSource,
            WeatherFact,
        ],
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

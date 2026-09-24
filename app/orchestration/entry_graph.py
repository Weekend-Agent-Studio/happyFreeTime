"""HappyFreeTime V2 的 LangGraph 编排层。

创建链路为：TurnInterpreter -> Enrichment -> Gate -> Planning；定向修改链路从
TurnInterpreter 进入单个 modify_plan 节点。Gate 判断需要反问时，进入
ask_question 并通过 interrupt 暂停；用户下一条消息通过 Command(resume) 恢复，
由 field-scoped ClarificationResolver 只更新待补字段，再回到 Enrichment/Gate。
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
    CriterionStrength,
    ConversationCommand,
    ConstraintSource,
    DateReference,
    EnrichmentResult,
    IdentityType,
    Intent,
    Interpretation,
    NormalizedConstraints,
    PendingModification,
    QuestionDecision,
    RouteObjective,
    SemanticCriterion,
    StopRole,
    TargetReference,
    TimeScope,
    Weekday,
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
from app.services.question_gate import GateContext, NeedQuestionGate
from app.services.clarification import ClarificationResolution, ClarificationResolver
from app.services.constraint_patch import ConstraintPatchCompiler
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
    clarification_default_fields: tuple[str, ...]
    clarification_resolution: str | None
    pending_modification: PendingModification | None
    planning_constraints: NormalizedConstraints | None
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
    question_gate = NeedQuestionGate()
    clarification_resolver = ClarificationResolver()
    constraint_patch_compiler = ConstraintPatchCompiler(
        geocoding_provider=geocoding_provider,
    )
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
            "clarification_default_fields": (),
            "clarification_resolution": None,
            "pending_modification": None,
            "planning_constraints": None,
            "mutation_kind": None,
        }

    def route_after_router(state: EntryState) -> str:
        """闲聊和无法可靠理解的输入直接结束，不浪费后续规划计算。"""
        interpretation = state["interpretation"]
        intent = interpretation.primary_intent
        if intent in {Intent.CHITCHAT, Intent.CLARIFY}:
            return END
        if (
            intent == Intent.CHECK_WEATHER
            and _weather_condition_requests_planning(interpretation)
        ):
            return "enrichment"
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
        return "enrichment"

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
                        rule_id="question.target_reference.v1",
                    )
                )
                return {
                    "modification_question": question,
                    "question_decision": question,
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
                    rule_id="question.target_reference.v1",
                )
            )
            return {
                "modification_question": question,
                "question_decision": question,
                "pending_modification": PendingModification(),
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
        question = outcome.question
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
            "plan_diff": outcome.plan_diff,
            "plan_diffs": outcome.plan_diffs,
            "modification_question": prepared_question,
            "question_decision": prepared_question,
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
        active_constraints = state.get("active_constraints")
        if command is None or command.operation != CommandOperation.PATCH_CONSTRAINTS:
            raise ValueError("compile_patch requires PATCH_CONSTRAINTS")
        if active_constraints is None:
            question = _prepare_question_decision(
                QuestionDecision(
                    need_question=True,
                    field="active_plan_version_id",
                    continuation="patch_constraints",
                    question="当前方案缺少可恢复的约束快照，请重新生成并选择方案。",
                    severity="blocking",
                    rule_id="question.patch.active_constraints.v1",
                )
            )
            return {
                "modification_question": question,
                "question_decision": question,
                "pending_modification": PendingModification(
                    operation="patch_constraints",
                    constraint_patch=command.constraint_patch,
                    evidence=command.evidence,
                ),
            }
        actor = state["actor"]
        result = constraint_patch_compiler.compile(
            base=active_constraints,
            proposal=command.constraint_patch,
            actor=actor,
            environment=environment_provider(actor),
        )
        if result.question is not None:
            prepared = _prepare_question_decision(result.question)
            return {
                "modification_question": prepared,
                "question_decision": prepared,
                "pending_modification": PendingModification(
                    operation="patch_constraints",
                    constraint_patch=command.constraint_patch,
                    evidence=command.evidence,
                ),
            }
        if result.conflict is not None:
            return {
                "candidate_set": CandidateSet(conflict=result.conflict),
                "mutation_kind": "constraint_patch_conflict",
                "modification_question": None,
                "question_decision": None,
            }
        return {
            "planning_constraints": result.updated_constraints,
            "mutation_kind": "constraint_patch",
            "modification_question": None,
            "question_decision": None,
        }

    def enrichment_node(state: EntryState) -> dict[str, object]:
        environment = environment_provider(state["actor"])
        result = enrichment_service.enrich(
            state["interpretation"],
            state["actor"],
            environment,
        )
        default_fields = state.get("clarification_default_fields", ())
        if default_fields:
            assumptions = list(result.assumptions)
            for field in default_fields:
                if any(
                    item.field == field
                    and item.rule_id == f"clarification.default.{field}.v1"
                    for item in assumptions
                ):
                    continue
                value = getattr(result.constraints, field, None)
                raw_value = (
                    value.value.model_dump(mode="json")
                    if value is not None and hasattr(value.value, "model_dump")
                    else value.value if value is not None else None
                )
                assumptions.append(
                    Assumption(
                        field=field,
                        value=raw_value,
                        reason="用户选择使用该字段的系统默认值",
                        rule_id=f"clarification.default.{field}.v1",
                    )
                )
            result = result.model_copy(update={"assumptions": assumptions})
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
        weather_condition_planning = (
            state["interpretation"].primary_intent == Intent.CHECK_WEATHER
            and _weather_condition_requests_planning(state["interpretation"])
        )
        return {
            "question_decision": _prepare_question_decision(decision),
            "ready_for_planning": (
                not decision.need_question
                and (
                    state["interpretation"].primary_intent in planning_intents
                    or weather_condition_planning
                )
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
        constraints = state.get("planning_constraints") or state["enrichment"].constraints
        candidate_set = planning_service.plan(constraints)
        geocoding_fact = state["enrichment"].geocoding_fact if state.get("enrichment") else None
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
                *(
                    (candidate_set.retrieval_runtime_decision,)
                    if candidate_set.retrieval_runtime_decision is not None
                    else ()
                ),
            ),
        }

    def ask_question_node(state: EntryState) -> dict[str, object]:
        decision = state["question_decision"]
        if decision is None:
            raise ValueError("ask_question requires a QuestionDecision")
        answer = interrupt(_question_payload(decision))
        reply = _coerce_clarification_reply(answer, decision)
        outcome = clarification_resolver.resolve(
            pending_question=decision,
            reply=reply,
            base_interpretation=state["interpretation"],
            pending_modification=state.get("pending_modification"),
        )
        trace = _clarification_runtime_decision(decision, reply, outcome.status)
        common: dict[str, object] = {
            "ready_for_planning": False,
            "clarification_resolution": outcome.status.value,
            "runtime_decisions": (
                *state.get("runtime_decisions", ()),
                trace,
            ),
        }
        if outcome.status == ClarificationResolution.UNRESOLVED:
            common.update(
                {
                    "question_decision": outcome.question,
                    "interpretation": outcome.interpretation,
                }
            )
        elif outcome.status == ClarificationResolution.RESOLVED:
            common.update(
                {
                    "question_decision": None,
                    "interpretation": outcome.interpretation,
                }
            )
        elif outcome.status == ClarificationResolution.MODIFICATION_RESOLVED:
            common.update(
                {
                    "question_decision": None,
                    "interpretation": outcome.interpretation,
                    "pending_modification": None,
                    "modification_question": None,
                }
            )
        elif outcome.status == ClarificationResolution.USE_DEFAULT:
            common.update(
                {
                    "question_decision": None,
                    "interpretation": outcome.interpretation,
                    "clarification_default_fields": (
                        *state.get("clarification_default_fields", ()),
                        outcome.defaulted_field,
                    ),
                }
            )
        elif outcome.status == ClarificationResolution.NEW_REQUEST:
            common.update(
                {
                    "user_input": outcome.value or "",
                    "interpretation": None,
                    "enrichment": None,
                    "question_decision": None,
                    "candidate_set": None,
                    "plan_diff": None,
                    "plan_diffs": (),
                    "clarification_default_fields": (),
                    "pending_modification": None,
                    "planning_constraints": None,
                }
            )
        else:
            common.update(
                {
                    "question_decision": None,
                    "interpretation": outcome.interpretation,
                    "enrichment": None,
                    "candidate_set": None,
                    "clarification_default_fields": (),
                    "pending_modification": None,
                    "modification_question": None,
                }
            )
        return common

    graph = StateGraph(EntryState)
    graph.add_node("router", router_node)
    graph.add_node("compile_patch", compile_patch_node)
    graph.add_node("modify_plan", modify_plan_node)
    graph.add_node("enrichment", enrichment_node)
    graph.add_node("gate", gate_node)
    graph.add_node("ask_question", ask_question_node)
    graph.add_node("planning", planning_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {
            "enrichment": "enrichment",
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
    graph.add_edge("enrichment", "gate")
    graph.add_conditional_edges(
        "gate",
        route_after_gate,
        {"ask_question": "ask_question", "planning": "planning", END: END},
    )
    graph.add_conditional_edges(
        "ask_question",
        route_after_clarification,
        {
            "enrichment": "enrichment",
            "router": "router",
            "compile_patch": "compile_patch",
            "modify_plan": "modify_plan",
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
    if field in {"location", "date", "time_window", "max_distance_km", "total_distance_km", "party"}:
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
        "question": decision.question,
        "severity": decision.severity,
        "rule_id": decision.rule_id,
        "clarification_id": decision.clarification_id,
        "attempt": decision.attempt,
        "max_attempts": decision.max_attempts,
        "allow_free_text": decision.allow_free_text,
        "continuation": decision.continuation,
        "options": [item.model_dump(mode="json") for item in decision.options],
    }


def _coerce_clarification_reply(
    answer: object,
    decision: QuestionDecision,
) -> ClarificationReply:
    """Keep direct ``Command(resume="text")`` callers backwards compatible."""

    if isinstance(answer, ClarificationReply):
        return answer
    if isinstance(answer, dict):
        payload = dict(answer)
        payload.setdefault("clarification_id", decision.clarification_id or "legacy")
        payload.setdefault("action", ClarificationAction.ANSWER.value)
        return ClarificationReply.model_validate(payload)
    return ClarificationReply(
        clarification_id=decision.clarification_id or "legacy",
        action=ClarificationAction.ANSWER,
        value=str(answer) if answer is not None else None,
    )


def route_after_clarification(state: EntryState) -> str:
    status = state.get("clarification_resolution")
    if status in {
        ClarificationResolution.RESOLVED.value,
        ClarificationResolution.USE_DEFAULT.value,
    }:
        return "enrichment"
    if status == ClarificationResolution.MODIFICATION_RESOLVED.value:
        command = state.get("interpretation").conversation_command if state.get("interpretation") else None
        if command is not None and command.operation == CommandOperation.PATCH_CONSTRAINTS:
            return "compile_patch"
        return "modify_plan"
    if status == ClarificationResolution.NEW_REQUEST.value:
        return "router"
    if status == ClarificationResolution.UNRESOLVED.value:
        return "ask_question"
    return END


def route_after_patch(state: EntryState) -> str:
    if state.get("mutation_kind") == "constraint_patch":
        return "planning"
    if state.get("mutation_kind") == "constraint_patch_conflict":
        return END
    question = state.get("modification_question")
    if question is not None and question.need_question:
        return "ask_question"
    return END


def route_after_modify(state: EntryState) -> str:
    """Only incomplete mutations enter the interrupt/resume path."""
    question = state.get("modification_question")
    if question is not None and question.need_question and (
        question.field in {"target_reference", "locked_stop", "constraint_patch"}
        or question.continuation == "patch_constraints"
    ):
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
        adapter="clarification_resolver",
        model_invoked=False,
        model_name=None,
        attempts=0,
        fallback_reason=None,
        diagnostic_code=f"clarification_{status.value}",
        diagnostic_paths=((decision.field,) if decision.field else ()),
        requested_mode=reply.action.value,
        wire_schema_version="clarification.v1",
    )


def _weather_condition_requests_planning(interpretation: Interpretation) -> bool:
    """Return whether a weather turn also contains an explicit plan condition.

    ``CHECK_WEATHER`` remains a read-only weather query by default.  A bounded
    condition such as “下雨就安排室内活动” is different: the Router has
    already supplied a planning preference/scene constraint, so the existing
    deterministic planning chain can consume it after WeatherProvider returns
    the actual fact.  This helper intentionally does not parse arbitrary
    conditional language or make a weather decision itself.
    """

    raw = interpretation.raw_constraints
    return bool(
        raw.preferences
        or raw.scene_tags
        or raw.diet_tags
        or raw.avoid
        or raw.required_stop_roles
        or raw.exact_stop_count is not None
        or raw.time_text
        or raw.explicit_time_window
        or raw.departure_at_text
        or raw.return_by_text
        or raw.duration_minutes is not None
    )


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
            DateReference,
            EnrichmentResult,
            IdentityType,
            Intent,
            Interpretation,
            CommandOperation,
            ConstraintPatch,
            CriterionStrength,
            ConversationCommand,
            NormalizedConstraints,
            PendingModification,
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

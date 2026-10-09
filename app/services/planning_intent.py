"""受约束的 PlanningIntent 决策 seam。

模型只能提出有限的结构偏好；硬约束、事实查询、方案生成和可行性证明仍由
确定性代码负责。RuleBased provider 是默认基线，也是所有失败路径的回退。
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.domain.constraints import PlanRequest, StopRole, TimeScope
from app.domain.semantics import (
    EvidenceRef,
    SOFT_OBJECTIVE_ALIASES,
    SemanticQuery,
    SemanticRequest,
    SoftObjective,
    SoftObjectiveKind,
    TimeCoverageObjective,
)
from app.domain.planning import (
    PlanPace,
    PlanStructureProposal,
    PlanningIntent,
    PlanningIntentDecision,
    RoleQueryProposal,
)
from app.services.llm_compat import thinking_extra_body, structured_output_schema
from app.services.model_usage import ModelTokenUsage, TokenUsageAccumulator
from app.services.model_errors import model_failure_reason


PROMPT_VERSION = "planning-intent.v3"
DEFAULT_MODEL_TIMEOUT_SECONDS = 15.0


class StructuredPlanningModel(Protocol):
    """最小结构化模型依赖；测试可注入 fake，不访问真实网络。"""

    def invoke(self, messages: list[object]) -> object:
        ...


class PlanningIntentProvider(Protocol):
    def decide(self, constraints: PlanRequest) -> PlanningIntentDecision:
        ...


class RuleBasedPlanningIntentProvider:
    """当前确定性规则的唯一实现，作为默认基线和安全回退。"""

    def decide(self, constraints: PlanRequest) -> PlanningIntentDecision:
        return PlanningIntentDecision(
            intent=build_rule_based_planning_intent(constraints),
            source="rule_based",
            confidence=1.0,
            attempts=0,
            fallback_reason=None,
            prompt_version="rule-based.v1",
            model_name=None,
            proposal_present=False,
            proposal_accepted=False,
        )


class LlmPlanningIntentProvider:
    """调用一次结构化模型，最多对非法结构做一次格式修复。"""

    def __init__(
        self,
        model: StructuredPlanningModel,
        fallback: PlanningIntentProvider | None = None,
        *,
        prompt_version: str = PROMPT_VERSION,
        model_name: str | None = None,
    ) -> None:
        self._model = model
        self._fallback = fallback or RuleBasedPlanningIntentProvider()
        self._prompt_version = prompt_version
        self._model_name = model_name

    def decide(self, constraints: PlanRequest) -> PlanningIntentDecision:
        baseline = self._fallback.decide(constraints)
        if not _should_call_model(constraints):
            return baseline

        messages: list[object] = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_build_context(constraints, baseline.intent)),
        ]
        token_usage = TokenUsageAccumulator()
        try:
            raw_result = self._model.invoke(messages)
            token_usage.record(raw_result)
        except Exception as error:
            token_usage.record_unknown()
            return self._fallback_decision(
                baseline,
                1,
                _model_failure_reason(error),
                token_usage.total,
            )
        try:
            proposal = _validate_proposal(raw_result)
        except Exception:
            # 结构化 Runnable 使用 include_raw=True 时，解析失败会以
            # {raw, parsed, parsing_error} 返回到这里，而不是在 invoke() 外抛出。
            # 因而生产模型和测试 fake 都真正共享一次格式修复路径。
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次结构提议未通过结构校验。只返回合法的 "
                        "PlanStructureProposal v3 JSON，不要解释。错误类型：invalid_output"
                    )
                ),
            ]
            try:
                raw_retry = self._model.invoke(retry_messages)
                token_usage.record(raw_retry)
            except Exception as error:
                token_usage.record_unknown()
                return self._fallback_decision(
                    baseline,
                    2,
                    _model_failure_reason(error),
                    token_usage.total,
                )
            try:
                proposal = _validate_proposal(raw_retry)
            except Exception:
                return self._fallback_decision(
                    baseline,
                    2,
                    "invalid_proposal_parse",
                    token_usage.total,
                )
            attempts = 2
        else:
            attempts = 1

        try:
            intent = _project_proposal_to_intent(proposal, baseline.intent)
        except ValueError as error:
            return self._fallback_decision(
                baseline,
                attempts,
                f"invalid_proposal_contract:{_safe_contract_code(error, 'proposal_out_of_bounds')}",
                token_usage.total,
                proposal_present=True,
                structure_proposal=proposal,
            )
        usage = token_usage.total
        return PlanningIntentDecision(
            intent=intent,
            source="llm",
            confidence=1.0,
            attempts=attempts,
            fallback_reason=None,
            prompt_version=self._prompt_version,
            model_name=self._model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            proposal_present=True,
            proposal_accepted=True,
            structure_proposal=proposal,
        )

    def _fallback_decision(
        self,
        baseline: PlanningIntentDecision,
        attempts: int,
        reason: str,
        token_usage: ModelTokenUsage,
        *,
        proposal_present: bool = False,
        structure_proposal: PlanStructureProposal | None = None,
    ) -> PlanningIntentDecision:
        return PlanningIntentDecision(
            intent=baseline.intent,
            source="fallback",
            confidence=baseline.confidence,
            attempts=attempts,
            fallback_reason=reason,
            prompt_version=self._prompt_version,
            model_name=self._model_name,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
            proposal_present=proposal_present,
            proposal_accepted=False,
            proposal_rejection_reason=reason,
            structure_proposal=structure_proposal,
        )


def build_rule_based_planning_intent(
    constraints: PlanRequest,
) -> PlanningIntent:
    """Build soft semantics only; structure comes from constraints or Proposal."""
    preferences = {
        item.strip().casefold()
        for item in constraints.preferences
        if item.strip()
    }
    if preferences & {
        "轻松",
        "松弛",
        "不赶",
        "休闲",
        "不希望太累",
        "不太累",
        "不累",
    }:
        pace = PlanPace.RELAXED
    elif (
        preferences & {"丰富", "充实", "多玩几个", "尽量多"}
        or _is_all_day_request(constraints)
    ):
        pace = PlanPace.FULL
    else:
        pace = PlanPace.BALANCED
    return PlanningIntent(
        pace=pace,
        semantic_request=_build_rule_semantic_request(constraints),
        time_coverage=_build_time_coverage_objective(constraints),
    )


def _is_all_day_request(constraints: PlanRequest) -> bool:
    """Read the typed trip scope; never infer semantics from rule identifiers."""

    return (
        constraints.trip_time_scope is not None
        and constraints.trip_time_scope.value == TimeScope.ALL_DAY
    )


def _build_time_coverage_objective(
    constraints: PlanRequest,
) -> TimeCoverageObjective | None:
    """Compile the explicit all-day language into a bounded soft objective.

    ``PlanningWindow`` remains the executable availability range.  The
    objective is added only when the user actually said “一整天”; ordinary
    explicit ranges and fuzzy morning/afternoon requests do not inherit it.
    Dinner or an evening-scoped activity extends the target to the evening,
    while an all-day request without either remains a morning+afternoon goal.
    """

    if not _is_all_day_request(constraints):
        return None
    periods = [TimeScope.MORNING, TimeScope.AFTERNOON]
    required_roles = (
        constraints.required_stop_roles.value
        if constraints.required_stop_roles is not None
        else ()
    )
    if (
        StopRole.DINNER in required_roles
        or (
            constraints.activity_time_scope is not None
            and constraints.activity_time_scope.value == TimeScope.EVENING
        )
    ):
        periods.append(TimeScope.EVENING)
    evidence = constraints.trip_time_scope.raw_text or "一整天"
    return TimeCoverageObjective(
        target_periods=tuple(periods),
        strength="preferred",
        evidence=evidence,
    )


def build_default_planning_intent_provider(
    mode: str | None = None,
) -> PlanningIntentProvider:
    """Build the configured provider without changing the default rule baseline."""
    selected_mode = (mode or os.getenv("HFT_PLANNING_INTENT_MODE", "rule")).lower()
    if selected_mode == "rule":
        return RuleBasedPlanningIntentProvider()
    if selected_mode != "llm":
        raise RuntimeError("HFT_PLANNING_INTENT_MODE must be one of: rule, llm")
    api_key = os.getenv("LLM_API")
    if not api_key:
        raise RuntimeError("HFT_PLANNING_INTENT_MODE=llm requires LLM_API")
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        timeout=_model_timeout_seconds(),
        max_retries=0,
        # Function Calling 把提议 Schema 传给兼容 OpenAI 的 provider；本地
        # Pydantic 继续负责严格校验。
        max_tokens=2048,
        extra_body=thinking_extra_body(model_name),
    )
    return LlmPlanningIntentProvider(
        llm.with_structured_output(
            structured_output_schema(model_name, PlanStructureProposal),
            method="function_calling",
            include_raw=True,
        ),
        model_name=model_name,
    )


def _model_timeout_seconds() -> float:
    raw_value = os.getenv(
        "HFT_PLANNING_INTENT_TIMEOUT_SECONDS",
        str(DEFAULT_MODEL_TIMEOUT_SECONDS),
    )
    try:
        timeout = float(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "HFT_PLANNING_INTENT_TIMEOUT_SECONDS must be a positive number"
        ) from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError(
            "HFT_PLANNING_INTENT_TIMEOUT_SECONDS must be a positive number"
        )
    return timeout


_model_failure_reason = model_failure_reason


def _member_evidence(constraints: PlanRequest) -> tuple[str, ...]:
    """Read relationship phrases from the normalized party profile, if any."""

    if constraints.party is None:
        return ()
    return tuple(item.strip() for item in constraints.party.value.members if item.strip())


def _should_call_model(constraints: PlanRequest) -> bool:
    if (
        constraints.exact_stop_count is not None
        and constraints.exact_stop_count.value == 1
        and constraints.required_stop_roles is not None
        and constraints.required_stop_roles.value == (StopRole.DINNER,)
    ):
        return False
    # S2 LLM 只负责可能改变规划结构的模糊语义。饮食标签和避开项
    # 由既有 Catalog/过滤链路处理，当前 PlanningIntent 不消费它们，
    # 因此仅凭这两类字段调用模型没有业务收益。
    return bool(
        constraints.preferences
        or constraints.scene_tags
        or constraints.activity_time_scope is not None
        or _member_evidence(constraints)
    )


def _validate_proposal(
    result: object,
) -> PlanStructureProposal:
    # ChatOpenAI(...).with_structured_output(..., include_raw=True) returns an
    # envelope. Local Pydantic validation remains the final authority, so a
    # provider-specific parser cannot silently bypass the repair contract.
    if isinstance(result, dict) and (
        "parsed" in result or "parsing_error" in result
    ):
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            raise ValueError(
                f"structured proposal parsing failed: {parsing_error}"
            )
        parsed = result.get("parsed")
        if parsed is None:
            raise ValueError("structured proposal parser returned no proposal")
        return _validate_proposal(parsed)
    if isinstance(result, PlanStructureProposal):
        return result
    if isinstance(result, str):
        payload = json.loads(result)
    else:
        payload = result
    return PlanStructureProposal.model_validate(payload)


def _project_proposal_to_intent(
    proposal: PlanStructureProposal,
    baseline: PlanningIntent,
) -> PlanningIntent:
    """Project only proposal semantics; structural legality stays in Compiler."""

    roles = tuple(slot.role for slot in proposal.slots)
    semantic_request = _project_proposal_semantics(proposal, baseline, roles)
    return PlanningIntent(
        pace=proposal.pace,
        semantic_request=semantic_request,
        # Coverage is derived from the user's temporal evidence, not invented
        # by the model's structure proposal. Preserve the baseline objective
        # while allowing the model to refine only the semantic payload.
        time_coverage=baseline.time_coverage,
    )


def _project_proposal_semantics(
    proposal: PlanStructureProposal,
    baseline: PlanningIntent,
    roles: tuple[StopRole, ...],
) -> SemanticRequest:
    """Carry only evidence-grounded soft semantics from the wire proposal.

    ``PlanSpecCompiler`` remains the authority for accepting or rejecting the
    proposal.  This helper is intentionally a tolerant projection so invalid
    references can be reported by that compiler instead of being rejected by a
    second, drifting validator in the provider.
    """

    known = {item.evidence_id for item in baseline.semantic_request.evidence}
    valid_objectives = tuple(
        objective
        for objective in proposal.objectives
        if objective.evidence_refs
        and set(objective.evidence_refs).issubset(known)
        and (
            objective.target_role is None
            or objective.target_role in roles
        )
    )
    objectives = _merge_objectives(
        baseline.semantic_request,
        valid_objectives,
    )
    valid_role_queries: dict[str, RoleQueryProposal] = {}
    for raw_role, query in proposal.role_queries.items():
        try:
            role = StopRole(raw_role)
        except ValueError:
            continue
        if role not in roles or not set(query.evidence_refs).issubset(known):
            continue
        valid_role_queries[raw_role] = query
    semantic_request = _merge_role_queries(
        baseline.semantic_request.model_copy(update={"objectives": objectives}),
        valid_role_queries,
        baseline.semantic_request,
        allowed_roles=set(roles),
    )
    return semantic_request


def _merge_objectives(
    baseline: SemanticRequest,
    proposed: tuple[SoftObjective, ...],
) -> tuple[SoftObjective, ...]:
    """Merge model objectives over the Rule baseline without duplicate kinds."""

    merged: dict[tuple[SoftObjectiveKind, StopRole | None], SoftObjective] = {}
    for objective in proposed:
        merged[(objective.kind, objective.target_role)] = objective
    for objective in baseline.objectives:
        merged.setdefault((objective.kind, objective.target_role), objective)
    return tuple(merged.values())


def _merge_role_queries(
    semantic_request: SemanticRequest,
    role_queries: dict[str, RoleQueryProposal],
    baseline: SemanticRequest,
    *,
    allowed_roles: set[StopRole],
) -> SemanticRequest:
    """Compile bounded role queries into the shared semantic contract."""

    if not role_queries:
        return semantic_request
    baseline_evidence = {item.evidence_id: item for item in baseline.evidence}
    known_evidence = set(baseline_evidence)
    evidence = list(semantic_request.evidence)
    evidence_ids = {item.evidence_id for item in evidence}
    queries = list(semantic_request.queries)
    for raw_role, query in role_queries.items():
        try:
            role = StopRole(raw_role)
        except ValueError as error:
            raise ValueError("role query references an unknown role") from error
        if role not in allowed_roles:
            raise ValueError("role query references a role outside the baseline")
        if not set(query.evidence_refs).issubset(known_evidence):
            raise ValueError("role query has ungrounded evidence")
        for evidence_id in query.evidence_refs:
            if evidence_id not in evidence_ids:
                evidence.append(baseline_evidence[evidence_id])
                evidence_ids.add(evidence_id)
        queries.append(
            SemanticQuery(
                query_id=f"semantic.query.role.{role.value}",
                text=query.text,
                target_role=role,
                evidence_refs=query.evidence_refs,
            )
        )
    deduped: dict[str, SemanticQuery] = {query.query_id: query for query in queries}
    return SemanticRequest(
        evidence=tuple(evidence),
        objectives=semantic_request.objectives,
        queries=tuple(deduped.values()),
    )


def _build_rule_semantic_request(
    constraints: PlanRequest,
) -> SemanticRequest:
    """Translate known semantic evidence to finite objectives while retaining text."""

    values_by_field = (
        ("members", _member_evidence(constraints)),
        ("preferences", constraints.preferences),
        ("scene_tags", constraints.scene_tags),
        ("diet_tags", constraints.diet_tags),
        ("avoid", constraints.avoid),
    )
    evidence: list[EvidenceRef] = []
    objectives: list[SoftObjective] = []
    queries: list[SemanticQuery] = []
    seen_values: set[tuple[str, str]] = set()
    for source_field, values in values_by_field:
        for index, value in enumerate(values, start=1):
            text = value.strip()
            if not text or (source_field, text.casefold()) in seen_values:
                continue
            seen_values.add((source_field, text.casefold()))
            evidence_id = f"user.{source_field}.{index}"
            evidence.append(
                EvidenceRef(
                    evidence_id=evidence_id,
                    source_type="user_message",
                    source_field=source_field,
                    summary=text,
                    confidence=1.0,
                )
            )
            normalized = text.casefold()
            matched_kind = _match_objective_alias(normalized, source_field)
            if matched_kind is not None:
                objectives.append(
                    SoftObjective(
                        kind=matched_kind,
                        strength="preferred",
                        evidence_refs=(evidence_id,),
                    )
                )
            queries.append(
                SemanticQuery(
                    query_id=f"semantic.query.{source_field}.{index}",
                    text=text,
                    evidence_refs=(evidence_id,),
                )
            )
    return SemanticRequest(
        evidence=tuple(evidence),
        objectives=tuple(objectives),
        queries=tuple(queries),
    )


def _match_objective_alias(
    normalized_text: str,
    source_field: str,
) -> SoftObjectiveKind | None:
    """Map one normalized evidence value to the finite objective vocabulary.

    Exact matches remain the default.  Membership phrases commonly contain a
    relation word (``跟女朋友``/``和对象``), so only the ``members`` field gets
    bounded substring matching; this avoids turning a negated preference such
    as ``不安静`` into a positive ``quiet`` objective.
    """

    for kind, aliases in SOFT_OBJECTIVE_ALIASES.items():
        for alias in aliases:
            candidate = alias.casefold()
            if normalized_text == candidate:
                return kind
            if source_field == "members" and candidate in normalized_text:
                return kind
    return None


def _safe_contract_code(error: Exception, fallback: str) -> str:
    """Keep contract diagnostics to a fixed code, never model/provider text."""

    value = str(error).strip()
    return value if re.fullmatch(r"[a-z0-9_]+", value) else fallback


def _build_context(
    constraints: PlanRequest,
    baseline: PlanningIntent,
) -> str:
    context = {
        "members": _member_evidence(constraints),
        "preferences": constraints.preferences,
        "diet_tags": constraints.diet_tags,
        "scene_tags": constraints.scene_tags,
        "avoid": constraints.avoid,
        "planning_window": {
            "date": (
                constraints.planning_window.date.value.isoformat()
                if constraints.planning_window.date is not None
                else None
            ),
            "start_at": (
                constraints.planning_window.start_at.value
                if constraints.planning_window.start_at is not None
                else None
            ),
            "end_at": (
                constraints.planning_window.end_at.value
                if constraints.planning_window.end_at is not None
                else None
            ),
        },
        "trip_time_scope": (
            constraints.trip_time_scope.value.value
            if constraints.trip_time_scope is not None
            else None
        ),
        "exact_stop_count": (
            constraints.exact_stop_count.value
            if constraints.exact_stop_count is not None
            else None
        ),
        "required_stop_roles": (
            [role.value for role in constraints.required_stop_roles.value]
            if constraints.required_stop_roles is not None
            else []
        ),
        "activity_time_scope": (
            constraints.activity_time_scope.value.value
            if constraints.activity_time_scope is not None
            else None
        ),
        "baseline_semantics": baseline.model_dump(mode="json"),
        "explicit_roles": (
            [role.value for role in constraints.required_stop_roles.value]
            if constraints.required_stop_roles is not None
            else []
        ),
        "available_evidence_ids": [
            item.evidence_id for item in baseline.semantic_request.evidence
        ],
        "allowed_objective_kinds": [kind.value for kind in SOFT_OBJECTIVE_ALIASES],
    }
    return (
        "请根据用户需求提出一个 PlanStructureProposal v3。slots 是 1-4 个有序角色，"
        "每个 slot 的 inclusion 只能是 core 或 optional；允许未注册的新角色序列和重复 activity。"
        "不得删除或重排用户明确要求的角色；不得修改用户硬约束。role_queries 是按角色的检索表达，"
        "objectives 只能使用 allowed_objective_kinds 中的有限目标，每个 objective 必须至少引用一个 "
        "available_evidence_ids；不得创建 evidence 或输出 confidence。role_queries 的 evidence_refs "
        "也只能引用 available_evidence_ids。不要输出 POI、路线、价格、营业、天气、"
        "确切开始时间或可行性结论；core 只是模型建议，不等于用户硬约束。\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


_SYSTEM_PROMPT = """你是 HappyFreeTime 的受约束 PlanningIntent 节点。
只输出一个合法的 PlanStructureProposal v3 JSON 对象，不要返回 Markdown 代码围栏或解释文字。
slots 必须是 1 到 4 个有序角色；允许提出当前注册骨架中没有的新序列，也允许重复 activity。
每个 slot 必须标记 inclusion=core 或 optional。core 是结构建议，不是用户硬约束。
objectives 只能使用输入中的 allowed_objective_kinds；每个 objective 必须引用至少一个
available_evidence_ids，不能创建 evidence、confidence 或新的目标种类。
不得删除或重排用户明确指定的角色；午饭必须在晚饭前；最多一个 lunch、一个 dinner。
role_queries 必须绑定输入中的 evidence_id。不得输出 POI、resource_id、路线、距离、价格、营业、天气、
确切开始时间、Availability 或最终可行性。"""

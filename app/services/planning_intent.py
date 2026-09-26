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

from app.domain.constraints import NormalizedConstraints, StopRole
from app.domain.semantics import (
    EvidenceRef,
    SOFT_OBJECTIVE_ALIASES,
    SemanticQuery,
    SemanticRequest,
    SoftObjective,
    SoftObjectiveKind,
)
from app.domain.planning import (
    PlanPace,
    PlanSkeleton,
    PlanStructureProposal,
    PlanningIntent,
    PlanningIntentDecision,
    PlanningIntentProposal,
    PlanningSlot,
    RoleQueryProposal,
)
from app.services.llm_compat import thinking_extra_body, structured_output_schema
from app.services.model_usage import ModelTokenUsage, TokenUsageAccumulator
from app.services.model_errors import model_failure_reason


ACTIVITY_MEAL_SKELETON = PlanSkeleton(
    skeleton_id="activity-meal-v1",
    roles=(StopRole.ACTIVITY, StopRole.MEAL),
)
ACTIVITY_ONLY_SKELETON = PlanSkeleton(
    skeleton_id="activity-only-v1",
    roles=(StopRole.ACTIVITY,),
)
DINNER_ONLY_SKELETON = PlanSkeleton(
    skeleton_id="dinner-only-v1",
    roles=(StopRole.DINNER,),
)
LUNCH_ONLY_SKELETON = PlanSkeleton(
    skeleton_id="lunch-only-v1",
    roles=(StopRole.LUNCH,),
)
LUNCH_ACTIVITY_DINNER_SKELETON = PlanSkeleton(
    skeleton_id="lunch-activity-dinner-v1",
    roles=(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
)
ACTIVITY_BREAK_DINNER_SKELETON = PlanSkeleton(
    skeleton_id="activity-break-dinner-v1",
    roles=(StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER),
)
ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON = PlanSkeleton(
    skeleton_id="activity-lunch-activity-dinner-v1",
    roles=(
        StopRole.ACTIVITY,
        StopRole.LUNCH,
        StopRole.ACTIVITY,
        StopRole.DINNER,
    ),
)
ALL_PLAN_SKELETONS: tuple[PlanSkeleton, ...] = (
    ACTIVITY_MEAL_SKELETON,
    ACTIVITY_ONLY_SKELETON,
    DINNER_ONLY_SKELETON,
    LUNCH_ONLY_SKELETON,
    LUNCH_ACTIVITY_DINNER_SKELETON,
    ACTIVITY_BREAK_DINNER_SKELETON,
    ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON,
)

# Explicit one-stop structures are intentionally a small closed vocabulary.
# The role remains the source of candidate/resource semantics; the skeleton id
# makes the chosen product contract visible in traces and persisted plans.
SINGLE_STOP_ROLES = frozenset(
    {StopRole.ACTIVITY, StopRole.LUNCH, StopRole.DINNER}
)

PROMPT_VERSION = "planning-intent.v2"
MIN_LLM_CONFIDENCE = 0.6
DEFAULT_MODEL_TIMEOUT_SECONDS = 15.0


class StructuredPlanningModel(Protocol):
    """最小结构化模型依赖；测试可注入 fake，不访问真实网络。"""

    def invoke(self, messages: list[object]) -> object:
        ...


class PlanningIntentProvider(Protocol):
    def decide(self, constraints: NormalizedConstraints) -> PlanningIntentDecision:
        ...


class RuleBasedPlanningIntentProvider:
    """当前确定性规则的唯一实现，作为默认基线和安全回退。"""

    def decide(self, constraints: NormalizedConstraints) -> PlanningIntentDecision:
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

    def decide(self, constraints: NormalizedConstraints) -> PlanningIntentDecision:
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
                        "PlanStructureProposal v2 JSON，不要解释。错误类型：invalid_output"
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

        if isinstance(proposal, PlanStructureProposal):
            try:
                intent = _accept_v2_proposal(proposal, constraints, baseline.intent)
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

        # Legacy proposal DTOs remain readable for old checkpoints and local
        # tests.  They are never used as the live provider schema, but retain
        # the one-format-repair and confidence behavior during migration.
        if proposal.confidence < MIN_LLM_CONFIDENCE:
            return self._fallback_decision(
                baseline,
                attempts,
                "low_confidence",
                token_usage.total,
                proposal_present=bool(proposal.slots),
            )
        try:
            intent = _accept_proposal(proposal, constraints, baseline.intent)
        except ValueError as error:
            return self._fallback_decision(
                baseline,
                attempts,
                f"invalid_proposal_contract:{_safe_contract_code(error, 'proposal_out_of_bounds')}",
                token_usage.total,
                proposal_present=bool(proposal.slots),
            )
        usage = token_usage.total
        return PlanningIntentDecision(
            intent=intent,
            source="llm",
            confidence=proposal.confidence,
            attempts=attempts,
            fallback_reason=None,
            prompt_version=self._prompt_version,
            model_name=self._model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            proposal_present=bool(proposal.slots),
            proposal_accepted=True,
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
    constraints: NormalizedConstraints,
) -> PlanningIntent:
    """从 PlanningService 迁移而来的原规则，保持行为等价。"""
    window = constraints.time_window.value
    if (
        constraints.exact_stop_count is not None
        and constraints.exact_stop_count.value == 1
        and constraints.required_stop_roles is not None
        and len(constraints.required_stop_roles.value) == 1
        and constraints.required_stop_roles.value[0] in SINGLE_STOP_ROLES
    ):
        single_role = constraints.required_stop_roles.value[0]
        return PlanningIntent(
            required_roles=(single_role,),
            optional_roles=(),
            minimum_stops=1,
            maximum_stops=1,
            pace=PlanPace.RELAXED,
            coverage=(constraints.time_scope.value if constraints.time_scope else None),
            slots=(PlanningSlot(role=single_role, required=True),),
            evidence={
                "exact_stop_count": constraints.exact_stop_count.raw_text or "1",
                "required_stop_roles": constraints.required_stop_roles.raw_text or single_role.value,
                "time_window": f"{window.start}-{window.end}",
            },
            semantic_request=_build_rule_semantic_request(constraints),
        )

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
        maximum_stops = 2
    elif preferences & {"丰富", "充实", "多玩几个", "尽量多"}:
        pace = PlanPace.FULL
        maximum_stops = 4
    else:
        pace = PlanPace.BALANCED
        maximum_stops = 4

    start_minutes = _clock_minutes(window.start)
    end_minutes = _clock_minutes(window.end)
    includes_lunch = start_minutes <= 13 * 60 and end_minutes >= 12 * 60
    includes_dinner = start_minutes <= 19 * 60 and end_minutes >= 18 * 60
    includes_break = start_minutes <= 16 * 60 and end_minutes >= 18 * 60
    optional_roles = [StopRole.MEAL]
    if includes_lunch:
        optional_roles.append(StopRole.LUNCH)
    if includes_break:
        optional_roles.append(StopRole.BREAK)
    if includes_dinner:
        optional_roles.append(StopRole.DINNER)
    precedence: list[tuple[StopRole, StopRole]] = []
    if includes_lunch and includes_dinner:
        precedence.append((StopRole.LUNCH, StopRole.DINNER))
    if includes_break and includes_dinner:
        precedence.append((StopRole.BREAK, StopRole.DINNER))
    return PlanningIntent(
        required_roles=(StopRole.ACTIVITY,),
        optional_roles=tuple(optional_roles),
        precedence=tuple(precedence),
        minimum_stops=2,
        maximum_stops=maximum_stops,
        pace=pace,
        coverage=(constraints.time_scope.value if constraints.time_scope else None),
        slots=(PlanningSlot(role=StopRole.ACTIVITY, required=True),),
        evidence={
            "time_window": f"{window.start}-{window.end}",
            "pace": pace.value,
        },
        semantic_request=_build_rule_semantic_request(constraints),
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


def _member_evidence(constraints: NormalizedConstraints) -> tuple[str, ...]:
    """Read relationship phrases from the normalized party profile, if any."""

    if constraints.party is None:
        return ()
    return tuple(item.strip() for item in constraints.party.value.members if item.strip())


def _should_call_model(constraints: NormalizedConstraints) -> bool:
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
        or _member_evidence(constraints)
    )


def _validate_proposal(
    result: object,
) -> PlanStructureProposal | PlanningIntentProposal:
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
    if isinstance(result, PlanningIntentProposal):
        return result
    if isinstance(result, PlanStructureProposal):
        return result
    if isinstance(result, str):
        payload = json.loads(result)
    else:
        payload = result
    if isinstance(payload, dict) and (
        payload.get("schema_version") == "plan-structure-proposal.v2"
        or any(
            isinstance(item, dict) and "inclusion" in item
            for item in payload.get("slots", ())
        )
    ):
        return PlanStructureProposal.model_validate(payload)
    return PlanningIntentProposal.model_validate(payload)


def _accept_v2_proposal(
    proposal: PlanStructureProposal,
    constraints: NormalizedConstraints,
    baseline: PlanningIntent,
) -> PlanningIntent:
    """Project a wire proposal into the compatibility domain view.

    Structural legality belongs exclusively to ``PlanSpecCompiler``.  This
    projection therefore preserves the ordered proposal, carries only
    evidence-grounded semantic material into the old domain object, and does
    not reject a role sequence before the compiler can record its stable
    rejection code.  Invalid semantic references are omitted from this
    compatibility view; the compiler still rejects the original proposal and
    selects the deterministic Rule fallback.
    """

    roles = tuple(slot.role for slot in proposal.slots)
    core_roles = tuple(dict.fromkeys(
        slot.role for slot in proposal.slots if slot.inclusion == "core"
    ))
    optional_roles = tuple(dict.fromkeys(
        slot.role for slot in proposal.slots if slot.inclusion == "optional"
    ))
    semantic_request = _project_v2_semantics(proposal, baseline, roles)
    return PlanningIntent(
        required_roles=core_roles,
        optional_roles=optional_roles,
        precedence=tuple(
            (left, right)
            for left, right in zip(roles, roles[1:])
            if left != right
        ),
        minimum_stops=max(
            1, sum(slot.inclusion == "core" for slot in proposal.slots)
        ),
        maximum_stops=len(proposal.slots),
        pace=proposal.pace,
        coverage=baseline.coverage,
        slots=tuple(
            PlanningSlot(
                role=slot.role,
                required=slot.inclusion == "core",
            )
            for slot in proposal.slots
        ),
        evidence=dict(baseline.evidence),
        semantic_request=semantic_request,
    )


def _project_v2_semantics(
    proposal: PlanStructureProposal,
    baseline: PlanningIntent,
    roles: tuple[StopRole, ...],
) -> SemanticRequest:
    """Carry only grounded V2 semantic material into the legacy domain view.

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


def _accept_proposal(
    proposal: PlanningIntentProposal,
    constraints: NormalizedConstraints,
    baseline: PlanningIntent,
) -> PlanningIntent:
    # Baseline required roles encode the existing product structure; a soft
    # preference may not remove or invent a required role.
    proposed_slots = tuple(slot.role for slot in proposal.slots)
    accepted_slots: tuple[PlanningSlot, ...] = ()
    if proposal.slots:
        if not proposed_slots:
            raise ValueError("slots cannot be empty when provided")
        effective_required_roles = tuple(
            dict.fromkeys(slot.role for slot in proposal.slots if slot.required)
        )
        effective_optional_roles = tuple(
            dict.fromkeys(slot.role for slot in proposal.slots if not slot.required)
        )
        # A model structure proposal is still only a soft proposal: it may
        # enrich the closed-world baseline but cannot remove a deterministic
        # required role.
        if not set(baseline.required_roles).issubset(proposed_slots):
            raise ValueError("slots cannot remove a baseline required role")
        if proposal.required_roles and tuple(dict.fromkeys(proposal.required_roles)) != effective_required_roles:
            raise ValueError("slot roles and required_roles must agree")
        if proposal.optional_roles and tuple(dict.fromkeys(proposal.optional_roles)) != effective_optional_roles:
            raise ValueError("slot roles and optional_roles must agree")
        required_roles = effective_required_roles
        optional_roles = effective_optional_roles
        # Preserve the model's bounded order and repeated roles.  The role
        # sets above are only compatibility summaries for the legacy fields;
        # the ordered slots are what the structure matcher consumes.
        accepted_slots = tuple(
            PlanningSlot(role=slot.role, required=slot.required)
            for slot in proposal.slots
        )
    else:
        required_roles = proposal.required_roles
        optional_roles = proposal.optional_roles
    if not set(baseline.required_roles).issubset(required_roles):
        raise ValueError("required roles cannot remove a baseline required role")
    allowed_roles = set(baseline.required_roles) | set(baseline.optional_roles)
    if not set(required_roles).issubset(allowed_roles):
        raise ValueError("required role is not allowed by the baseline intent")
    if not set(optional_roles).issubset(allowed_roles):
        raise ValueError("optional role is not allowed by the baseline intent")
    if set(required_roles) & set(optional_roles):
        raise ValueError("required and optional roles must be disjoint")
    for before, after in proposal.precedence:
        if before == after or before not in allowed_roles or after not in allowed_roles:
            raise ValueError("precedence references a role outside the allowed intent")

    exact_count = (
        constraints.exact_stop_count.value
        if constraints.exact_stop_count is not None
        else None
    )
    if exact_count is not None:
        if proposal.minimum_stops != exact_count or proposal.maximum_stops != exact_count:
            raise ValueError("exact stop count cannot be changed")
    elif proposal.minimum_stops < baseline.minimum_stops:
        raise ValueError("minimum stop count cannot be weakened")
    if proposal.maximum_stops < proposal.minimum_stops:
        raise ValueError("invalid stop range")
    effective_proposal = proposal.model_copy(
        update={
            "required_roles": required_roles,
            "optional_roles": optional_roles,
        }
    )
    if not any(
        _skeleton_matches_proposal(skeleton, effective_proposal)
        for skeleton in ALL_PLAN_SKELETONS
    ):
        raise ValueError("proposal does not match an existing plan skeleton")
    semantic_request = _sanitize_semantic_request(
        proposal.semantic_request,
        baseline.semantic_request,
    )
    semantic_request = _merge_role_queries(
        semantic_request,
        proposal.role_queries,
        baseline.semantic_request,
        allowed_roles=allowed_roles,
    )
    return PlanningIntent(
        required_roles=required_roles,
        optional_roles=optional_roles,
        precedence=proposal.precedence,
        minimum_stops=proposal.minimum_stops,
        maximum_stops=proposal.maximum_stops,
        pace=proposal.pace,
        coverage=baseline.coverage,
        slots=accepted_slots,
        evidence=_sanitize_evidence(proposal.evidence, constraints, baseline),
        semantic_request=semantic_request,
    )


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
    constraints: NormalizedConstraints,
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


def _sanitize_semantic_request(
    proposal: SemanticRequest,
    baseline: SemanticRequest,
) -> SemanticRequest:
    """Allow model semantics only when they cite deterministic user evidence."""

    if proposal.is_empty:
        return baseline
    known = {item.evidence_id: item for item in baseline.evidence}
    if not all(item.evidence_id in known for item in proposal.evidence):
        raise ValueError("semantic evidence is not grounded in normalized input")
    for objective in proposal.objectives:
        if not objective.evidence_refs or not set(objective.evidence_refs).issubset(known):
            raise ValueError("semantic objective has ungrounded evidence")
    for query in proposal.queries:
        if not query.evidence_refs or not set(query.evidence_refs).issubset(known):
            raise ValueError("semantic query has ungrounded evidence")
    # The model may reorder or select a subset of grounded queries, but it may
    # not invent a new evidence record or turn an absent preference into one.
    # If it preserves grounded evidence but omits all queries, retain the
    # deterministic baseline query for those same evidence ids.  Otherwise a
    # valid semantic proposal can silently disable Hybrid Retrieval (the
    # ``no_dense_query`` fallback) even though the semantic middle layer still
    # contains user preferences or scene tags.
    selected_evidence_ids = {item.evidence_id for item in proposal.evidence}
    proposal_query_keys = {
        (query.text.casefold(), query.target_role, query.evidence_refs)
        for query in proposal.queries
    }
    queries = list(proposal.queries)
    for baseline_query in baseline.queries:
        if not baseline_query.evidence_refs:
            continue
        if not set(baseline_query.evidence_refs).issubset(selected_evidence_ids):
            continue
        key = (
            baseline_query.text.casefold(),
            baseline_query.target_role,
            baseline_query.evidence_refs,
        )
        if key not in proposal_query_keys:
            queries.append(baseline_query)
    return SemanticRequest(
        evidence=tuple(known[item.evidence_id] for item in proposal.evidence),
        objectives=proposal.objectives,
        queries=tuple(queries),
    )


def _sanitize_evidence(
    proposal_evidence: dict[str, str],
    constraints: NormalizedConstraints,
    baseline: PlanningIntent,
) -> dict[str, str]:
    """Keep only evidence that can be traced to normalized input.

    The model may choose a structure, but it is not an evidence source. Start
    with the deterministic baseline evidence and retain a fixed-key record only
    when the proposed text is present in a normalized soft-preference field.
    Unknown keys and hallucinated values are dropped.
    """
    # baseline 中的 pace 是规则推导结果，不是用户证据。LLM 接受后它
    # 可能已经改变 pace，保留旧值会让 trace 自相矛盾；time_window 等
    # 确定性来源仍然保留。
    evidence = {
        key: value for key, value in baseline.evidence.items() if key != "pace"
    }
    grounded_values = {
        value.strip().casefold()
        for values in (
            _member_evidence(constraints),
            constraints.preferences,
            constraints.scene_tags,
            constraints.diet_tags,
            constraints.avoid,
        )
        for value in values
        if value and value.strip()
    }
    for value in proposal_evidence.values():
        normalized = value.strip().casefold()
        if normalized in grounded_values:
            evidence.setdefault("soft_preference", value.strip())
            break
    return evidence


def _safe_contract_code(error: Exception, fallback: str) -> str:
    """Keep contract diagnostics to a fixed code, never model/provider text."""

    value = str(error).strip()
    return value if re.fullmatch(r"[a-z0-9_]+", value) else fallback


def _skeleton_matches_proposal(
    skeleton: PlanSkeleton,
    proposal: PlanningIntentProposal,
) -> bool:
    roles = skeleton.roles
    if not proposal.minimum_stops <= len(roles) <= proposal.maximum_stops:
        return False
    role_set = set(roles)
    if not set(proposal.required_roles).issubset(role_set):
        return False
    if not role_set.issubset(set(proposal.required_roles) | set(proposal.optional_roles)):
        return False
    if proposal.slots:
        required_slot_roles = tuple(
            slot.role for slot in proposal.slots if slot.required
        )
        if not _contains_ordered_roles(roles, required_slot_roles):
            return False
    for before, after in proposal.precedence:
        if before in role_set and after in role_set and roles.index(before) >= roles.index(after):
            return False
    return True


def _contains_ordered_roles(
    actual: tuple[StopRole, ...],
    required: tuple[StopRole, ...],
) -> bool:
    """Return whether ``required`` occurs as an ordered subsequence.

    Repeated roles are intentionally significant (for example
    ``activity -> lunch -> activity -> dinner``).  Optional slots are omitted
    by the caller, so a closed skeleton may still leave an optional break out.
    """

    if not required:
        return True
    cursor = 0
    for role in actual:
        if role == required[cursor]:
            cursor += 1
            if cursor == len(required):
                return True
    return False


def _build_context(
    constraints: NormalizedConstraints,
    baseline: PlanningIntent,
) -> str:
    context = {
        "members": _member_evidence(constraints),
        "preferences": constraints.preferences,
        "diet_tags": constraints.diet_tags,
        "scene_tags": constraints.scene_tags,
        "avoid": constraints.avoid,
        "time_window": (
            constraints.time_window.value.model_dump(mode="json")
            if constraints.time_window is not None
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
        "baseline_intent": baseline.model_dump(mode="json"),
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
        "请根据用户需求提出一个 PlanStructureProposal v2。slots 是 1-4 个有序角色，"
        "每个 slot 的 inclusion 只能是 core 或 optional；允许未注册的新角色序列和重复 activity。"
        "不得删除或重排用户明确要求的角色；不得修改用户硬约束。role_queries 是按角色的检索表达，"
        "objectives 只能使用 allowed_objective_kinds 中的有限目标，每个 objective 必须至少引用一个 "
        "available_evidence_ids；不得创建 evidence 或输出 confidence。role_queries 的 evidence_refs "
        "也只能引用 available_evidence_ids。不要输出 POI、路线、价格、营业、天气、"
        "确切开始时间或可行性结论；core 只是模型建议，不等于用户硬约束。\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


_SYSTEM_PROMPT = """你是 HappyFreeTime 的受约束 PlanningIntent 节点。
只输出一个合法的 PlanStructureProposal v2 JSON 对象，不要返回 Markdown 代码围栏或解释文字。
slots 必须是 1 到 4 个有序角色；允许提出当前注册骨架中没有的新序列，也允许重复 activity。
每个 slot 必须标记 inclusion=core 或 optional。core 是结构建议，不是用户硬约束。
objectives 只能使用输入中的 allowed_objective_kinds；每个 objective 必须引用至少一个
available_evidence_ids，不能创建 evidence、confidence 或新的目标种类。
不得删除或重排用户明确指定的角色；午饭必须在晚饭前；最多一个 lunch、一个 dinner。
role_queries 必须绑定输入中的 evidence_id。不得输出 POI、resource_id、路线、距离、价格、营业、天气、
确切开始时间、Availability 或最终可行性。"""

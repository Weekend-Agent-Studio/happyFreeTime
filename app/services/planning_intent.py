"""受约束的 PlanningIntent 决策 seam。

模型只能提出有限的结构偏好；硬约束、事实查询、方案生成和可行性证明仍由
确定性代码负责。RuleBased provider 是默认基线，也是所有失败路径的回退。
"""

from __future__ import annotations

import json
import math
import os
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.domain.constraints import NormalizedConstraints, StopRole
from app.domain.semantics import EvidenceRef, SemanticQuery, SemanticRequest, SoftObjective
from app.domain.planning import (
    PlanPace,
    PlanSkeleton,
    PlanningIntent,
    PlanningIntentDecision,
    PlanningIntentProposal,
)


ACTIVITY_MEAL_SKELETON = PlanSkeleton(
    skeleton_id="activity-meal-v1",
    roles=(StopRole.ACTIVITY, StopRole.MEAL),
)
DINNER_ONLY_SKELETON = PlanSkeleton(
    skeleton_id="dinner-only-v1",
    roles=(StopRole.DINNER,),
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
    DINNER_ONLY_SKELETON,
    LUNCH_ACTIVITY_DINNER_SKELETON,
    ACTIVITY_BREAK_DINNER_SKELETON,
    ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON,
)

PROMPT_VERSION = "planning-intent.v1"
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
        try:
            raw_result = self._model.invoke(messages)
        except Exception as error:
            return self._fallback_decision(
                baseline, 1, _model_failure_reason(error)
            )
        try:
            proposal = _validate_proposal(raw_result)
        except Exception as first_error:
            # 结构化 Runnable 使用 include_raw=True 时，解析失败会以
            # {raw, parsed, parsing_error} 返回到这里，而不是在 invoke() 外抛出。
            # 因而生产模型和测试 fake 都真正共享一次格式修复路径。
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次 PlanningIntent 提议未通过结构校验。只返回合法的 "
                        "PlanningIntentProposal JSON，不要解释。校验错误："
                        f"{first_error}"
                    )
                ),
            ]
            try:
                raw_retry = self._model.invoke(retry_messages)
            except Exception as error:
                return self._fallback_decision(
                    baseline, 2, _model_failure_reason(error)
                )
            try:
                proposal = _validate_proposal(raw_retry)
            except Exception:
                return self._fallback_decision(baseline, 2, "invalid_proposal")
            attempts = 2
        else:
            attempts = 1

        if proposal.confidence < MIN_LLM_CONFIDENCE:
            return self._fallback_decision(baseline, attempts, "low_confidence")
        try:
            intent = _accept_proposal(proposal, constraints, baseline.intent)
        except ValueError:
            return self._fallback_decision(baseline, attempts, "proposal_out_of_bounds")
        return PlanningIntentDecision(
            intent=intent,
            source="llm",
            confidence=proposal.confidence,
            attempts=attempts,
            fallback_reason=None,
            prompt_version=self._prompt_version,
            model_name=self._model_name,
        )

    def _fallback_decision(
        self,
        baseline: PlanningIntentDecision,
        attempts: int,
        reason: str,
    ) -> PlanningIntentDecision:
        return PlanningIntentDecision(
            intent=baseline.intent,
            source="fallback",
            confidence=baseline.confidence,
            attempts=attempts,
            fallback_reason=reason,
            prompt_version=self._prompt_version,
            model_name=self._model_name,
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
        and constraints.required_stop_roles.value == (StopRole.DINNER,)
    ):
        return PlanningIntent(
            required_roles=(StopRole.DINNER,),
            optional_roles=(),
            minimum_stops=1,
            maximum_stops=1,
            pace=PlanPace.RELAXED,
            evidence={
                "exact_stop_count": constraints.exact_stop_count.raw_text or "1",
                "required_stop_roles": constraints.required_stop_roles.raw_text or StopRole.DINNER.value,
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
        extra_body={"thinking": {"type": "disabled"}},
    )
    return LlmPlanningIntentProvider(
        llm.with_structured_output(PlanningIntentProposal, include_raw=True),
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


def _model_failure_reason(error: Exception) -> str:
    """Normalize timeout classes from built-in, httpx, and OpenAI clients."""
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.casefold():
        return "timeout"
    return "model_error"


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
    return bool(constraints.preferences or constraints.scene_tags)


def _validate_proposal(result: object) -> PlanningIntentProposal:
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
    if isinstance(result, str):
        return PlanningIntentProposal.model_validate_json(result)
    return PlanningIntentProposal.model_validate(result)


def _accept_proposal(
    proposal: PlanningIntentProposal,
    constraints: NormalizedConstraints,
    baseline: PlanningIntent,
) -> PlanningIntent:
    # Baseline required roles encode the existing product structure; a soft
    # preference may not remove or invent a required role.
    if proposal.required_roles != baseline.required_roles:
        raise ValueError("required roles cannot be changed by a soft preference")
    allowed_roles = set(baseline.required_roles) | set(baseline.optional_roles)
    if not set(proposal.optional_roles).issubset(allowed_roles):
        raise ValueError("optional role is not allowed by the baseline intent")
    if set(proposal.required_roles) & set(proposal.optional_roles):
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
    if not any(
        _skeleton_matches_proposal(skeleton, proposal)
        for skeleton in ALL_PLAN_SKELETONS
    ):
        raise ValueError("proposal does not match an existing plan skeleton")
    return PlanningIntent(
        required_roles=proposal.required_roles,
        optional_roles=proposal.optional_roles,
        precedence=proposal.precedence,
        minimum_stops=proposal.minimum_stops,
        maximum_stops=proposal.maximum_stops,
        pace=proposal.pace,
        evidence=_sanitize_evidence(proposal.evidence, constraints, baseline),
        semantic_request=_sanitize_semantic_request(
            proposal.semantic_request,
            baseline.semantic_request,
        ),
    )


def _build_rule_semantic_request(
    constraints: NormalizedConstraints,
) -> SemanticRequest:
    """Translate known preferences to finite objectives while retaining text."""

    objective_aliases: dict[str, tuple[str, ...]] = {
        "low_fatigue": ("轻松", "松弛", "不赶", "休闲", "不累", "不希望太累", "慢慢走"),
        "shorter_travel": ("近一点", "更近", "近点", "少走", "步行可达"),
        "novelty": ("新鲜感", "新奇", "新意", "有意思"),
        "quiet": ("安静", "清静"),
        "conversation_friendly": ("聊天", "适合聊天", "能聊天"),
        "romantic": ("约会", "浪漫"),
        "family_friendly": ("亲子", "带孩子", "家庭"),
        "low_spice": ("少辣", "不辣", "微辣"),
    }
    values_by_field = (
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
            matched_kind = next(
                (
                    kind
                    for kind, aliases in objective_aliases.items()
                    if normalized in {alias.casefold() for alias in aliases}
                ),
                None,
            )
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
    return SemanticRequest(
        evidence=tuple(known[item.evidence_id] for item in proposal.evidence),
        objectives=proposal.objectives,
        queries=proposal.queries,
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
    for before, after in proposal.precedence:
        if before in role_set and after in role_set and roles.index(before) >= roles.index(after):
            return False
    return True


def _build_context(
    constraints: NormalizedConstraints,
    baseline: PlanningIntent,
) -> str:
    context = {
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
    }
    return (
        "请根据用户的软偏好提出 PlanningIntentProposal。只能调整节奏、可选角色、"
        "合法的站数范围和顺序偏好；不得修改硬约束。\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


_SYSTEM_PROMPT = """你是 HappyFreeTime 的受约束 PlanningIntent 节点。
只输出 PlanningIntentProposal 的结构化结果。你的输出不是最终方案，也不能包含 POI、路线、天气、价格、库存或硬约束值。
required_roles 必须保持 baseline_intent.required_roles；不要把模糊偏好升级成新的 required role。
只使用 baseline_intent 中允许的角色，站数必须在 1 到 4 内，且至少有一个现有骨架能够匹配。
evidence 只能引用输入中出现的软偏好词，不要编造用户没有说过的事实。
semantic_request 只能引用 baseline_intent.semantic_request 中已有的 evidence_id；
可以选择或重排已有 objective/query，但不得创建未被用户输入支持的证据。
"""

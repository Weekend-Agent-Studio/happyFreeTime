"""Grounded recommendation advice for already verified plans.

This seam is intentionally downstream of planning.  The Rule adapter renders a
deterministic explanation from plan facts and evidence.  The optional LLM
adapter may choose among the same plans and map user needs to evidence, but a
small harness validates all ids and rejects numeric or ungrounded prose before
it can reach the product response.
"""

from __future__ import annotations

import json
import math
import os
import re
from time import perf_counter
from typing import Protocol, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.domain.constraints import StopRole
from app.domain.recommendation import (
    NeedSummary,
    PlanAdvice,
    PlanRationaleProposal,
    RecommendationAdvice,
    RecommendationAdviceProposal,
    RecommendationAdviceRequest,
)
from app.domain.semantics import EvidenceRef, SoftObjectiveKind
from app.services.llm_compat import thinking_extra_body, structured_output_schema
from app.services.model_errors import model_failure_reason
from app.services.model_usage import ModelTokenUsage, TokenUsageAccumulator


PROMPT_VERSION = "recommendation-advisor.v2"
DEFAULT_MODEL_TIMEOUT_SECONDS = 15.0


class AdvisorProposalValidationError(ValueError):
    """Safe, stable diagnostics for untrusted structured model output."""

    def __init__(
        self,
        code: str,
        paths: Sequence[str] = (),
        error_types: Sequence[str] = (),
    ) -> None:
        self.code = code
        self.paths = tuple(paths)[:8]
        self.error_types = tuple(error_types)[:8]
        super().__init__(code)


class StructuredRecommendationModel(Protocol):
    """Minimal structured-model dependency; tests inject a deterministic fake."""

    def invoke(self, messages: list[object]) -> object:
        ...


class RecommendationAdvisor(Protocol):
    def advise(self, request: RecommendationAdviceRequest) -> RecommendationAdvice:
        ...


class RuleBasedRecommendationAdvisor:
    """Safe baseline that never calls a model or invents a planning fact."""

    adapter = "rule_based"
    prompt_version = "rule-based.v1"

    def advise(self, request: RecommendationAdviceRequest) -> RecommendationAdvice:
        started_at = perf_counter()
        needs = _build_needs(request)
        evidence_by_id = {
            item.evidence_id: item
            for item in (
                *request.semantic_request.evidence,
                *request.retrieval_evidence,
            )
        }
        diff_by_plan_id = {item.new_plan_id: item for item in request.plan_diffs}
        plan_advice = tuple(
            _build_plan_advice(
                plan,
                request,
                needs,
                evidence_by_id,
                diff=diff_by_plan_id.get(plan.plan_id),
            )
            for plan in request.verified_plans
        )
        # ``total_score`` is the deterministic Planner score: a larger value
        # is better.  Use ``min`` over the negative score so the tie-breakers
        # remain explicit (shorter duration, then stable id) rather than
        # accidentally selecting the lowest-scoring plan.
        recommended = min(
            request.verified_plans,
            key=lambda plan: (-plan.total_score, plan.total_duration_minutes, plan.plan_id),
        )
        recommended_advice = next(
            item for item in plan_advice if item.plan_id == recommended.plan_id
        )
        matched_text = _matched_need_text(recommended_advice, needs)
        overall_reason = _overall_reason(
            recommended,
            matched_text,
            request=request,
            diff=diff_by_plan_id.get(recommended.plan_id),
        )
        return RecommendationAdvice(
            recommended_plan_id=recommended.plan_id,
            understood_needs=needs,
            overall_reason=overall_reason,
            plans=plan_advice,
            adapter=self.adapter,
            fallback_reason=None,
            prompt_version=self.prompt_version,
            model_name=None,
            model_invoked=False,
            attempts=0,
            latency_ms=_elapsed_ms(started_at),
        )


class LlmRecommendationAdvisor:
    """Bounded model choice over a verified plan set with deterministic fallback."""

    def __init__(
        self,
        model: StructuredRecommendationModel,
        fallback: RecommendationAdvisor | None = None,
        *,
        prompt_version: str = PROMPT_VERSION,
        model_name: str | None = None,
    ) -> None:
        self._model = model
        self._fallback = fallback or RuleBasedRecommendationAdvisor()
        self._prompt_version = prompt_version
        self._model_name = model_name

    def advise(self, request: RecommendationAdviceRequest) -> RecommendationAdvice:
        baseline = self._fallback.advise(request)
        if not _should_call_model(request):
            return baseline

        started_at = perf_counter()
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_build_context(request, baseline)),
        ]
        token_usage = TokenUsageAccumulator()
        try:
            raw_result = self._model.invoke(messages)
            token_usage.record(raw_result)
        except Exception as error:
            token_usage.record_unknown()
            return _fallback_advice(
                baseline,
                attempts=1,
                reason=_model_failure_reason(error),
                started_at=started_at,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
                token_usage=token_usage.total,
            )
        try:
            proposal = _validate_proposal(raw_result)
        except Exception as parse_error:
            parse_code, parse_paths = _proposal_parse_diagnostics(parse_error)
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次 RecommendationAdviceProposal 未通过结构校验。"
                        "只返回合法 JSON，不要解释。"
                        f"诊断码={parse_code}；字段路径={','.join(parse_paths) or '$'}。"
                    )
                ),
            ]
            try:
                raw_retry = self._model.invoke(retry_messages)
                token_usage.record(raw_retry)
            except Exception as error:
                if token_usage.attempt_count < 2:
                    token_usage.record_unknown()
                return _fallback_advice(
                    baseline,
                    attempts=2,
                    reason=model_failure_reason(error),
                    started_at=started_at,
                    model_name=self._model_name,
                    prompt_version=self._prompt_version,
                    token_usage=token_usage.total,
                )
            try:
                proposal = _validate_proposal(raw_retry)
            except Exception as retry_error:
                if token_usage.attempt_count < 2:
                    token_usage.record_unknown()
                retry_code, _ = _proposal_parse_diagnostics(retry_error)
                return _fallback_advice(
                    baseline,
                    attempts=2,
                    reason=f"invalid_proposal_parse:{retry_code}",
                    started_at=started_at,
                    model_name=self._model_name,
                    prompt_version=self._prompt_version,
                    token_usage=token_usage.total,
                )
            attempts = 2
        else:
            attempts = 1

        try:
            return _accept_proposal(
                proposal,
                request,
                baseline,
                attempts=attempts,
                started_at=started_at,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
                token_usage=token_usage.total,
            )
        except ValueError as error:
            return _fallback_advice(
                baseline,
                attempts=attempts,
                reason=f"invalid_proposal_contract:{_safe_contract_code(error)}",
                started_at=started_at,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
                token_usage=token_usage.total,
            )


def build_default_recommendation_advisor(
    mode: str | None = None,
) -> RecommendationAdvisor:
    """Build the safe Rule adapter or explicitly configured LLM adapter."""

    selected_mode = (
        mode or os.getenv("HFT_RECOMMENDATION_ADVISOR_MODE", "rule")
    ).lower()
    if selected_mode == "rule":
        return RuleBasedRecommendationAdvisor()
    if selected_mode != "llm":
        raise RuntimeError("HFT_RECOMMENDATION_ADVISOR_MODE must be one of: rule, llm")
    api_key = os.getenv("LLM_API")
    if not api_key:
        raise RuntimeError("HFT_RECOMMENDATION_ADVISOR_MODE=llm requires LLM_API")
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        timeout=_model_timeout_seconds(),
        max_retries=0,
        # Function Calling 把推荐 Schema 传给兼容 OpenAI 的 provider；本地
        # Pydantic 继续负责严格校验。
        max_tokens=2048,
        extra_body=thinking_extra_body(model_name),
    )
    return LlmRecommendationAdvisor(
        llm.with_structured_output(
            structured_output_schema(model_name, RecommendationAdviceProposal),
            method="function_calling",
            include_raw=True,
        ),
        model_name=model_name,
    )


def _build_needs(request: RecommendationAdviceRequest) -> tuple[NeedSummary, ...]:
    needs: list[NeedSummary] = []
    for evidence in request.semantic_request.evidence:
        needs.append(
            NeedSummary(
                need_id=f"need.{evidence.evidence_id}",
                text=evidence.summary,
                user_evidence_ids=(evidence.evidence_id,),
            )
        )

    constraints = request.constraints
    if constraints.time_window is not None:
        window = constraints.time_window.value
        needs.append(
            NeedSummary(
                need_id="constraint.time_window",
                text=f"时间 {window.start}–{window.end}",
            )
        )
    if constraints.return_by is not None:
        needs.append(
            NeedSummary(
                need_id="constraint.return_by",
                text=f"最晚 {constraints.return_by.value} 前到家",
            )
        )
    if constraints.exact_stop_count is not None:
        needs.append(
            NeedSummary(
                need_id="constraint.exact_stop_count",
                text=f"只安排 {constraints.exact_stop_count.value} 站",
            )
        )
    if constraints.required_stop_roles is not None:
        labels = {
            StopRole.ACTIVITY: "活动",
            StopRole.MEAL: "餐饮",
            StopRole.LUNCH: "午餐",
            StopRole.DINNER: "晚餐",
            StopRole.BREAK: "休息",
        }
        role_text = "、".join(
            labels.get(role, role.value)
            for role in constraints.required_stop_roles.value
        )
        needs.append(
            NeedSummary(
                need_id="constraint.required_stop_roles",
                text=f"必选角色：{role_text}",
            )
        )
    if constraints.party is not None:
        party = constraints.party.value
        people = party.adults + party.children
        needs.append(
            NeedSummary(
                need_id="constraint.party",
                text=f"同行 {people} 人",
            )
        )
    if constraints.budget_per_person is not None:
        needs.append(
            NeedSummary(
                need_id="constraint.budget_per_person",
                text=f"人均预算 {constraints.budget_per_person.value} 元",
            )
        )
    return tuple(needs)


def _build_plan_advice(
    plan,
    request: RecommendationAdviceRequest,
    needs: Sequence[NeedSummary],
    evidence_by_id: dict[str, EvidenceRef],
    *,
    diff=None,
) -> PlanAdvice:
    semantic_needs = [
        need
        for need in needs
        if need.need_id.startswith("need.")
    ]
    matched_needs = [
        need
        for need in semantic_needs
        if _need_matches_plan(need, plan, request.retrieval_evidence)
    ]
    matched_needs.extend(
        need
        for need in needs
        if need.need_id.startswith("constraint.")
    )
    matched_ids = tuple(need.need_id for need in matched_needs)
    supporting: list[str] = [
        evidence_id
        for need in matched_needs
        for evidence_id in need.user_evidence_ids
    ]
    stop_ids = {stop.resource_id for stop in plan.stops}
    for evidence in request.retrieval_evidence:
        if not any(
            _evidence_belongs_to_resource(evidence, resource_id)
            for resource_id in stop_ids
        ):
            continue
        if any(
            _text_overlap(need.text, evidence.summary)
            for need in matched_needs
            if need.need_id.startswith("need.")
        ):
            supporting.append(evidence.evidence_id)
        if (
            request.weather_fact is not None
            and request.weather_fact.is_adverse
            and _is_weather_suitable_evidence(evidence.summary)
        ):
            supporting.append(evidence.evidence_id)
    supporting = list(dict.fromkeys(item for item in supporting if item in evidence_by_id))
    matched_text = _matched_need_text(
        PlanAdvice(
            plan_id=plan.plan_id,
            reason="pending",
            matched_need_ids=matched_ids,
            supporting_evidence_ids=tuple(supporting),
            tradeoffs=(),
        ),
        needs,
    )
    reason = (
        f"回应了你的{matched_text}需求；"
        if matched_text
        else "没有找到直接的偏好标签证据；"
    ) + "同时通过了当前时间、路线、预算和可行性校验。"
    weather_note = _weather_note(request, plan)
    if weather_note:
        reason += f" {weather_note}"
    if diff is not None:
        reason += f" {_diff_note(diff)}"
    return PlanAdvice(
        plan_id=plan.plan_id,
        reason=reason,
        matched_need_ids=matched_ids,
        supporting_evidence_ids=tuple(supporting),
        tradeoffs=tuple(plan.tradeoffs[:3]),
    )


def _need_matches_plan(
    need: NeedSummary,
    plan,
    retrieval_evidence: Sequence[EvidenceRef],
) -> bool:
    fact_text = " ".join(
        [
            plan.title,
            *plan.highlights,
            *plan.tradeoffs,
            *(item.message for item in plan.score_breakdown),
            *(
                evidence
                for contribution in plan.score_breakdown
                for evidence in contribution.evidence
            ),
            *(stop.name for stop in plan.stops),
            *(tag for stop in plan.stops for tag in stop.category_tags),
        ]
    )
    if _text_overlap(need.text, fact_text):
        return True
    stop_ids = {stop.resource_id for stop in plan.stops}
    return any(
        any(
            _evidence_belongs_to_resource(evidence, resource_id)
            for resource_id in stop_ids
        )
        and _text_overlap(need.text, evidence.summary)
        for evidence in retrieval_evidence
    )


def _evidence_belongs_to_resource(evidence: EvidenceRef, resource_id: str) -> bool:
    """Resolve both lexical and dense chunk citation formats to a POI.

    Lexical evidence uses the resource id as ``source_ref``.  Dense chunks
    use either ``profile:<resource_id>`` for a summary or a source document
    URI for a curated aspect, while all generated evidence ids retain the
    ``poi.<resource_id>.`` prefix.  Treating these as one read-only mapping
    keeps advisor grounding intact without adding resource identity fields to
    the public EvidenceRef contract.
    """

    return (
        evidence.source_ref in {resource_id, f"profile:{resource_id}"}
        or evidence.evidence_id.startswith(f"poi.{resource_id}.")
    )


def _matched_need_text(advice: PlanAdvice, needs: Sequence[NeedSummary]) -> str:
    texts = [
        need.text
        for need in needs
        if need.need_id in set(advice.matched_need_ids)
        and need.need_id.startswith("need.")
    ]
    return "、".join(dict.fromkeys(texts[:3]))


def _overall_reason(
    plan,
    matched_text: str,
    *,
    request: RecommendationAdviceRequest | None = None,
    diff=None,
) -> str:
    if matched_text:
        reason = (
            f"优先推荐“{plan.title}”：它在已验证候选中综合评分最高，"
            f"并更直接回应了你的{matched_text}需求。"
        )
    else:
        reason = (
            f"优先推荐“{plan.title}”：它在已验证候选中综合评分最高，"
            "并满足当前时间、路线和可行性要求。"
        )
    if request is not None:
        weather_note = _weather_note(request, plan)
        if weather_note:
            reason = f"{reason} {weather_note}"
    return f"{reason} {_diff_note(diff)}" if diff is not None else reason


_WEATHER_PROFILE_TERMS = (
    "室内",
    "阴雨",
    "雨天",
    "不依赖天气",
    "天气不好",
    "不受天气",
)


def _is_weather_suitable_evidence(summary: str) -> bool:
    text = summary.strip().casefold()
    return bool(text) and any(term.casefold() in text for term in _WEATHER_PROFILE_TERMS)


def _weather_evidence_for_plan(
    request: RecommendationAdviceRequest,
    plan,
) -> tuple[EvidenceRef, ...]:
    fact = request.weather_fact
    if fact is None or not fact.is_adverse:
        return ()
    stop_ids = {stop.resource_id for stop in plan.stops}
    return tuple(
        evidence
        for evidence in request.retrieval_evidence
        if any(
            _evidence_belongs_to_resource(evidence, resource_id)
            for resource_id in stop_ids
        )
        and _is_weather_suitable_evidence(evidence.summary)
    )


def _weather_note(
    request: RecommendationAdviceRequest,
    plan,
) -> str | None:
    fact = request.weather_fact
    if fact is None or not fact.is_adverse:
        return None
    evidence = _weather_evidence_for_plan(request, plan)
    if evidence:
        return f"天气实况为{fact.condition}，并引用了该活动的室内/雨天资料。"
    return f"天气实况为{fact.condition}，已排除天气敏感活动。"


def _diff_note(diff) -> str:
    """Describe only the deterministic facts in one candidate-level diff."""

    replacement = diff.replacements[0]
    parts = [
        f"本候选将“{replacement.before_name}”替换为“{replacement.after_name}”",
        (
            f"其他 {len(diff.locked_stops)} 站保持不变"
            if diff.locked_stops
            else "其他站点保持不变"
        ),
    ]
    if diff.route_distance_delta_km < 0:
        parts.append(f"全程缩短 {abs(diff.route_distance_delta_km):.1f} km")
    elif diff.route_distance_delta_km > 0:
        parts.append(f"全程增加 {diff.route_distance_delta_km:.1f} km")
    else:
        parts.append("全程距离不变")
    if diff.duration_delta_minutes < 0:
        parts.append(f"节省 {abs(diff.duration_delta_minutes)} 分钟")
    elif diff.duration_delta_minutes > 0:
        parts.append(f"增加 {diff.duration_delta_minutes} 分钟")
    if diff.price_delta < 0:
        parts.append(f"地点费用减少 ¥{abs(diff.price_delta)}")
    elif diff.price_delta > 0:
        parts.append(f"地点费用增加 ¥{diff.price_delta}")
    return "修改结果：" + "；".join(parts) + "。"


def _text_overlap(left: str, right: str) -> bool:
    left_text = left.strip().casefold()
    right_text = right.strip().casefold()
    if not left_text or not right_text:
        return False
    if left_text in right_text or right_text in left_text:
        return True
    left_terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", left_text))
    right_terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", right_text))
    return bool(left_terms & right_terms)


def _should_call_model(request: RecommendationAdviceRequest) -> bool:
    if not request.verified_plans:
        return False
    return bool(
        request.semantic_request.queries
        or any(
            objective.kind != SoftObjectiveKind.SHORTER_TRAVEL
            for objective in request.semantic_request.objectives
        )
    )


def _build_advice_facts(
    request: RecommendationAdviceRequest,
    baseline: RecommendationAdvice,
) -> dict[str, dict[str, str]]:
    """Compile qualitative, deterministic facts the model may cite.

    The model receives IDs and qualitative summaries only.  Numeric route,
    price, time and diff values stay in the verified domain objects and are
    rendered after acceptance by the Harness.
    """

    facts: dict[str, dict[str, str]] = {}
    plans = request.verified_plans
    distances = {
        plan.plan_id: sum(leg.distance_km for leg in plan.route_legs)
        for plan in plans
    }
    durations = {plan.plan_id: plan.total_duration_minutes for plan in plans}
    prices = {plan.plan_id: plan.total_price for plan in plans}
    min_distance = min(distances.values(), default=None)
    min_duration = min(durations.values(), default=None)
    min_price = min(prices.values(), default=None)
    diff_by_plan_id = {item.new_plan_id: item for item in request.plan_diffs}

    for plan in plans:
        plan_facts: dict[str, str] = {
            f"fact.{plan.plan_id}.verified": "已通过当前时间、路线、预算、营业与可行性校验。",
        }
        if min_distance is not None and distances[plan.plan_id] == min_distance:
            plan_facts[f"fact.{plan.plan_id}.shorter_route"] = "在当前候选中路线更紧凑。"
        if min_duration is not None and durations[plan.plan_id] == min_duration:
            plan_facts[f"fact.{plan.plan_id}.shorter_duration"] = "在当前候选中整体更省时。"
        if min_price is not None and prices[plan.plan_id] == min_price:
            plan_facts[f"fact.{plan.plan_id}.lower_cost"] = "在当前候选中地点费用更低。"
        if _weather_evidence_for_plan(request, plan):
            plan_facts[f"fact.{plan.plan_id}.weather_fit"] = "方案包含与当前不利天气相适配的资料证据。"
        diff = diff_by_plan_id.get(plan.plan_id)
        if diff is not None and diff.locked_stops:
            plan_facts[f"fact.{plan.plan_id}.locked_stops_preserved"] = "修改只替换目标站，其他已锁定站点保持不变。"
        facts[plan.plan_id] = plan_facts
    return facts


def _accept_proposal(
    proposal: RecommendationAdviceProposal,
    request: RecommendationAdviceRequest,
    baseline: RecommendationAdvice,
    *,
    attempts: int,
    started_at: float,
    model_name: str | None,
    prompt_version: str,
    token_usage: ModelTokenUsage,
) -> RecommendationAdvice:
    plan_ids = {plan.plan_id for plan in request.verified_plans}
    if proposal.recommended_plan_id not in plan_ids:
        raise ValueError("invalid_plan_id")
    rationale_ids = [item.plan_id for item in proposal.plans]
    if len(rationale_ids) != len(set(rationale_ids)):
        raise ValueError("duplicate_plan_rationale")
    if not set(rationale_ids).issubset(plan_ids):
        raise ValueError("invalid_plan_id")
    if proposal.recommended_plan_id not in set(rationale_ids):
        raise ValueError("missing_recommended_rationale")

    baseline_needs = {item.need_id: item for item in baseline.understood_needs}
    known_evidence = {
        item.evidence_id
        for item in (*request.semantic_request.evidence, *request.retrieval_evidence)
    }
    baseline_by_plan = {item.plan_id: item for item in baseline.plans}
    plan_by_id = {plan.plan_id: plan for plan in request.verified_plans}
    facts_by_plan = _build_advice_facts(request, baseline)
    diff_by_plan_id = {item.new_plan_id: item for item in request.plan_diffs}
    rationale_by_plan = {item.plan_id: item for item in proposal.plans}

    accepted: list[PlanAdvice] = []
    for plan in request.verified_plans:
        baseline_item = baseline_by_plan[plan.plan_id]
        rationale = rationale_by_plan.get(plan.plan_id)
        if rationale is None:
            accepted.append(baseline_item)
            continue
        if not set(rationale.matched_need_ids).issubset(baseline_needs):
            raise ValueError("invalid_need_id")
        if not set(rationale.supporting_evidence_ids).issubset(known_evidence):
            raise ValueError("invalid_evidence_id")
        if not set(rationale.matched_need_ids).issubset(
            set(baseline_item.matched_need_ids)
        ):
            raise ValueError("unsupported_plan_need")
        if not set(rationale.supporting_evidence_ids).issubset(
            set(baseline_item.supporting_evidence_ids)
        ):
            raise ValueError("unsupported_plan_evidence")
        matched_needs = {
            need.need_id: need
            for need in baseline.understood_needs
            if need.need_id in rationale.matched_need_ids
        }
        if any(
            need.user_evidence_ids
            and not set(need.user_evidence_ids).issubset(
                set(rationale.supporting_evidence_ids)
            )
            for need in matched_needs.values()
        ):
            raise ValueError("unsupported_need_evidence")
        allowed_facts = facts_by_plan[plan.plan_id]
        if not set(rationale.supporting_fact_ids).issubset(allowed_facts):
            raise ValueError("invalid_fact_id")
        prose_violation = _ungrounded_prose_code(
            rationale.qualitative_reason,
            request,
            baseline,
        )
        if prose_violation is not None:
            raise ValueError(prose_violation)
        reason_parts = [rationale.qualitative_reason]
        reason_parts.extend(allowed_facts[item] for item in rationale.supporting_fact_ids)
        weather_note = _weather_note(request, plan)
        if weather_note:
            reason_parts.append(weather_note)
        diff = diff_by_plan_id.get(plan.plan_id)
        if diff is not None:
            reason_parts.append(_diff_note(diff))
        accepted.append(
            PlanAdvice(
                plan_id=plan.plan_id,
                reason=" ".join(dict.fromkeys(reason_parts)),
                matched_need_ids=rationale.matched_need_ids,
                supporting_evidence_ids=rationale.supporting_evidence_ids,
                tradeoffs=baseline_item.tradeoffs,
            )
        )

    recommended = plan_by_id[proposal.recommended_plan_id]
    recommended_rationale = rationale_by_plan[proposal.recommended_plan_id]
    prose_violation = _ungrounded_prose_code(
        recommended_rationale.qualitative_reason,
        request,
        baseline,
    )
    if prose_violation is not None:
        raise ValueError(prose_violation)
    overall_parts = [
        f"优先推荐“{recommended.title}”：{recommended_rationale.qualitative_reason}"
    ]
    overall_parts.extend(
        facts_by_plan[recommended.plan_id][item]
        for item in recommended_rationale.supporting_fact_ids
    )
    weather_note = _weather_note(request, recommended)
    if weather_note:
        overall_parts.append(weather_note)
    diff = diff_by_plan_id.get(recommended.plan_id)
    if diff is not None:
        overall_parts.append(_diff_note(diff))
    return RecommendationAdvice(
        recommended_plan_id=proposal.recommended_plan_id,
        understood_needs=baseline.understood_needs,
        overall_reason=" ".join(dict.fromkeys(overall_parts)),
        plans=tuple(accepted),
        adapter="llm",
        fallback_reason=None,
        prompt_version=prompt_version,
        model_name=model_name,
        model_invoked=True,
        attempts=attempts,
        latency_ms=_elapsed_ms(started_at),
        input_tokens=token_usage.input_tokens,
        output_tokens=token_usage.output_tokens,
    )


def _contains_ungrounded_prose(
    text: str,
    request: RecommendationAdviceRequest,
    baseline: RecommendationAdvice,
) -> bool:
    """Reject claims that can be checked cheaply at the adapter boundary.

    The model is writing natural-language rationale, so a character-level
    whitelist would reject ordinary Chinese grammar and make the LLM adapter
    unusable.  Numeric facts are still forbidden, while quoted POI names are
    accepted only when they occur in the verified input/evidence.  All plan,
    need and evidence identifiers are validated separately by the caller.
    """

    return _ungrounded_prose_code(text, request, baseline) is not None


def _ungrounded_prose_code(
    text: str,
    request: RecommendationAdviceRequest,
    baseline: RecommendationAdvice,
) -> str | None:
    """Return a stable, non-sensitive grounding rejection code.

    The old boolean helper remains as a compatibility wrapper, while the
    evaluation harness needs to distinguish a numeric-fact violation from a
    made-up POI/claim.  No model text crosses the runtime boundary; only this
    fixed code is retained in ``fallback_reason``.
    """

    # Only reject numbers that look like dynamic facts.  Ordinals and generic
    # Chinese quantifiers (第一站、两种方案) are normal qualitative prose.
    dynamic_patterns = (
        r"(?:¥|￥)\s?\d+(?:\.\d+)?",
        r"\d+(?:\.\d+)?\s*(?:元|块|人民币)",
        r"[零一二三四五六七八九十百千万两]{1,6}\s*(?:元|块|人民币)",
        r"\d+(?:\.\d+)?\s*(?:公里|千米|km|KM)",
        r"[零一二三四五六七八九十百千万两]{1,6}\s*(?:公里|千米)",
        r"\d+(?:\.\d+)?\s*(?:分钟|小时)",
        r"[零一二三四五六七八九十百千万两]{1,6}\s*(?:分钟|小时)",
        r"\b\d{1,2}:\d{2}\b",
        r"(?:零|一|二|三|四|五|六|七|八|九|十){1,3}(?:点|时)(?:半|[零一二三四五六七八九十]{1,3}分)?",
        r"\d+(?:\.\d+)?\s*(?:%|分)",
        r"(?:价格|费用|花费|预算|距离|时长|路线)\s*[:：]?\s*(?:¥|￥)?\d+",
    )
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in dynamic_patterns):
        return "numeric_fact_violation"

    known_phrases = {
        item
        for item in (
            *(need.text for need in baseline.understood_needs),
            *(plan.title for plan in request.verified_plans),
            *(
                fact
                for plan in request.verified_plans
                for fact in (
                    *plan.highlights,
                    *plan.tradeoffs,
                    *(stop.name for stop in plan.stops),
                )
            ),
            *(item.summary for item in request.semantic_request.evidence),
            *(item.summary for item in request.retrieval_evidence),
        )
        if item
    }
    quoted_phrases = re.findall(r"[“\"]([^”\"]+)[”\"]", text)
    if any(
        phrase.strip() not in known_phrases
        for phrase in quoted_phrases
        if phrase.strip()
    ):
        return "unsupported_claim"

    # Do not reject ordinary unquoted Chinese prose.  Quoted names are the
    # unambiguous hallucination boundary and are checked against verified
    # plan/evidence vocabulary above.
    return None


def _validate_proposal(result: object) -> RecommendationAdviceProposal:
    if isinstance(result, dict) and (
        "parsed" in result or "parsing_error" in result
    ):
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            raise AdvisorProposalValidationError("advisor_nested_parse_error", ("$",))
        parsed = result.get("parsed")
        if parsed is None:
            raise AdvisorProposalValidationError("advisor_missing_field", ("$",))
        return _validate_proposal(parsed)
    if isinstance(result, RecommendationAdviceProposal):
        return result
    try:
        if isinstance(result, str):
            return RecommendationAdviceProposal.model_validate_json(result)
        return RecommendationAdviceProposal.model_validate(result)
    except Exception as error:
        code, paths = _proposal_parse_diagnostics(error)
        raise AdvisorProposalValidationError(code, paths) from None


def _proposal_parse_diagnostics(error: Exception) -> tuple[str, tuple[str, ...]]:
    """Map provider/Pydantic parse failures to safe stable diagnostics."""

    if isinstance(error, AdvisorProposalValidationError):
        return error.code, error.paths
    errors = getattr(error, "errors", None)
    if callable(errors):
        raw_errors = errors()
        paths: list[str] = []
        types: list[str] = []
        for item in raw_errors[:8]:
            loc = item.get("loc") or ("$",)
            paths.append(".".join(str(part) for part in loc))
            types.append(str(item.get("type") or ""))
        error_type = types[0] if types else ""
        if error_type == "missing":
            code = "advisor_missing_field"
        elif error_type == "extra_forbidden":
            code = "advisor_extra_forbidden"
        elif error_type in {"too_short", "too_long"} and any(
            path.endswith("plans") for path in paths
        ):
            code = "advisor_invalid_plan_count"
        elif error_type.endswith("_type") or error_type in {"string_type", "int_type"}:
            code = "advisor_invalid_type"
        else:
            code = "advisor_nested_parse_error"
        return code, tuple(paths)
    return "advisor_nested_parse_error", ("$",)


def _fallback_advice(
    baseline: RecommendationAdvice,
    *,
    attempts: int,
    reason: str,
    started_at: float,
    model_name: str | None,
    prompt_version: str,
    token_usage: ModelTokenUsage,
) -> RecommendationAdvice:
    return baseline.model_copy(
        update={
            "adapter": "fallback",
            "fallback_reason": reason,
            "prompt_version": prompt_version,
            "model_name": model_name,
            "model_invoked": True,
            "attempts": attempts,
            "latency_ms": _elapsed_ms(started_at),
            "input_tokens": token_usage.input_tokens,
            "output_tokens": token_usage.output_tokens,
        }
    )


_model_failure_reason = model_failure_reason


def _safe_contract_code(error: Exception) -> str:
    """Keep contract diagnostics to a fixed code, never model/provider text."""

    value = str(error).strip()
    return value if re.fullmatch(r"[a-z0-9_]+", value) else "validation_failed"


def _model_timeout_seconds() -> float:
    raw_value = os.getenv(
        "HFT_RECOMMENDATION_ADVISOR_TIMEOUT_SECONDS",
        str(DEFAULT_MODEL_TIMEOUT_SECONDS),
    )
    try:
        timeout = float(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "HFT_RECOMMENDATION_ADVISOR_TIMEOUT_SECONDS must be a positive number"
        ) from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError(
            "HFT_RECOMMENDATION_ADVISOR_TIMEOUT_SECONDS must be a positive number"
        )
    return timeout


def _build_context(
    request: RecommendationAdviceRequest,
    baseline: RecommendationAdvice,
) -> str:
    evidence_by_id = {
        item.evidence_id: item
        for item in (
            *request.semantic_request.evidence,
            *request.retrieval_evidence,
        )
    }
    baseline_by_plan = {item.plan_id: item for item in baseline.plans}
    facts_by_plan = _build_advice_facts(request, baseline)
    plans = []
    for plan in request.verified_plans:
        baseline_item = baseline_by_plan[plan.plan_id]
        allowed_evidence_ids = tuple(baseline_item.supporting_evidence_ids)
        plans.append(
            {
                "plan_id": plan.plan_id,
                "title": _redact_dynamic_text(plan.title),
                "strategy": plan.strategy.value,
                "stops": [
                    {
                        "name": _redact_dynamic_text(stop.name),
                        "role": stop.role.value if stop.role else None,
                        "category_tags": list(stop.category_tags),
                    }
                    for stop in plan.stops
                ],
                "highlights": [_redact_dynamic_text(item) for item in plan.highlights],
                "tradeoffs": [_redact_dynamic_text(item) for item in plan.tradeoffs],
                "allowed_matched_need_ids": list(baseline_item.matched_need_ids),
                "allowed_supporting_evidence_ids": list(allowed_evidence_ids),
                "allowed_supporting_evidence": [
                    {
                        "evidence_id": evidence_by_id[evidence_id].evidence_id,
                        "summary": _redact_dynamic_text(evidence_by_id[evidence_id].summary),
                        "source_type": evidence_by_id[evidence_id].source_type,
                    }
                    for evidence_id in allowed_evidence_ids
                    if evidence_id in evidence_by_id
                ],
                "allowed_supporting_fact_ids": sorted(facts_by_plan[plan.plan_id]),
                "allowed_supporting_facts": [
                    {"fact_id": fact_id, "summary": facts_by_plan[plan.plan_id][fact_id]}
                    for fact_id in sorted(facts_by_plan[plan.plan_id])
                ],
            }
        )
    semantic_request = {
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "summary": _redact_dynamic_text(item.summary),
                "source_type": item.source_type,
            }
            for item in request.semantic_request.evidence
        ],
        "objectives": [
            {
                "kind": item.kind.value,
                "strength": item.strength,
                "target_role": item.target_role.value if item.target_role else None,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in request.semantic_request.objectives
        ],
        "queries": [
            {
                "query_id": item.query_id,
                "text": _redact_dynamic_text(item.text),
                "target_role": item.target_role.value if item.target_role else None,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in request.semantic_request.queries
        ],
    }
    diff_facts = {
        diff.new_plan_id: sorted(facts_by_plan.get(diff.new_plan_id, {}))
        for diff in request.plan_diffs
    }
    context = {
        "needs": [
            {
                "need_id": item.need_id,
                "summary": _redact_dynamic_text(item.text),
                "user_evidence_ids": list(item.user_evidence_ids),
            }
            for item in baseline.understood_needs
        ],
        "semantic_request": semantic_request,
        "retrieval_evidence": [
            {
                "evidence_id": item.evidence_id,
                "summary": _redact_dynamic_text(item.summary),
                "source_type": item.source_type,
            }
            for item in request.retrieval_evidence
        ],
        "plan_diffs": diff_facts,
        "weather_fact": (
            {
                "adverse": request.weather_fact.is_adverse,
                "condition": request.weather_fact.condition,
            }
            if request.weather_fact is not None
            else None
        ),
        "verified_plans": plans,
    }
    return (
        "只在已验证方案之间选择和解释，不要重新规划。模型只提交方案级定性理由、需求/证据/事实 ID；精确数字由系统渲染。输入如下：\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def _redact_dynamic_text(text: str) -> str:
    """Remove exact dynamic quantities before sending context to the model."""

    redactions = (
        (r"(?:¥|￥)\s?\d+(?:\.\d+)?", "费用"),
        (r"\d+(?:\.\d+)?\s*(?:元|块|人民币)", "费用"),
        (r"[零一二三四五六七八九十百千万两]{1,6}\s*(?:元|块|人民币)", "费用"),
        (r"\d+(?:\.\d+)?\s*(?:公里|千米|km|KM)", "距离"),
        (r"[零一二三四五六七八九十百千万两]{1,6}\s*(?:公里|千米)", "距离"),
        (r"\d+(?:\.\d+)?\s*(?:分钟|小时)", "时长"),
        (r"[零一二三四五六七八九十百千万两]{1,6}\s*(?:分钟|小时)", "时长"),
        (r"\b\d{1,2}:\d{2}\b", "具体时间"),
        (r"(?:上午|下午|晚上|早上|中午)?[零一二三四五六七八九十百千万两]{1,4}(?:点|时)(?:半|[零一二三四五六七八九十百千万两]{1,4}分)?", "具体时间"),
        (r"\d{4}[-年]\d{1,2}[-月]\d{1,2}(?:日)?", "具体日期"),
        (r"\d+(?:\.\d+)?\s*(?:%|分)", "评分"),
    )
    redacted = text
    for pattern, replacement in redactions:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


_SYSTEM_PROMPT = """你是 HappyFreeTime 的 RecommendationAdvisor。
你只能在输入的 verified_plans 中选择推荐方案，并为需要说明的方案提供定性理由。
只返回 RecommendationAdviceProposal 的结构化 JSON：
recommended_plan_id + plans[{plan_id, matched_need_ids, supporting_evidence_ids,
supporting_fact_ids, qualitative_reason}]。

严格限制：
- 所有 plan_id、need_id、supporting_evidence_ids、supporting_fact_ids 必须来自
  对应方案的 allowlist；不要跨方案引用证据或事实。
- 只需要解释推荐方案，也可以额外解释少量其他方案；遗漏的方案由系统使用规则基线补齐。
- qualitative_reason 只能是定性说明，例如“更适合安静聊天”“安排更从容”。
- 不要输出价格、距离、时长、时间、评分、百分比或其他动态数字；不要输出新的 POI、路线、营业或天气事实。
- “第一站”“两种方案”等普通序号/数量表达可以使用，但不要把它们写成路线或价格事实。
- 天气、PlanDiff、锁定站点和精确变化由系统根据已验证事实自动补充，模型只引用允许的 fact_id。
- 不要输出 understood_needs、overall_reason 或任何额外字段。
"""

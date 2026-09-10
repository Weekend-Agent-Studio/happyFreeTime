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
    RecommendationAdvice,
    RecommendationAdviceProposal,
    RecommendationAdviceRequest,
)
from app.domain.semantics import EvidenceRef, SoftObjectiveKind


PROMPT_VERSION = "recommendation-advisor.v1"
DEFAULT_MODEL_TIMEOUT_SECONDS = 15.0


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
        try:
            raw_result = self._model.invoke(messages)
        except Exception as error:
            return _fallback_advice(
                baseline,
                attempts=1,
                reason=_model_failure_reason(error),
                started_at=started_at,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
            )
        try:
            proposal = _validate_proposal(raw_result)
        except Exception as first_error:
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次推荐解释未通过结构或证据校验。只返回合法的 "
                        "RecommendationAdviceProposal JSON，不要解释。校验错误："
                        f"{first_error}"
                    )
                ),
            ]
            try:
                raw_retry = self._model.invoke(retry_messages)
                proposal = _validate_proposal(raw_retry)
            except Exception:
                return _fallback_advice(
                    baseline,
                    attempts=2,
                    reason="invalid_proposal",
                    started_at=started_at,
                    model_name=self._model_name,
                    prompt_version=self._prompt_version,
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
            )
        except ValueError as error:
            return _fallback_advice(
                baseline,
                attempts=attempts,
                reason=str(error),
                started_at=started_at,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
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
        extra_body={"thinking": {"type": "disabled"}},
    )
    return LlmRecommendationAdvisor(
        llm.with_structured_output(
            RecommendationAdviceProposal,
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


def _overall_reason(plan, matched_text: str, *, diff=None) -> str:
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
    return f"{reason} {_diff_note(diff)}" if diff is not None else reason


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


def _accept_proposal(
    proposal: RecommendationAdviceProposal,
    request: RecommendationAdviceRequest,
    baseline: RecommendationAdvice,
    *,
    attempts: int,
    started_at: float,
    model_name: str | None,
    prompt_version: str,
) -> RecommendationAdvice:
    plan_ids = {plan.plan_id for plan in request.verified_plans}
    if proposal.recommended_plan_id not in plan_ids:
        raise ValueError("invalid_plan_id")
    proposal_plan_ids = [item.plan_id for item in proposal.plans]
    if set(proposal_plan_ids) != plan_ids or len(proposal_plan_ids) != len(plan_ids):
        raise ValueError("incomplete_plan_advice")
    baseline_needs = {item.need_id: item for item in baseline.understood_needs}
    known_evidence = {
        item.evidence_id
        for item in (
            *request.semantic_request.evidence,
            *request.retrieval_evidence,
        )
    }
    for need in proposal.understood_needs:
        expected = baseline_needs.get(need.need_id)
        if expected is None or need.text != expected.text:
            raise ValueError("ungrounded_need")
        if need.user_evidence_ids != expected.user_evidence_ids:
            raise ValueError("ungrounded_need_evidence")
    known_need_ids = set(baseline_needs)
    baseline_by_plan = {item.plan_id: item for item in baseline.plans}
    diff_by_plan_id = {item.new_plan_id: item for item in request.plan_diffs}
    accepted: list[PlanAdvice] = []
    for item in proposal.plans:
        if not set(item.matched_need_ids).issubset(known_need_ids):
            raise ValueError("invalid_need_id")
        if not set(item.supporting_evidence_ids).issubset(known_evidence):
            raise ValueError("invalid_evidence_id")
        baseline_item = baseline_by_plan[item.plan_id]
        if not set(item.matched_need_ids).issubset(
            set(baseline_item.matched_need_ids)
        ):
            raise ValueError("unsupported_plan_need")
        if not set(item.supporting_evidence_ids).issubset(
            set(baseline_item.supporting_evidence_ids)
        ):
            raise ValueError("unsupported_plan_evidence")
        matched_needs = {
            need.need_id: need
            for need in baseline.understood_needs
            if need.need_id in item.matched_need_ids
        }
        if any(
            need.user_evidence_ids
            and not set(need.user_evidence_ids).issubset(
                set(item.supporting_evidence_ids)
            )
            for need in matched_needs.values()
        ):
            raise ValueError("unsupported_need_evidence")
        if _contains_ungrounded_prose(item.reason, request, baseline):
            raise ValueError("ungrounded_text")
        # Tradeoffs are facts owned by the verified Plan.  The model cannot
        # replace them with a new numeric claim or a new POI description.
        diff = diff_by_plan_id.get(item.plan_id)
        if diff is not None:
            # Keep the model's qualitative wording, but append the verified
            # change facts so an LLM cannot accidentally omit what changed.
            item_reason = f"{item.reason} {_diff_note(diff)}"
        else:
            item_reason = item.reason
        accepted.append(
            item.model_copy(
                update={
                    "reason": item_reason,
                    "tradeoffs": baseline_by_plan[item.plan_id].tradeoffs,
                }
            )
        )
    if _contains_ungrounded_prose(proposal.overall_reason, request, baseline):
        raise ValueError("ungrounded_text")
    overall_reason = proposal.overall_reason
    diff = diff_by_plan_id.get(proposal.recommended_plan_id)
    if diff is not None:
        overall_reason = f"{overall_reason} {_diff_note(diff)}"
    # The model is allowed to select a subset of needs for emphasis, but the
    # complete deterministic need list remains the persisted user-understanding
    # record and cannot be replaced by a hallucinated summary.
    return RecommendationAdvice(
        recommended_plan_id=proposal.recommended_plan_id,
        understood_needs=baseline.understood_needs,
        overall_reason=overall_reason,
        plans=tuple(accepted),
        adapter="llm",
        fallback_reason=None,
        prompt_version=prompt_version,
        model_name=model_name,
        model_invoked=True,
        attempts=attempts,
        latency_ms=_elapsed_ms(started_at),
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

    if re.search(r"\d|[零一二三四五六七八九十百千万]|[¥￥%]", text):
        return True
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
        return True

    # Catch the common unquoted hallucination shape (e.g. ``虚构景点``)
    # without trying to segment arbitrary Chinese prose.  Generic references
    # such as ``这个景点`` are harmless; named suffixes must occur in the
    # verified plan/evidence vocabulary.
    generic_poi_phrases = {"景点", "这个景点", "该景点", "地点", "餐厅", "这家餐厅"}
    poi_suffixes = (
        "景点", "公园", "博物馆", "餐厅", "餐馆", "咖啡馆", "咖啡店", "书店",
        "展馆", "美术馆", "商场", "广场", "乐园", "湖", "园", "馆", "店",
    )
    for match in re.finditer(
        rf"[\u4e00-\u9fff]{{2,16}}(?:{'|'.join(poi_suffixes)})",
        text,
    ):
        phrase = match.group(0)
        if phrase in generic_poi_phrases:
            continue
        if not any(phrase in known or known in phrase for known in known_phrases):
            return True
    return False


def _validate_proposal(result: object) -> RecommendationAdviceProposal:
    if isinstance(result, dict) and (
        "parsed" in result or "parsing_error" in result
    ):
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            raise ValueError(f"structured proposal parsing failed: {parsing_error}")
        parsed = result.get("parsed")
        if parsed is None:
            raise ValueError("structured proposal parser returned no proposal")
        return _validate_proposal(parsed)
    if isinstance(result, RecommendationAdviceProposal):
        return result
    if isinstance(result, str):
        return RecommendationAdviceProposal.model_validate_json(result)
    return RecommendationAdviceProposal.model_validate(result)


def _fallback_advice(
    baseline: RecommendationAdvice,
    *,
    attempts: int,
    reason: str,
    started_at: float,
    model_name: str | None,
    prompt_version: str,
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
        }
    )


def _model_failure_reason(error: Exception) -> str:
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.casefold():
        return "timeout"
    return "model_error"


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
    plans = []
    for plan in request.verified_plans:
        plans.append(
            {
                "plan_id": plan.plan_id,
                "title": plan.title,
                "strategy": plan.strategy.value,
                "total_score": plan.total_score,
                "total_price": plan.total_price,
                "total_duration_minutes": plan.total_duration_minutes,
                "stops": [
                    {
                        "resource_id": stop.resource_id,
                        "name": stop.name,
                        "role": stop.role.value if stop.role else None,
                        "start": stop.start,
                        "end": stop.end,
                        "price": stop.price,
                    }
                    for stop in plan.stops
                ],
                "route_legs": [
                    {
                        "origin_name": leg.origin_name,
                        "destination_name": leg.destination_name,
                        "distance_km": leg.distance_km,
                        "duration_minutes": leg.duration_minutes,
                        "degraded": leg.degraded,
                    }
                    for leg in plan.route_legs
                ],
                "highlights": plan.highlights,
                "tradeoffs": plan.tradeoffs,
            }
        )
    context = {
        "needs": [item.model_dump(mode="json") for item in baseline.understood_needs],
        "semantic_request": request.semantic_request.model_dump(mode="json"),
        "retrieval_evidence": [
            item.model_dump(mode="json") for item in request.retrieval_evidence
        ],
        "plan_diffs": [item.model_dump(mode="json") for item in request.plan_diffs],
        "verified_plans": plans,
    }
    return (
        "只在已验证方案之间选择和解释，不要重新规划。输入如下：\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


_SYSTEM_PROMPT = """你是 HappyFreeTime 的 RecommendationAdvisor。
你只能在输入的 verified_plans 中选择推荐方案，并说明它如何回应输入的用户需求。
只返回 RecommendationAdviceProposal 的结构化 JSON。

严格限制：
- recommended_plan_id、PlanAdvice.plan_id 必须使用输入中已有的 plan_id。
- matched_need_ids 必须使用输入中已有的 need_id；supporting_evidence_ids 必须使用输入中已有的证据 id。
- understood_needs 必须原样复制输入 needs，不要增加或改写用户没有说过的需求。
- 不要新增 POI、路线、价格、距离、时间、营业或可用性事实；不要输出任何数字。
- reason 和 overall_reason 只写需求回应和取舍，不要编造新地点名称；具体事实由已验证 Plan 和界面渲染。
"""

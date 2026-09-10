import unittest

from app.domain.planning import (
    Plan,
    PlanDiff,
    PlanPriceStatus,
    PlanStrategy,
    StopReplacement,
)
from app.domain.recommendation import (
    RecommendationAdviceProposal,
    RecommendationAdviceRequest,
)
from app.domain.semantics import EvidenceRef, SemanticQuery, SemanticRequest
from app.services.recommendation_advisor import (
    LlmRecommendationAdvisor,
    RuleBasedRecommendationAdvisor,
)
from tests.test_planning import planning_constraints


class SequenceModel:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> object:
        self.calls.append(messages)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_plan(
    plan_id: str,
    *,
    score: float,
    title: str | None = None,
    highlights: list[str] | None = None,
    tradeoffs: list[str] | None = None,
) -> Plan:
    return Plan(
        plan_id=plan_id,
        composition_fingerprint=plan_id,
        skeleton_id="activity-meal-v1",
        title=title or plan_id,
        strategy=PlanStrategy.BALANCED,
        total_score=score,
        total_price=120,
        price_status=PlanPriceStatus.KNOWN,
        total_duration_minutes=180,
        stops=[],
        route_legs=[],
        score_breakdown=[],
        highlights=highlights or [],
        tradeoffs=tradeoffs or [],
    )


def semantic_request() -> SemanticRequest:
    evidence = EvidenceRef(
        evidence_id="user.preferences.1",
        source_type="user_message",
        source_field="preferences",
        summary="轻松约会",
        confidence=1.0,
    )
    return SemanticRequest(
        evidence=(evidence,),
        queries=(
            SemanticQuery(
                query_id="query.1",
                text="轻松约会",
                evidence_refs=(evidence.evidence_id,),
            ),
        ),
    )


def make_request(*, plans: tuple[Plan, ...] | None = None, diffs=()) -> RecommendationAdviceRequest:
    return RecommendationAdviceRequest(
        constraints=planning_constraints(),
        semantic_request=semantic_request(),
        verified_plans=plans
        or (
            make_plan(
                "plan-one",
                score=8,
                title="轻松方案",
                highlights=["轻松约会"],
                tradeoffs=["路线略长"],
            ),
            make_plan(
                "plan-two",
                score=12,
                title="高分方案",
                highlights=["轻松约会"],
                tradeoffs=["可选活动较少"],
            ),
        ),
        plan_diffs=tuple(diffs),
    )


def valid_proposal(request: RecommendationAdviceRequest) -> RecommendationAdviceProposal:
    baseline = RuleBasedRecommendationAdvisor().advise(request)
    return RecommendationAdviceProposal(
        recommended_plan_id=baseline.recommended_plan_id,
        understood_needs=baseline.understood_needs,
        overall_reason="优先回应你的轻松约会需求，并通过可行性校验。",
        plans=tuple(
            item.model_copy(update={"reason": "回应了你的轻松约会需求，并通过校验。"})
            for item in baseline.plans
        ),
    )


class RecommendationAdvisorTest(unittest.TestCase):
    def test_rule_adapter_recommends_highest_scoring_verified_plan(self) -> None:
        advice = RuleBasedRecommendationAdvisor().advise(make_request())

        self.assertEqual(advice.recommended_plan_id, "plan-two")
        self.assertFalse(advice.model_invoked)
        self.assertEqual(
            advice.plans[0].supporting_evidence_ids,
            ("user.preferences.1",),
        )

    def test_llm_accepts_grounded_structured_proposal(self) -> None:
        request = make_request()
        model = SequenceModel(valid_proposal(request))

        advice = LlmRecommendationAdvisor(model, model_name="fake-model").advise(request)

        self.assertEqual(advice.adapter, "llm")
        self.assertTrue(advice.model_invoked)
        self.assertEqual(advice.attempts, 1)
        self.assertEqual(len(model.calls), 1)

    def test_invalid_plan_or_evidence_falls_back_without_leaking_model_text(self) -> None:
        request = make_request()
        baseline = RuleBasedRecommendationAdvisor().advise(request)
        invalid = valid_proposal(request).model_copy(
            update={
                "recommended_plan_id": "not-verified",
                "plans": tuple(
                    item.model_copy(
                        update={"supporting_evidence_ids": ("not-known",)}
                    )
                    for item in baseline.plans
                ),
            }
        )
        model = SequenceModel(invalid, invalid)

        advice = LlmRecommendationAdvisor(model).advise(request)

        self.assertEqual(advice.adapter, "fallback")
        self.assertEqual(advice.fallback_reason, "invalid_plan_id")
        self.assertEqual(advice.overall_reason, baseline.overall_reason)
        self.assertEqual(advice.attempts, 1)

    def test_numeric_or_quoted_unknown_claim_falls_back(self) -> None:
        request = make_request()
        proposal = valid_proposal(request).model_copy(
            update={
                "overall_reason": "优先推荐“虚构景点”，节省 3 公里。",
            }
        )
        model = SequenceModel(proposal)

        advice = LlmRecommendationAdvisor(model).advise(request)

        self.assertEqual(advice.adapter, "fallback")
        self.assertEqual(advice.fallback_reason, "ungrounded_text")

    def test_format_repair_is_bounded_to_one_retry(self) -> None:
        request = make_request()
        model = SequenceModel({"bad": True}, valid_proposal(request))

        advice = LlmRecommendationAdvisor(model).advise(request)

        self.assertEqual(advice.adapter, "llm")
        self.assertEqual(advice.attempts, 2)
        self.assertEqual(len(model.calls), 2)

    def test_timeout_falls_back_and_no_semantic_request_skips_model(self) -> None:
        request = make_request()
        timeout_model = SequenceModel(TimeoutError("request timeout"))
        timeout_advice = LlmRecommendationAdvisor(timeout_model).advise(request)
        self.assertEqual(timeout_advice.adapter, "fallback")
        self.assertEqual(timeout_advice.fallback_reason, "timeout")

        error_model = SequenceModel(RuntimeError("provider unavailable"))
        error_advice = LlmRecommendationAdvisor(error_model).advise(request)
        self.assertEqual(error_advice.adapter, "fallback")
        self.assertEqual(error_advice.fallback_reason, "model_error")

        plain_request = RecommendationAdviceRequest(
            constraints=planning_constraints(),
            verified_plans=request.verified_plans,
        )
        skipped_model = SequenceModel(valid_proposal(request))
        skipped_advice = LlmRecommendationAdvisor(skipped_model).advise(plain_request)
        self.assertEqual(skipped_advice.adapter, "rule_based")
        self.assertEqual(skipped_model.calls, [])

    def test_modification_advice_includes_verified_diff_facts(self) -> None:
        request = make_request(
            plans=(make_plan("replacement", score=10, title="替换后方案"),),
            diffs=(
                PlanDiff(
                    base_plan_id="base",
                    new_plan_id="replacement",
                    locked_stops=(),
                    replacements=(
                        StopReplacement(
                            stop_index=0,
                            before_resource_id="old",
                            before_name="旧活动",
                            after_resource_id="new",
                            after_name="新活动",
                        ),
                    ),
                    route_distance_delta_km=-1.2,
                    duration_delta_minutes=-10,
                    price_delta=0,
                ),
            ),
        )

        advice = RuleBasedRecommendationAdvisor().advise(request)

        self.assertIn("旧活动", advice.plans[0].reason)
        self.assertIn("新活动", advice.plans[0].reason)
        self.assertIn("缩短 1.2 km", advice.plans[0].reason)

    def test_request_rejects_duplicate_plan_or_diff_identity(self) -> None:
        plan = make_plan("duplicate", score=1)
        with self.assertRaises(ValueError):
            RecommendationAdviceRequest(
                constraints=planning_constraints(),
                verified_plans=(plan, plan),
            )

        diff = PlanDiff(
            base_plan_id="base",
            new_plan_id="duplicate",
            replacements=(
                StopReplacement(
                    stop_index=0,
                    before_resource_id="old",
                    before_name="旧",
                    after_resource_id="new",
                    after_name="新",
                ),
            ),
            route_distance_delta_km=0,
            duration_delta_minutes=0,
            price_delta=0,
        )
        with self.assertRaises(ValueError):
            RecommendationAdviceRequest(
                constraints=planning_constraints(),
                verified_plans=(plan,),
                plan_diffs=(diff, diff),
            )

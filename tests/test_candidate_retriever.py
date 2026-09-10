import unittest

from app.domain.catalog import ResourceType
from app.domain.constraints import StopRole
from app.domain.semantics import EvidenceRef, SemanticQuery, SemanticRequest
from app.services.candidate_retriever import (
    HybridRagCandidateRetriever,
    RuleBasedCandidateRetriever,
)
from tests.test_native_planning import candidate
from tests.test_planning import FixedReplayRouteProvider, planning_constraints
from app.domain.planning import PlanSkeleton, PlanningIntent
from app.services.catalog import InMemoryCatalog
from app.services.planning import PlanningService
from app.services.planning import _rank_skeleton_plans


class CandidateRetrieverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = [
            candidate("plain", ResourceType.ACTIVITY, "普通展厅", ["展览"]),
            candidate("quiet", ResourceType.ACTIVITY, "安静书店", ["书店", "安静"]),
        ]
        self.request = SemanticRequest(
            evidence=(
                EvidenceRef(
                    evidence_id="user.1",
                    source_type="user_message",
                    source_field="preferences",
                    summary="安静、适合聊天",
                    confidence=1.0,
                ),
            ),
            queries=(
                SemanticQuery(
                    query_id="q1",
                    text="安静、适合聊天",
                    evidence_refs=("user.1",),
                ),
            ),
        )

    def test_rule_adapter_preserves_catalog_order_and_exclusions(self) -> None:
        result = RuleBasedCandidateRetriever().retrieve(
            self.candidates,
            semantic_request=self.request,
            exclusions={"plain"},
        )

        self.assertEqual([item.candidate.resource_id for item in result.items], ["quiet"])
        self.assertEqual(result.mode, "rule")
        self.assertTrue(result.items[0].evidence_refs)

    def test_hybrid_adapter_falls_back_without_local_index(self) -> None:
        result = HybridRagCandidateRetriever().retrieve(
            self.candidates,
            semantic_request=self.request,
        )

        self.assertEqual(result.mode, "rule")
        self.assertEqual(result.actual_adapter, "rule_based")
        self.assertEqual(result.requested_mode, "hybrid")
        self.assertIsNotNone(result.fallback_reason)
        self.assertEqual(result.items[0].candidate.resource_id, "quiet")
        self.assertGreaterEqual(result.items[0].embedding_score, 0.0)

    def test_retriever_evidence_contains_only_profile_values_hit_by_request(self) -> None:
        tagged = self.candidates[1].model_copy(
            update={"category_tags": ["书店", "安静", "户外"]}
        )

        result = RuleBasedCandidateRetriever().retrieve(
            [tagged],
            semantic_request=self.request,
        )

        profile_summaries = {
            ref.summary
            for ref in result.items[0].evidence_refs
            if ref.source_type == "poi_profile"
        }
        self.assertIn("安静", profile_summaries)
        self.assertNotIn("户外", profile_summaries)

    def test_empty_hybrid_request_keeps_legacy_order(self) -> None:
        result = HybridRagCandidateRetriever().retrieve(self.candidates)

        self.assertEqual([item.candidate.resource_id for item in result.items], ["plain", "quiet"])

    def test_hybrid_semantic_candidate_survives_three_stop_role_pool_truncation(self) -> None:
        distractors = [
            candidate(
                f"activity-{index}",
                ResourceType.ACTIVITY,
                f"普通活动 {index}",
                ["展览"],
            )
            for index in range(8)
        ]
        semantic_target = candidate(
            "z-semantic-target",
            ResourceType.ACTIVITY,
            "与需求高度相关的活动",
            ["展览"],
        )
        restaurants = [
            candidate("lunch", ResourceType.RESTAURANT, "午餐餐厅", ["餐厅"]),
            candidate("dinner", ResourceType.RESTAURANT, "晚餐餐厅", ["餐厅"]),
        ]
        constraints = planning_constraints(max_distance_km=30, time_end="22:00")
        skeleton = PlanSkeleton(
            skeleton_id="three-stop-regression",
            roles=(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
        )
        intent = PlanningIntent(
            optional_roles=(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
            minimum_stops=3,
            maximum_stops=3,
        )

        result = _rank_skeleton_plans(
            [*distractors, semantic_target, *restaurants],
            constraints,
            (skeleton,),
            intent,
            semantic_scores={semantic_target.resource_id: 100.0},
        )

        self.assertTrue(result.plans)
        self.assertTrue(
            any(
                semantic_target.resource_id
                in {stop.resource_id for stop in plan.stops}
                for plan in result.plans
            )
        )

    def test_planner_records_hybrid_mode_and_semantic_score_without_changing_hard_gate(self) -> None:
        catalog = InMemoryCatalog(
            [
                *self.candidates,
                candidate("meal", ResourceType.RESTAURANT, "安静餐厅", ["餐厅"]),
            ]
        )
        constraints = planning_constraints(time_end="18:00").model_copy(
            update={"preferences": ["安静"]}
        )
        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
            candidate_retriever=HybridRagCandidateRetriever(),
        ).plan(constraints)

        self.assertEqual(result.retrieval_mode, "rule")
        self.assertIsNotNone(result.retrieval_runtime_decision)
        self.assertEqual(result.retrieval_runtime_decision.stage, "candidate_retrieval")
        self.assertTrue(result.plans)
        self.assertTrue(
            any(
                item.rule_id == "planning.semantic_retrieval.v1"
                for plan in result.plans
                for item in plan.score_breakdown
            )
        )


if __name__ == "__main__":
    unittest.main()

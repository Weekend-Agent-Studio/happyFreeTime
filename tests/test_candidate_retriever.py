import unittest

from app.domain.catalog import ResourceType
from app.domain.semantics import EvidenceRef, SemanticQuery, SemanticRequest
from app.services.candidate_retriever import (
    HybridRagCandidateRetriever,
    RuleBasedCandidateRetriever,
)
from tests.test_native_planning import candidate
from tests.test_planning import FixedReplayRouteProvider, planning_constraints
from app.services.catalog import InMemoryCatalog
from app.services.planning import PlanningService


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

    def test_hybrid_adapter_ranks_semantically_similar_profile(self) -> None:
        result = HybridRagCandidateRetriever().retrieve(
            self.candidates,
            semantic_request=self.request,
        )

        self.assertEqual(result.mode, "hybrid")
        self.assertEqual(result.items[0].candidate.resource_id, "quiet")
        self.assertGreaterEqual(result.items[0].embedding_score, 0.0)

    def test_empty_hybrid_request_keeps_legacy_order(self) -> None:
        result = HybridRagCandidateRetriever().retrieve(self.candidates)

        self.assertEqual([item.candidate.resource_id for item in result.items], ["plain", "quiet"])

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

        self.assertEqual(result.retrieval_mode, "hybrid")
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

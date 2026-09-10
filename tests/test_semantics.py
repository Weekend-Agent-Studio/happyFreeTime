import unittest

from pydantic import ValidationError

from app.domain.constraints import StopRole
from app.domain.semantics import (
    EvidenceRef,
    SemanticQuery,
    SemanticRequest,
    SoftObjective,
    SoftObjectiveKind,
)
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from tests.test_planning import planning_constraints


class SemanticContractTest(unittest.TestCase):
    def test_rule_provider_emits_grounded_objectives_and_queries(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "preferences": ["不希望太累", "适合聊天"],
                "scene_tags": ["约会"],
                "diet_tags": ["少辣"],
            }
        )

        request = RuleBasedPlanningIntentProvider().decide(constraints).intent.semantic_request

        self.assertEqual(
            {item.kind for item in request.objectives},
            {
                SoftObjectiveKind.LOW_FATIGUE,
                SoftObjectiveKind.CONVERSATION_FRIENDLY,
                SoftObjectiveKind.ROMANTIC,
                SoftObjectiveKind.LOW_SPICE,
            },
        )
        self.assertEqual(
            {item.text for item in request.queries},
            {"不希望太累", "适合聊天", "约会", "少辣"},
        )
        evidence_ids = {item.evidence_id for item in request.evidence}
        self.assertTrue(evidence_ids)
        self.assertTrue(
            all(set(item.evidence_refs).issubset(evidence_ids) for item in request.queries)
        )

    def test_unknown_long_tail_is_retained_as_open_query(self) -> None:
        constraints = planning_constraints().model_copy(
            update={"preferences": ["适合慢慢逛、能偶尔坐下来"]}
        )

        request = RuleBasedPlanningIntentProvider().decide(constraints).intent.semantic_request

        self.assertEqual([item.text for item in request.queries], ["适合慢慢逛、能偶尔坐下来"])
        self.assertEqual(request.objectives, ())

    def test_unresolved_evidence_reference_is_rejected(self) -> None:
        evidence = EvidenceRef(
            evidence_id="user.1",
            source_type="user_message",
            source_field="preferences",
            summary="安静",
            confidence=1.0,
        )
        with self.assertRaises(ValidationError):
            SemanticRequest(
                evidence=(evidence,),
                objectives=(
                    SoftObjective(
                        kind="quiet",
                        target_role=StopRole.ACTIVITY,
                        evidence_refs=("missing",),
                    ),
                ),
                queries=(
                    SemanticQuery(
                        query_id="q1",
                        text="安静",
                        evidence_refs=("user.1",),
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()

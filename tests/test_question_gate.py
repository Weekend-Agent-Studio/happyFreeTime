import unittest

from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    EnrichmentResult,
    Intent,
    Interpretation,
    NormalizedConstraints,
    RawConstraints,
)
from app.services.question_gate import GateContext, NeedQuestionGate


class NeedQuestionGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = NeedQuestionGate()

    def test_normal_planning_uses_transparent_budget_default_without_question(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.98},
        )
        enrichment = EnrichmentResult(
            constraints=NormalizedConstraints(
                budget_per_person=ConstraintValue[int](
                    value=120,
                    source=ConstraintSource.DEFAULT_RULE,
                    rule_id="budget.default.beijing.v1",
                )
            )
        )

        decision = self.gate.decide(interpretation, enrichment, GateContext())

        self.assertFalse(decision.need_question)
        self.assertIsNone(decision.field)

    def test_strict_budget_without_amount_asks_for_budget(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.98},
            raw_constraints=RawConstraints(
                budget_text="千万别超预算",
                strict_budget=True,
            ),
        )
        enrichment = EnrichmentResult(
            constraints=NormalizedConstraints(strict_budget=True)
        )

        decision = self.gate.decide(interpretation, enrichment, GateContext())

        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "budget_per_person")
        self.assertIn("预算", decision.question)
        self.assertEqual(decision.severity, "blocking")

    def test_execution_without_selected_plan_asks_for_selection_first(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.EXECUTE_PLAN,
            intent_scores={Intent.EXECUTE_PLAN: 0.99},
        )

        decision = self.gate.decide(
            interpretation,
            EnrichmentResult(constraints=NormalizedConstraints()),
            GateContext(has_plans=True),
        )

        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "selected_plan_index")
        self.assertIn("方案", decision.question)

    def test_unresolved_explicit_date_blocks_planning(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(date_text="等我忙完那天"),
        )

        decision = self.gate.decide(
            interpretation,
            EnrichmentResult(constraints=NormalizedConstraints()),
            GateContext(),
        )

        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "date")

    def test_age_is_only_required_when_a_candidate_has_an_age_limit(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(children=1),
        )
        enrichment = EnrichmentResult(
            constraints=NormalizedConstraints()
        )

        without_age_limit = self.gate.decide(
            interpretation,
            enrichment,
            GateContext(child_age_required=False),
        )
        with_age_limit = self.gate.decide(
            interpretation,
            enrichment,
            GateContext(child_age_required=True),
        )

        self.assertFalse(without_age_limit.need_question)
        self.assertTrue(with_age_limit.need_question)
        self.assertEqual(with_age_limit.field, "child_age")

    def test_unresolved_explicit_location_blocks_planning(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(location_text="我公司附近"),
        )

        decision = self.gate.decide(
            interpretation,
            EnrichmentResult(constraints=NormalizedConstraints()),
            GateContext(),
        )

        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "location")

    def test_unresolved_explicit_distance_blocks_planning(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(max_distance_text="不要跑到城市另一头"),
        )

        decision = self.gate.decide(
            interpretation,
            EnrichmentResult(constraints=NormalizedConstraints()),
            GateContext(),
        )

        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "max_distance_km")


if __name__ == "__main__":
    unittest.main()

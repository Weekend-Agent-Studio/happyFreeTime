import unittest

from app.domain.constraints import (
    ClarificationIssue,
    ConstraintSource,
    ConstraintValue,
    Intent,
    Interpretation,
    PartyProfile,
    PlanRequest,
    RawConstraints,
    TimeProposal,
    TimeScope,
)
from app.services.question_gate import GateContext, NeedQuestionGate


class NeedQuestionGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = NeedQuestionGate()
        self.interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
        )

    def test_visible_default_budget_does_not_trigger_question(self) -> None:
        request = PlanRequest(
            budget_per_person=ConstraintValue(
                value=120,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="budget.default.beijing.v1",
            )
        )
        decision = self.gate.decide(self.interpretation, request, GateContext())
        self.assertFalse(decision.need_question)

    def test_strict_budget_without_amount_asks_for_budget(self) -> None:
        request = PlanRequest(strict_budget=True)
        decision = self.gate.decide(self.interpretation, request, GateContext())
        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "budget_per_person")

    def test_execution_without_selected_plan_asks_for_selection(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.EXECUTE_PLAN,
            intent_scores={Intent.EXECUTE_PLAN: 1.0},
        )
        decision = self.gate.decide(
            interpretation, PlanRequest(), GateContext(has_plans=True)
        )
        self.assertEqual(decision.field, "selected_plan_index")

    def test_temporal_clarification_issue_controls_blocking(self) -> None:
        issue = ClarificationIssue(
            field="departure_at",
            code="DEPARTURE_TIME_REQUIRES_CLOCK",
            reason="explicit_departure_period_requires_clock",
            expected_value_type="clock",
            request_revision=0,
        )
        interpretation = self.interpretation.model_copy(
            update={
                "time_proposals": (
                    TimeProposal(
                        target="departure",
                        precision="period",
                        period=TimeScope.MORNING,
                        evidence="早上出发",
                    ),
                )
            }
        )
        decision = self.gate.decide(
            interpretation, PlanRequest(), GateContext(), issue=issue
        )
        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "departure_at")
        self.assertIn("具体时间", decision.question)

    def test_unresolved_location_still_blocks(self) -> None:
        interpretation = self.interpretation.model_copy(
            update={"raw_constraints": RawConstraints(location_text="我公司附近")}
        )
        decision = self.gate.decide(
            interpretation, PlanRequest(), GateContext()
        )
        self.assertEqual(decision.field, "location")

    def test_child_age_is_only_required_for_a_restricted_candidate(self) -> None:
        request = PlanRequest(
            party=ConstraintValue(
                value=PartyProfile(children=1),
                source=ConstraintSource.USER_EXPLICIT,
            )
        )
        without_limit = self.gate.decide(
            self.interpretation, request, GateContext(child_age_required=False)
        )
        with_limit = self.gate.decide(
            self.interpretation, request, GateContext(child_age_required=True)
        )
        self.assertFalse(without_limit.need_question)
        self.assertEqual(with_limit.field, "child_age")

    def test_unresolved_explicit_distance_still_blocks(self) -> None:
        interpretation = self.interpretation.model_copy(
            update={"raw_constraints": RawConstraints(max_distance_text="不要跑太远")}
        )
        decision = self.gate.decide(
            interpretation, PlanRequest(), GateContext()
        )
        self.assertEqual(decision.field, "max_distance_km")


if __name__ == "__main__":
    unittest.main()

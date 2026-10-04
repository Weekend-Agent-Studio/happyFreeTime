import unittest

from app.domain.constraints import (
    ClarificationIssue,
    ConstraintSource,
    ConstraintValue,
    Intent,
    PartyProfile,
    PlanRequest,
)
from app.services.question_policy import QuestionPolicyContext, QuestionPolicy


class QuestionPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = QuestionPolicy()

    def test_visible_default_budget_does_not_trigger_question(self) -> None:
        request = PlanRequest(
            budget_per_person=ConstraintValue(
                value=120,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="budget.default.beijing.v1",
            )
        )
        decision = self.policy.decide(
            Intent.PLAN_OUTING, request, QuestionPolicyContext()
        )
        self.assertFalse(decision.need_question)

    def test_strict_budget_without_amount_asks_for_budget(self) -> None:
        issue = ClarificationIssue(
            field="budget_per_person",
            code="STRICT_BUDGET_AMOUNT_REQUIRED",
            reason="strict_budget_needs_amount",
            expected_value_type="integer",
            request_revision=0,
        )
        decision = self.policy.decide(
            Intent.PLAN_OUTING,
            PlanRequest(strict_budget=True),
            QuestionPolicyContext(),
            issue=issue,
        )
        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "budget_per_person")

    def test_execution_without_selected_plan_asks_for_selection(self) -> None:
        decision = self.policy.decide(
            Intent.EXECUTE_PLAN,
            PlanRequest(),
            QuestionPolicyContext(has_plans=True),
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
        decision = self.policy.decide(
            Intent.PLAN_OUTING,
            PlanRequest(),
            QuestionPolicyContext(),
            issue=issue,
        )
        self.assertTrue(decision.need_question)
        self.assertEqual(decision.field, "departure_at")
        self.assertIn("具体时间", decision.question)

    def test_unresolved_location_still_blocks(self) -> None:
        issue = ClarificationIssue(
            field="location",
            code="LOCATION_REQUIRES_RESOLUTION",
            reason="explicit_location_not_resolved",
            expected_value_type="location",
            request_revision=0,
        )
        decision = self.policy.decide(
            Intent.PLAN_OUTING,
            PlanRequest(),
            QuestionPolicyContext(),
            issue=issue,
        )
        self.assertEqual(decision.field, "location")

    def test_child_age_is_only_required_for_a_restricted_candidate(self) -> None:
        request = PlanRequest(
            party=ConstraintValue(
                value=PartyProfile(children=1),
                source=ConstraintSource.USER_EXPLICIT,
            )
        )
        without_limit = self.policy.decide(
            Intent.PLAN_OUTING,
            request,
            QuestionPolicyContext(child_age_required=False),
        )
        with_limit = self.policy.decide(
            Intent.PLAN_OUTING,
            request,
            QuestionPolicyContext(child_age_required=True),
        )
        self.assertFalse(without_limit.need_question)
        self.assertEqual(with_limit.field, "child_age")

    def test_unresolved_explicit_distance_still_blocks(self) -> None:
        issue = ClarificationIssue(
            field="max_distance_km",
            code="MAX_DISTANCE_REQUIRES_NUMBER",
            reason="explicit_max_distance_not_resolved",
            expected_value_type="number",
            request_revision=0,
        )
        decision = self.policy.decide(
            Intent.PLAN_OUTING,
            PlanRequest(),
            QuestionPolicyContext(),
            issue=issue,
        )
        self.assertEqual(decision.field, "max_distance_km")


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import date

from app.domain.constraints import Intent
from app.services.demo_router import DemoRouter
from app.services.router_extractor import RouterContext


class DemoRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = DemoRouter()
        self.context = RouterContext(current_date=date(2026, 8, 12))

    def test_extracts_demo_constraints_deterministically(self) -> None:
        result = self.router.interpret(
            "今天下午和朋友出去玩，人均150，别太远",
            self.context,
        )

        self.assertEqual(result.primary_intent, Intent.PLAN_OUTING)
        self.assertIsNone(result.raw_constraints.adults)
        self.assertEqual(result.raw_constraints.budget_per_person, 150)
        self.assertEqual(result.raw_constraints.max_distance_text, "别太远")

    def test_follow_up_budget_is_extracted_from_combined_turn(self) -> None:
        result = self.router.interpret(
            "今天下午出去玩，别超预算\n用户补充：人均200",
            self.context,
        )

        self.assertTrue(result.raw_constraints.strict_budget)
        self.assertEqual(result.raw_constraints.budget_per_person, 200)

    def test_extracts_return_by_deadline(self) -> None:
        result = self.router.interpret(
            "下午出去玩，最晚18:00到家",
            self.context,
        )

        self.assertEqual(result.raw_constraints.return_by, "18:00")
        self.assertIsNotNone(result.raw_constraints.return_by_text)

    def test_keeps_departure_evidence_separate_from_return_deadline(self) -> None:
        result = self.router.interpret(
            "明天下午两点半准时出发，18:00 前回家",
            self.context,
        )

        self.assertEqual(result.raw_constraints.departure_at_text, "下午两点半准时出发")
        self.assertIsNone(result.raw_constraints.departure_at)
        self.assertEqual(result.raw_constraints.return_by, "18:00")

    def test_extracts_total_distance_with_its_original_evidence(self) -> None:
        result = self.router.interpret(
            "今天下午出去玩，全程不超过10公里",
            self.context,
        )

        self.assertEqual(result.raw_constraints.total_distance_km, 10.0)
        self.assertEqual(result.raw_constraints.total_distance_text, "全程不超过10公里")

    def test_preserves_unresolved_return_deadline_for_the_question_gate(self) -> None:
        result = self.router.interpret(
            "下午出去玩，最晚十八点回家",
            self.context,
        )

        self.assertEqual(result.raw_constraints.return_by_text, "最晚十八点回家")
        self.assertIsNone(result.raw_constraints.return_by)


if __name__ == "__main__":
    unittest.main()

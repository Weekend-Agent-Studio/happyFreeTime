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
        self.assertEqual(result.raw_constraints.adults, 2)
        self.assertEqual(result.raw_constraints.budget_per_person, 150)
        self.assertEqual(result.raw_constraints.max_distance_text, "别太远")

    def test_follow_up_budget_is_extracted_from_combined_turn(self) -> None:
        result = self.router.interpret(
            "今天下午出去玩，别超预算\n用户补充：人均200",
            self.context,
        )

        self.assertTrue(result.raw_constraints.strict_budget)
        self.assertEqual(result.raw_constraints.budget_per_person, 200)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue
from app.services.planning import PlanningService
from app.services.presenter import present_candidate_set
from tests.test_planning import FixedReplayRouteProvider, planning_constraints


class TemplatePresenterTest(unittest.TestCase):
    def test_plan_reply_only_uses_verified_plan_facts_and_active_constraints(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "return_by": ConstraintValue[str](
                    value="23:00",
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "total_distance_km": ConstraintValue[float](
                    value=30.0,
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        candidate_set = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(constraints)

        reply = present_candidate_set(candidate_set, constraints)

        self.assertIn("可行方案", reply)
        self.assertIn("23:00 前到家", reply)
        self.assertIn("全程不超过 30 km", reply)
        self.assertNotIn("实时营业", reply)


if __name__ == "__main__":
    unittest.main()

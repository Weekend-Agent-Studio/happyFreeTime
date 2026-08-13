import unittest
from datetime import date

from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    NormalizedConstraints,
    PartyProfile,
    TimeWindow,
)
from app.domain.planning import RouteSource, StopType
from app.services.planning import PlanningService


def planning_constraints(*, budget: int = 120, strict_budget: bool = False) -> NormalizedConstraints:
    return NormalizedConstraints(
        date=ConstraintValue[date](
            value=date(2026, 8, 15),
            source=ConstraintSource.USER_INFERRED,
        ),
        time_window=ConstraintValue[TimeWindow](
            value=TimeWindow(start="14:00", end="18:00"),
            source=ConstraintSource.USER_INFERRED,
        ),
        location=ConstraintValue[GeoLocation](
            value=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
            source=ConstraintSource.SYSTEM_CONTEXT,
        ),
        party=ConstraintValue[PartyProfile](
            value=PartyProfile(adults=1),
            source=ConstraintSource.DEFAULT_RULE,
        ),
        budget_per_person=ConstraintValue[int](
            value=budget,
            source=ConstraintSource.USER_EXPLICIT,
        ),
        max_distance_km=ConstraintValue[float](
            value=8.0,
            source=ConstraintSource.USER_INFERRED,
        ),
        strict_budget=strict_budget,
    )


class PlanningServiceTest(unittest.TestCase):
    def test_builds_structured_two_stop_plans_from_the_mock_catalog(self) -> None:
        result = PlanningService().plan(planning_constraints())

        self.assertGreaterEqual(len(result.plans), 1)
        self.assertLessEqual(len(result.plans), 3)
        self.assertIsNone(result.conflict)
        for plan in result.plans:
            self.assertEqual([stop.type for stop in plan.stops], [StopType.ACTIVITY, StopType.RESTAURANT])
            self.assertEqual(len(plan.route_legs), 2)
            self.assertTrue(
                all(leg.source == RouteSource.LOCAL_ESTIMATE for leg in plan.route_legs)
            )
            self.assertLessEqual(plan.stops[-1].end, "18:00")
            self.assertGreater(plan.total_duration_minutes, 0)
            self.assertGreater(plan.total_score, 0)

    def test_strict_budget_returns_a_conflict_instead_of_silent_relaxation(self) -> None:
        result = PlanningService().plan(
            planning_constraints(budget=10, strict_budget=True)
        )

        self.assertEqual(result.plans, [])
        self.assertIsNotNone(result.conflict)
        self.assertEqual(result.conflict.code, "NO_PLAN_WITHIN_STRICT_BUDGET")
        self.assertIn("预算", result.conflict.message)
        self.assertTrue(result.conflict.relaxation_options)


if __name__ == "__main__":
    unittest.main()

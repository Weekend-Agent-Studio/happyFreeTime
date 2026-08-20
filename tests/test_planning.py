import unittest
from datetime import date, datetime, timezone

from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    NormalizedConstraints,
    PartyProfile,
    TimeWindow,
)
from app.domain.planning import RouteSource, StopType
from app.domain.providers import (
    ProviderMode,
    ProviderSource,
    WeatherFact,
    WeatherRequest,
)
from app.providers.weather import InMemoryWeatherReplayStore, ReplayWeatherProvider
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

    def test_rain_eliminates_weather_sensitive_outdoor_stops_but_keeps_indoor_stops(self) -> None:
        rainy_weather = WeatherFact(
            city="北京市",
            district="朝阳区",
            date=date(2026, 8, 15),
            condition="中雨",
            temperature_c=23,
            precipitation_mm=8.0,
            is_adverse=True,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc),
            verified_at=datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
        )
        constraints = planning_constraints().model_copy(
            update={
                "party": ConstraintValue[PartyProfile](
                    value=PartyProfile(adults=1, children=1, child_age=6),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "preferences": ["亲子"],
                "scene_tags": ["家庭"],
            }
        )
        store = InMemoryWeatherReplayStore()
        request = WeatherRequest(
            city="北京市",
            district="朝阳区",
            adcode="110105",
            date=date(2026, 8, 15),
        )
        store.save(request, rainy_weather)

        result = PlanningService(
            weather_provider=ReplayWeatherProvider(
                store=store,
                clock=lambda: datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
            )
        ).plan(constraints)

        self.assertGreaterEqual(len(result.plans), 1)
        self.assertIsNone(result.conflict)
        activity_ids = {
            stop.resource_id
            for plan in result.plans
            for stop in plan.stops
            if stop.type == StopType.ACTIVITY
        }
        self.assertIn("act_001", activity_ids)
        self.assertNotIn("act_002", activity_ids)
        self.assertEqual(result.provider_facts[0].source, ProviderSource.REPLAY)
        self.assertEqual(result.provider_facts[0].condition, "中雨")


if __name__ == "__main__":
    unittest.main()

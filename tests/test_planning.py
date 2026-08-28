import unittest
from datetime import date, datetime, timezone

from app.domain.catalog import VerificationStatus, ViolationCode
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
    GeoPoint,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteRequest,
    WeatherFact,
    WeatherRequest,
)
from app.providers.weather import InMemoryWeatherReplayStore, ReplayWeatherProvider
from app.services.catalog import InMemoryCatalog, LocalFixtureCatalog, SnapshotCatalog
from app.services.planning import PlanningService


class FixedReplayRouteProvider:
    def __init__(
        self,
        duration_minutes: int = 20,
        distance_km: float = 4.2,
    ) -> None:
        self.requests: list[RouteRequest] = []
        self.duration_minutes = duration_minutes
        self.distance_km = distance_km

    def route(self, request: RouteRequest) -> RouteFact:
        self.requests.append(request)
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            distance_km=self.distance_km,
            duration_minutes=self.duration_minutes,
            geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY,
            provider_mode=ProviderMode.REPLAY,
            verified_at=datetime(2026, 8, 15, 8, 5, tzinfo=timezone.utc),
        )


def planning_constraints(
    *,
    budget: int = 120,
    strict_budget: bool = False,
    max_distance_km: float = 8.0,
    time_end: str = "18:00",
) -> NormalizedConstraints:
    return NormalizedConstraints(
        date=ConstraintValue[date](
            value=date(2026, 8, 15),
            source=ConstraintSource.USER_INFERRED,
        ),
        time_window=ConstraintValue[TimeWindow](
            value=TimeWindow(start="14:00", end=time_end),
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
            value=max_distance_km,
            source=ConstraintSource.USER_INFERRED,
        ),
        strict_budget=strict_budget,
    )


class PlanningServiceTest(unittest.TestCase):
    def test_default_catalog_uses_generated_snapshot_records(self) -> None:
        result = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2)
        ).plan(planning_constraints(max_distance_km=30, time_end="22:00"))

        self.assertGreaterEqual(len(result.plans), 1)
        self.assertTrue(all(plan.price_status.value == "incomplete" for plan in result.plans))
        self.assertTrue(
            all(
                stop.resource_id.startswith("osm-")
                for plan in result.plans
                for stop in plan.stops
            )
        )

    def test_snapshot_catalog_runs_through_planning_and_route_verification_offline(self) -> None:
        route_provider = FixedReplayRouteProvider(duration_minutes=10, distance_km=2)

        result = PlanningService(
            catalog=SnapshotCatalog(),
            route_provider=route_provider,
        ).plan(
            planning_constraints(max_distance_km=30, time_end="22:00")
        )

        self.assertGreaterEqual(len(result.plans), 1)
        self.assertIsNone(result.conflict)
        self.assertTrue(
            all(
                stop.resource_id.startswith("osm-")
                for plan in result.plans
                for stop in plan.stops
            )
        )
        self.assertTrue(
            all(
                stop.source.source_license == "ODbL-1.0"
                for plan in result.plans
                for stop in plan.stops
            )
        )
        self.assertEqual(len(route_provider.requests), len(result.plans) * 2)

    def test_strict_budget_rejects_unknown_snapshot_prices_before_combination(self) -> None:
        result = PlanningService().plan(
            planning_constraints(
                budget=100,
                strict_budget=True,
                max_distance_km=15,
                time_end="20:00",
            )
        )

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.code, "NO_PLAN_WITHIN_STRICT_BUDGET")
        self.assertIn(
            ViolationCode.SINGLE_RESOURCE_PRICE_UNVERIFIED,
            {item.code for item in result.catalog_violations},
        )

    def test_unknown_catalog_facts_are_exposed_only_for_final_plan_stops(self) -> None:
        recalled = LocalFixtureCatalog().recall(
            planning_constraints(max_distance_km=30, time_end="22:00")
        ).candidates
        unknown = [item.model_copy(update={"open_hours": {}}) for item in recalled]

        result = PlanningService(
            catalog=InMemoryCatalog(unknown),
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
        ).plan(planning_constraints(max_distance_km=30, time_end="22:00"))

        selected_ids = {stop.resource_id for plan in result.plans for stop in plan.stops}
        self.assertTrue(result.catalog_warnings)
        self.assertEqual(
            {warning.resource_id for warning in result.catalog_warnings},
            selected_ids,
        )

    def test_finalist_routes_rebuild_stop_times_from_provider_durations(self) -> None:
        route_provider = FixedReplayRouteProvider()

        result = PlanningService(
            route_provider=route_provider,
            catalog=LocalFixtureCatalog(),
        ).plan(
            planning_constraints()
        )

        self.assertGreaterEqual(len(result.plans), 1)
        self.assertEqual(len(route_provider.requests), len(result.plans) * 2)
        first = result.plans[0]
        self.assertEqual(first.route_legs[0].start, "14:00")
        self.assertEqual(first.route_legs[0].end, "14:20")
        self.assertEqual(first.stops[0].start, "14:20")
        self.assertEqual(first.route_legs[1].start, first.stops[0].end)
        self.assertEqual(first.stops[1].start, first.route_legs[1].end)
        self.assertEqual(first.stops[1].end, "17:40")
        self.assertEqual(first.total_duration_minutes, 220)
        self.assertEqual(
            first.total_duration_minutes,
            sum(stop.duration_minutes for stop in first.stops)
            + sum(leg.duration_minutes for leg in first.route_legs),
        )
        self.assertTrue(
            all(leg.source == RouteSource.REPLAY for leg in first.route_legs)
        )
        self.assertEqual(len(first.route_legs[0].geometry), 2)
        self.assertEqual(
            first.route_legs[0].geometry[0],
            GeoPoint(latitude=39.9219, longitude=116.4436),
        )

    def test_route_verification_eliminates_finalists_that_overrun_time_window(self) -> None:
        route_provider = FixedReplayRouteProvider(duration_minutes=60)

        result = PlanningService(
            route_provider=route_provider,
            catalog=LocalFixtureCatalog(),
        ).plan(
            planning_constraints()
        )

        self.assertEqual(result.plans, [])
        self.assertIsNotNone(result.conflict)
        self.assertEqual(result.conflict.code, "NO_PLAN_AFTER_ROUTE_VERIFICATION")
        self.assertEqual(result.conflict.fields, ["time_window"])
        self.assertGreater(len(route_provider.requests), 0)

    def test_route_verification_reports_next_day_departure_as_a_conflict(self) -> None:
        route_provider = FixedReplayRouteProvider(duration_minutes=5000)

        result = PlanningService(
            route_provider=route_provider,
            catalog=LocalFixtureCatalog(),
        ).plan(planning_constraints())

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.code, "NO_PLAN_AFTER_ROUTE_VERIFICATION")
        self.assertEqual(result.conflict.fields, ["time_window"])

    def test_route_verification_eliminates_finalists_with_an_overlong_leg(self) -> None:
        route_provider = FixedReplayRouteProvider(distance_km=12.0)

        result = PlanningService(
            route_provider=route_provider,
            catalog=LocalFixtureCatalog(),
        ).plan(
            planning_constraints()
        )

        self.assertEqual(result.plans, [])
        self.assertIsNotNone(result.conflict)
        self.assertEqual(result.conflict.code, "NO_PLAN_AFTER_ROUTE_VERIFICATION")
        self.assertEqual(result.conflict.fields, ["max_distance_km"])

    def test_builds_structured_two_stop_plans_from_the_mock_catalog(self) -> None:
        result = PlanningService(catalog=LocalFixtureCatalog()).plan(
            planning_constraints()
        )

        self.assertGreaterEqual(len(result.plans), 1)
        self.assertLessEqual(len(result.plans), 3)
        self.assertIsNone(result.conflict)
        for plan in result.plans:
            self.assertEqual(
                [stop.type for stop in plan.stops],
                [StopType.ACTIVITY, StopType.RESTAURANT],
            )
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
        self.assertNotIn("双站", result.conflict.message)
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
            ),
            catalog=LocalFixtureCatalog(),
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

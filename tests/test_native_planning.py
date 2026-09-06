import unittest
from datetime import datetime, timezone

from app.domain.catalog import (
    CatalogSource,
    PriceKind,
    ResourceType,
    StopCandidate,
    VerificationStatus,
)
from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    NormalizedConstraints,
    PartyProfile,
    TimeWindow,
)
from app.domain.planning import PlanPriceStatus, ScoreContribution, StopRole
from app.domain.providers import (
    AvailabilityStatus,
    GeoPoint,
    ProviderMode,
    ProviderSource,
    WeatherFact,
    WeatherRequest,
)
from app.providers.availability import MockAvailabilityProvider
from app.services.catalog import InMemoryCatalog
from app.services.planning import (
    PlanningService,
    _apply_dynamic_strategies,
    _diversify_plans,
)
from app.services.plan_verifier import PlanVerifier
from tests.test_planning import FixedReplayRouteProvider, planning_constraints


SOURCE = CatalogSource(
    source_name="native-planning-test",
    source_uri="https://example.test/catalog",
    source_license="test-only",
    collected_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    last_verified_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    verification_status=VerificationStatus.VERIFIED,
)


def candidate(
    resource_id: str,
    resource_type: ResourceType,
    name: str,
    tags: list[str],
    duration_minutes: int = 60,
    diet_tags: list[str] | None = None,
    scene_tags: list[str] | None = None,
    avg_price: int = 50,
    price_kind: PriceKind = PriceKind.KNOWN,
    weather_sensitive: bool = False,
    open_hours: dict[str, str] | None = None,
) -> StopCandidate:
    return StopCandidate(
        resource_id=resource_id,
        resource_type=resource_type,
        name=name,
        location=GeoPoint(latitude=39.92, longitude=116.44),
        category_tags=tags,
        diet_tags=diet_tags or [],
        scene_tags=scene_tags or [],
        avg_price=avg_price,
        price_kind=price_kind,
        duration_minutes=duration_minutes,
        open_hours=open_hours or {},
        weather_sensitive=weather_sensitive,
        source=SOURCE,
    )


class RainyWeatherProvider:
    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        return WeatherFact(
            city=request.city,
            district=request.district,
            date=request.date,
            condition="中雨",
            temperature_c=23,
            precipitation_mm=8,
            is_adverse=True,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            verified_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )


class NativePlanningBehaviorTest(unittest.TestCase):
    def test_exact_departure_drives_local_timeline_and_route_requests(self) -> None:
        route_provider = FixedReplayRouteProvider(duration_minutes=10, distance_km=1)
        constraints = planning_constraints(budget=1_000, time_end="18:00").model_copy(
            update={
                "departure_at": ConstraintValue[str](
                    value="14:30", source=ConstraintSource.USER_EXPLICIT
                ),
                "return_by": ConstraintValue[str](
                    value="18:00", source=ConstraintSource.USER_EXPLICIT
                ),
            }
        )
        result = PlanningService(
            catalog=InMemoryCatalog(
                [
                    candidate("activity", ResourceType.ACTIVITY, "展览", ["展览"], duration_minutes=30),
                    candidate("meal", ResourceType.RESTAURANT, "晚餐", ["餐厅"], duration_minutes=30),
                ]
            ),
            route_provider=route_provider,
        ).plan(constraints)

        self.assertTrue(result.plans)
        first = result.plans[0]
        self.assertEqual(first.route_legs[0].start, "14:30")
        self.assertEqual(first.stops[0].start, "14:40")
        self.assertEqual(route_provider.requests[0].departure_at.hour, 14)
        self.assertEqual(route_provider.requests[0].departure_at.minute, 30)
        self.assertEqual(route_provider.requests[0].departure_at.date(), constraints.date.value)
        self.assertLessEqual(first.route_legs[-1].end, "18:00")

    def test_invalid_departure_conflict_skips_route_provider(self) -> None:
        route_provider = FixedReplayRouteProvider(duration_minutes=10, distance_km=1)
        constraints = planning_constraints(budget=1_000, time_end="18:00").model_copy(
            update={
                "departure_at": ConstraintValue[str](
                    value="17:30", source=ConstraintSource.USER_EXPLICIT
                ),
                "return_by": ConstraintValue[str](
                    value="17:00", source=ConstraintSource.USER_EXPLICIT
                ),
            }
        )
        result = PlanningService(route_provider=route_provider).plan(constraints)

        self.assertEqual(result.conflict.code, "DEPARTURE_NOT_BEFORE_RETURN_BY")
        self.assertEqual(result.conflict.fields, ["departure_at", "return_by"])
        self.assertEqual(route_provider.requests, [])

    def test_departure_outside_explicit_window_skips_route_provider(self) -> None:
        route_provider = FixedReplayRouteProvider(duration_minutes=10, distance_km=1)
        constraints = planning_constraints(budget=1_000, time_end="18:00").model_copy(
            update={
                "departure_at": ConstraintValue[str](
                    value="13:30", source=ConstraintSource.USER_EXPLICIT
                ),
            }
        )
        result = PlanningService(route_provider=route_provider).plan(constraints)

        self.assertEqual(result.conflict.code, "DEPARTURE_OUTSIDE_TIME_WINDOW")
        self.assertEqual(result.conflict.fields, ["departure_at", "time_window"])
        self.assertEqual(route_provider.requests, [])

    def test_verified_unavailable_stop_is_locally_replaced_without_changing_other_stop(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity-a", ResourceType.ACTIVITY, "展览", ["展览"]),
                candidate("meal-a", ResourceType.RESTAURANT, "餐厅 A", ["餐厅"]),
                candidate("meal-b", ResourceType.RESTAURANT, "餐厅 B", ["餐厅"]),
            ]
        )
        availability = MockAvailabilityProvider(
            statuses={"meal-a": AvailabilityStatus.UNAVAILABLE},
            clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
            availability_provider=availability,
        ).plan(planning_constraints(budget=1_000, time_end="18:00"))

        self.assertTrue(result.plans)
        self.assertTrue(all("meal-a" not in {stop.resource_id for stop in plan.stops} for plan in result.plans))
        self.assertTrue(any([stop.resource_id for stop in plan.stops] == ["activity-a", "meal-b"] for plan in result.plans))
        self.assertTrue(any(item.code == "availability_unconfirmed" for item in result.warnings))

    def test_two_local_replan_rounds_can_reach_a_third_local_candidate_within_budget(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity-a", ResourceType.ACTIVITY, "展览", ["展览"]),
                candidate("meal-a", ResourceType.RESTAURANT, "餐厅 A", ["餐厅"]),
                candidate("meal-b", ResourceType.RESTAURANT, "餐厅 B", ["餐厅"]),
                candidate("meal-c", ResourceType.RESTAURANT, "餐厅 C", ["餐厅"]),
            ]
        )
        availability = MockAvailabilityProvider(
            statuses={"meal-a": AvailabilityStatus.UNAVAILABLE, "meal-b": AvailabilityStatus.UNAVAILABLE, "meal-c": AvailabilityStatus.AVAILABLE},
            clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
            availability_provider=availability,
        ).plan(planning_constraints(budget=1_000, time_end="18:00"))

        self.assertTrue(result.plans)
        self.assertEqual(result.plans[0].stops[0].resource_id, "activity-a")
        self.assertEqual(result.plans[0].stops[1].resource_id, "meal-c")

    def test_local_replan_limit_returns_structured_conflict_and_stays_within_provider_budget(self) -> None:
        class CountingUnavailableProvider:
            def __init__(self) -> None:
                self.calls = 0

            def check(self, request):
                self.calls += 1
                return MockAvailabilityProvider(
                    statuses={check.resource_id: AvailabilityStatus.UNAVAILABLE for check in request.checks},
                    clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
                ).check(request)

        catalog = InMemoryCatalog(
            [
                candidate("activity-a", ResourceType.ACTIVITY, "展览", ["展览"]),
                candidate("meal-a", ResourceType.RESTAURANT, "餐厅 A", ["餐厅"]),
                candidate("meal-b", ResourceType.RESTAURANT, "餐厅 B", ["餐厅"]),
                candidate("meal-c", ResourceType.RESTAURANT, "餐厅 C", ["餐厅"]),
            ]
        )
        availability = CountingUnavailableProvider()
        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
            availability_provider=availability,
        ).plan(planning_constraints(budget=1_000, time_end="18:00"))

        self.assertFalse(result.plans)
        self.assertEqual(result.conflict.code, "NO_PLAN_AFTER_LOCAL_REPLAN")
        self.assertIn("availability", result.conflict.fields)
        self.assertLessEqual(availability.calls, 6)
    def test_afternoon_request_can_produce_activity_break_dinner(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "14:00-17:00"},
                ),
                candidate(
                    "break",
                    ResourceType.CAFE,
                    "下午茶",
                    ["咖啡"],
                    duration_minutes=60,
                    open_hours={"sat": "16:00-19:00"},
                ),
                candidate(
                    "dinner",
                    ResourceType.RESTAURANT,
                    "晚餐馆",
                    ["晚餐"],
                    duration_minutes=90,
                    open_hours={"sat": "17:00-22:00"},
                ),
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            time_end="21:00",
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(
                duration_minutes=5,
                distance_km=1,
            ),
        ).plan(constraints)

        break_plan = next(
            (
                plan
                for plan in result.plans
                if plan.skeleton_id == "activity-break-dinner-v1"
            ),
            None,
        )
        self.assertIsNotNone(break_plan)
        self.assertEqual(
            [stop.resource_id for stop in break_plan.stops],
            ["activity", "break", "dinner"],
        )
        self.assertEqual(
            [stop.role.value for stop in break_plan.stops],
            ["activity", "break", "dinner"],
        )
        self.assertEqual(len(break_plan.route_legs), 3)

    def test_full_day_request_can_produce_activity_lunch_activity_dinner(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "morning-activity",
                    ResourceType.ACTIVITY,
                    "上午展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "09:00-12:00"},
                ),
                candidate(
                    "lunch",
                    ResourceType.RESTAURANT,
                    "午餐馆",
                    ["午餐"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-14:00"},
                ),
                candidate(
                    "afternoon-activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=300,
                    open_hours={"sat": "12:00-18:00"},
                ),
                candidate(
                    "dinner",
                    ResourceType.RESTAURANT,
                    "晚餐馆",
                    ["晚餐"],
                    duration_minutes=60,
                    open_hours={"sat": "17:00-21:00"},
                ),
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            time_end="21:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="09:00", end="21:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(
                duration_minutes=5,
                distance_km=1,
            ),
        ).plan(constraints)

        four_stop = next(
            (plan for plan in result.plans if len(plan.stops) == 4),
            None,
        )
        self.assertIsNotNone(four_stop)
        self.assertEqual(
            [stop.resource_id for stop in four_stop.stops],
            ["morning-activity", "lunch", "afternoon-activity", "dinner"],
        )
        self.assertEqual(
            [stop.role.value for stop in four_stop.stops],
            ["activity", "lunch", "activity", "dinner"],
        )
        self.assertEqual(
            len({stop.resource_id for stop in four_stop.stops}),
            4,
        )
        self.assertEqual(len(four_stop.route_legs), 4)

    def test_full_day_request_can_produce_lunch_activity_dinner(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "lunch",
                    ResourceType.RESTAURANT,
                    "午餐馆",
                    ["午餐"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-14:00"},
                ),
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=270,
                    open_hours={"sat": "13:00-18:00"},
                ),
                candidate(
                    "dinner",
                    ResourceType.RESTAURANT,
                    "晚餐馆",
                    ["晚餐"],
                    duration_minutes=60,
                    open_hours={"sat": "17:00-21:00"},
                ),
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            time_end="20:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="12:00", end="20:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(
                duration_minutes=5,
                distance_km=1,
            ),
        ).plan(constraints)

        three_stop = next(
            (plan for plan in result.plans if len(plan.stops) == 3),
            None,
        )
        self.assertIsNotNone(three_stop)
        self.assertEqual(
            [stop.resource_id for stop in three_stop.stops],
            ["lunch", "activity", "dinner"],
        )
        self.assertEqual(
            [stop.role.value for stop in three_stop.stops],
            ["lunch", "activity", "dinner"],
        )
        self.assertEqual(len(three_stop.route_legs), 3)

    def test_activity_meal_skeleton_accepts_a_cafe_for_the_meal_role(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                ),
                candidate(
                    "cafe",
                    ResourceType.CAFE,
                    "安静咖啡馆",
                    ["咖啡"],
                ),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(
                duration_minutes=5,
                distance_km=1,
            ),
        ).plan(planning_constraints(time_end="20:00"))

        self.assertIsNone(result.conflict)
        self.assertTrue(result.plans)
        self.assertEqual(
            [stop.type.value for stop in result.plans[0].stops],
            ["activity", "cafe"],
        )

    def test_lunch_and_dinner_roles_require_meal_resources(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=270,
                    open_hours={"sat": "12:00-20:00"},
                ),
                candidate(
                    "cafe",
                    ResourceType.CAFE,
                    "咖啡馆",
                    ["咖啡"],
                    open_hours={"sat": "11:00-21:00"},
                ),
                candidate(
                    "dessert",
                    ResourceType.DESSERT,
                    "甜品店",
                    ["甜品"],
                    open_hours={"sat": "11:00-21:00"},
                ),
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            time_end="20:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="12:00", end="20:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(
                duration_minutes=5,
                distance_km=1,
            ),
        ).plan(constraints)

        self.assertTrue(result.plans)
        self.assertTrue(all(len(plan.stops) == 2 for plan in result.plans))
        self.assertTrue(
            all(plan.skeleton_id == "activity-meal-v1" for plan in result.plans)
        )

    def test_route_verification_attempts_are_bounded_when_every_plan_fails(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "14:00-20:00"},
                ),
                *[
                    candidate(
                        f"closed-{index:02d}",
                        ResourceType.RESTAURANT,
                        f"下午茶限定店 {index:02d}",
                        ["甜品"],
                        open_hours={"sat": "14:00-16:00"},
                    )
                    for index in range(20)
                ],
            ]
        )
        route_provider = FixedReplayRouteProvider(duration_minutes=5, distance_km=1)

        result = PlanningService(
            catalog=catalog,
            route_provider=route_provider,
        ).plan(planning_constraints(time_end="20:00"))

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.fields, ["opening_hours"])
        self.assertEqual(
            result.conflict.relaxation_options,
            ["调整到店时间或选择营业时段更匹配的地点"],
        )
        self.assertEqual(len(route_provider.requests), 24)

    def test_route_verification_budget_is_counted_by_legs_for_three_stop_plans(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=270,
                    open_hours={"sat": "12:00-20:00"},
                ),
                *[
                    candidate(
                        f"meal-{index:02d}",
                        ResourceType.RESTAURANT,
                        f"午间限定餐厅 {index:02d}",
                        ["餐厅"],
                        duration_minutes=60,
                        open_hours={"sat": "12:00-13:00"},
                    )
                    for index in range(20)
                ],
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            time_end="20:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="12:00", end="20:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )
        route_provider = FixedReplayRouteProvider(duration_minutes=5, distance_km=1)

        result = PlanningService(
            catalog=catalog,
            route_provider=route_provider,
        ).plan(constraints)

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.fields, ["opening_hours"])
        self.assertLessEqual(len(route_provider.requests), 24)

    def test_route_budget_preserves_a_feasible_lower_scored_skeleton(self) -> None:
        catalog = InMemoryCatalog(
            [
                *[
                    candidate(
                        f"activity-{index}",
                        ResourceType.ACTIVITY,
                        f"活动 {index}",
                        ["活动"],
                        duration_minutes=180,
                        open_hours={"sat": "09:00-20:00"},
                    )
                    for index in range(5)
                ],
                *[
                    candidate(
                        f"meal-{index}",
                        ResourceType.RESTAURANT,
                        f"午间餐厅 {index}",
                        ["餐厅"],
                        duration_minutes=60,
                        open_hours={"sat": "09:00-14:00"},
                    )
                    for index in range(5)
                ],
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            time_end="21:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="09:00", end="21:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(
                duration_minutes=5,
                distance_km=1,
            ),
        ).plan(constraints)

        self.assertTrue(result.plans)
        self.assertTrue(any(len(plan.stops) == 2 for plan in result.plans))
        self.assertLessEqual(len(result.plans[0].route_legs), 4)

    def test_opening_and_closing_boundaries_are_inclusive(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    duration_minutes=110,
                    open_hours={"sat": "14:05-15:55"},
                ),
                candidate(
                    "boundary-restaurant",
                    ResourceType.RESTAURANT,
                    "整点营业餐厅",
                    ["餐厅"],
                    open_hours={"sat": "16:00-17:00"},
                ),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(time_end="20:00"))

        self.assertTrue(result.plans)
        activity, restaurant = result.plans[0].stops
        self.assertEqual((activity.start, activity.end), ("14:05", "15:55"))
        self.assertEqual((restaurant.start, restaurant.end), ("16:00", "17:00"))

    def test_route_verification_backfills_after_top_three_become_infeasible(self) -> None:
        closed_restaurants = [
            candidate(
                f"preferred-closed-{index}",
                ResourceType.RESTAURANT,
                f"下午茶限定店 {index}",
                ["甜品"],
                open_hours={"sat": "14:00-16:00"},
            )
            for index in range(3)
        ]
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "14:00-20:00"},
                ),
                *closed_restaurants,
                candidate(
                    "lower-ranked-open",
                    ResourceType.RESTAURANT,
                    "晚间简餐",
                    ["简餐"],
                    open_hours={"sat": "16:00-20:00"},
                ),
            ]
        )
        constraints = planning_constraints(time_end="20:00").model_copy(
            update={"preferences": ["甜品"]}
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(constraints)

        self.assertTrue(result.plans)
        self.assertEqual(
            {plan.stops[1].resource_id for plan in result.plans},
            {"lower-ranked-open"},
        )

    def test_evening_visit_uses_the_matching_opening_hours_interval(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "09:00-20:00"},
                ),
                candidate(
                    "split-hours",
                    ResourceType.RESTAURANT,
                    "分时段营业餐厅",
                    ["餐厅"],
                    open_hours={"sat": "11:00-14:00,16:00-20:00"},
                ),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(time_end="20:00"))

        self.assertTrue(result.plans)
        self.assertEqual(result.plans[0].stops[1].resource_id, "split-hours")

    def test_route_verified_plan_requires_each_visit_to_fit_opening_hours(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "14:00-20:00"},
                ),
                candidate(
                    "closed-at-arrival",
                    ResourceType.RESTAURANT,
                    "下午茶限定店",
                    ["甜品"],
                    open_hours={"sat": "14:00-16:00"},
                ),
                candidate(
                    "open-at-arrival",
                    ResourceType.RESTAURANT,
                    "晚间甜品店",
                    ["甜品"],
                    open_hours={"sat": "16:00-20:00"},
                ),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(time_end="20:00"))

        self.assertTrue(result.plans)
        restaurant_ids = {plan.stops[1].resource_id for plan in result.plans}
        self.assertEqual(restaurant_ids, {"open-at-arrival"})

    def test_explicit_preference_changes_the_top_ranked_plan(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                candidate("quiet", ResourceType.RESTAURANT, "静巷茶馆", ["安静", "tea"]),
                candidate("dessert", ResourceType.RESTAURANT, "糖霜甜品店", ["甜品", "cake"]),
            ]
        )
        planner = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )

        quiet = planner.plan(
            planning_constraints(time_end="20:00").model_copy(
                update={"preferences": ["安静"]}
            )
        )
        dessert = planner.plan(
            planning_constraints(time_end="20:00").model_copy(
                update={"preferences": ["甜品"]}
            )
        )

        self.assertEqual(quiet.plans[0].stops[1].resource_id, "quiet")
        self.assertEqual(dessert.plans[0].stops[1].resource_id, "dessert")
        self.assertNotEqual(
            [stop.resource_id for stop in quiet.plans[0].stops],
            [stop.resource_id for stop in dessert.plans[0].stops],
        )

    def test_diet_and_scene_preferences_change_restaurant_ranking(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                candidate(
                    "quiet-vegetarian",
                    ResourceType.RESTAURANT,
                    "静巷素食",
                    ["中餐"],
                    diet_tags=["素食"],
                    scene_tags=["安静"],
                ),
                candidate(
                    "lively-meat",
                    ResourceType.RESTAURANT,
                    "热闹烤肉",
                    ["烧烤"],
                    diet_tags=["肉食"],
                    scene_tags=["朋友聚餐"],
                ),
            ]
        )
        planner = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )
        base = planning_constraints(time_end="20:00")

        quiet = planner.plan(
            base.model_copy(update={"diet_tags": ["素食"], "scene_tags": ["安静"]})
        )
        lively = planner.plan(
            base.model_copy(
                update={"diet_tags": ["肉食"], "scene_tags": ["朋友聚餐"]}
            )
        )

        self.assertEqual(quiet.plans[0].stops[1].resource_id, "quiet-vegetarian")
        self.assertEqual(lively.plans[0].stops[1].resource_id, "lively-meat")

    def test_avoid_tags_penalize_conflicting_compositions(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                candidate("avoid", ResourceType.RESTAURANT, "热闹餐厅", ["热闹"]),
                candidate("calm", ResourceType.RESTAURANT, "安静餐厅", ["安静"]),
            ]
        )
        planner = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )

        result = planner.plan(
            planning_constraints(time_end="20:00").model_copy(
                update={"avoid": ["热闹"]}
            )
        )

        self.assertEqual(result.plans[0].stops[1].resource_id, "calm")
        conflicting = next(
            plan for plan in result.plans if plan.stops[1].resource_id == "avoid"
        )
        self.assertIn(
            "planning.avoid_tag_penalty.v1",
            {item.rule_id for item in conflicting.score_breakdown},
        )

    def test_non_strict_budget_penalizes_but_keeps_over_budget_plans(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    avg_price=30,
                ),
                candidate(
                    "restaurant-z",
                    ResourceType.RESTAURANT,
                    "高价餐厅",
                    ["餐厅"],
                    avg_price=200,
                ),
                candidate(
                    "restaurant-a",
                    ResourceType.RESTAURANT,
                    "平价餐厅",
                    ["餐厅"],
                    avg_price=50,
                ),
            ]
        )
        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(budget=120, time_end="20:00"))

        self.assertEqual(result.plans[0].stops[1].resource_id, "restaurant-a")
        expensive = next(
            plan
            for plan in result.plans
            if plan.stops[1].resource_id == "restaurant-z"
        )
        self.assertIn(
            "planning.budget_preference.v1",
            {item.rule_id for item in expensive.score_breakdown},
        )
        self.assertTrue(any("超出预算偏好" in item for item in expensive.tradeoffs))

    def test_requested_duration_changes_the_feasible_top_plan(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "short-activity",
                    ResourceType.ACTIVITY,
                    "街区小展",
                    ["展览"],
                    duration_minutes=40,
                ),
                candidate(
                    "long-activity",
                    ResourceType.ACTIVITY,
                    "城市深度展",
                    ["展览"],
                    duration_minutes=140,
                ),
                candidate(
                    "restaurant",
                    ResourceType.RESTAURANT,
                    "附近简餐",
                    ["简餐"],
                    duration_minutes=40,
                ),
            ]
        )
        planner = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )
        base = planning_constraints(time_end="22:00")

        short = planner.plan(
            base.model_copy(
                update={
                    "duration_minutes": ConstraintValue[int](
                        value=120,
                        source=ConstraintSource.USER_EXPLICIT,
                    )
                }
            )
        )
        long = planner.plan(
            base.model_copy(
                update={
                    "duration_minutes": ConstraintValue[int](
                        value=240,
                        source=ConstraintSource.USER_EXPLICIT,
                    )
                }
            )
        )

        self.assertEqual(short.plans[0].stops[0].resource_id, "short-activity")
        self.assertLessEqual(short.plans[0].total_duration_minutes, 120)
        self.assertEqual(long.plans[0].stops[0].resource_id, "long-activity")
        self.assertLessEqual(long.plans[0].total_duration_minutes, 240)

    def test_snapshot_plans_explain_scores_without_legacy_lookup_errors(self) -> None:
        result = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2)
        ).plan(planning_constraints(max_distance_km=30, time_end="22:00"))

        self.assertTrue(result.plans)
        first = result.plans[0]
        self.assertTrue(all(stop.resource_id.startswith("osm-") for stop in first.stops))
        self.assertTrue(first.score_breakdown)
        self.assertAlmostEqual(
            sum(item.points for item in first.score_breakdown),
            first.total_score,
        )
        self.assertIn(
            "planning.catalog_feasible.v1",
            {item.rule_id for item in first.score_breakdown},
        )
        duration_reason = next(
            item
            for item in first.score_breakdown
            if item.rule_id == "planning.duration_fit.v1"
        )
        self.assertIn(
            f"route_checked_total_minutes={first.total_duration_minutes}",
            duration_reason.evidence,
        )
        explanations = [*first.highlights, *first.tradeoffs]
        self.assertFalse(any("不存在" in item for item in explanations))

    def test_snapshot_concept_preferences_produce_different_real_compositions(self) -> None:
        planner = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2)
        )
        base = planning_constraints(max_distance_km=30, time_end="22:00")

        dessert = planner.plan(
            base.model_copy(update={"preferences": ["甜品"]})
        )
        quiet = planner.plan(
            base.model_copy(update={"preferences": ["安静"]})
        )

        dessert_ids = [stop.resource_id for stop in dessert.plans[0].stops]
        quiet_ids = [stop.resource_id for stop in quiet.plans[0].stops]
        self.assertNotEqual(dessert_ids, quiet_ids)
        self.assertIn("匹配偏好：甜品", dessert.plans[0].highlights)
        self.assertIn("匹配偏好：安静", quiet.plans[0].highlights)

    def test_too_short_requested_duration_is_reported_as_the_conflict(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "小展", ["展览"]),
                candidate("restaurant", ResourceType.RESTAURANT, "简餐", ["简餐"]),
            ]
        )
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "duration_minutes": ConstraintValue[int](
                    value=90,
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )

        result = PlanningService(catalog=catalog).plan(constraints)

        self.assertEqual(result.plans, [])
        self.assertIsNotNone(result.conflict)
        self.assertIn("duration_minutes", result.conflict.fields)
        self.assertTrue(any("时长" in item for item in result.conflict.relaxation_options))

    def test_strict_budget_does_not_hide_a_duration_conflict(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "小展", ["展览"]),
                candidate("restaurant", ResourceType.RESTAURANT, "简餐", ["简餐"]),
            ]
        )
        constraints = planning_constraints(
            budget=200,
            strict_budget=True,
            time_end="22:00",
        ).model_copy(
            update={
                "duration_minutes": ConstraintValue[int](
                    value=90,
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )

        result = PlanningService(catalog=catalog).plan(constraints)

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.code, "NO_FEASIBLE_PLAN")
        self.assertNotIn("双站", result.conflict.message)
        self.assertEqual(result.conflict.fields, ["duration_minutes"])

    def test_unrelated_budget_violation_does_not_hide_a_weather_conflict(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "outdoor",
                    ResourceType.ACTIVITY,
                    "雨天不可用的公园",
                    ["公园"],
                    weather_sensitive=True,
                ),
                candidate(
                    "expensive",
                    ResourceType.RESTAURANT,
                    "高价餐厅",
                    ["餐厅"],
                    avg_price=200,
                ),
            ]
        )
        result = PlanningService(
            weather_provider=RainyWeatherProvider(),
            catalog=catalog,
        ).plan(
            planning_constraints(budget=100, strict_budget=True, time_end="20:00")
        )

        self.assertEqual(result.conflict.code, "NO_FEASIBLE_PLAN")
        self.assertNotIn("双站", result.conflict.message)
        self.assertIn("weather", result.conflict.fields)
        self.assertNotIn("budget_per_person", result.conflict.fields)

    def test_duplicate_catalog_rows_do_not_masquerade_as_plan_diversity(self) -> None:
        activity = candidate(
            "activity",
            ResourceType.ACTIVITY,
            "城市展览",
            ["展览"],
        )
        catalog = InMemoryCatalog(
            [
                activity,
                activity.model_copy(deep=True),
                candidate("restaurant", ResourceType.RESTAURANT, "附近简餐", ["简餐"]),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(time_end="20:00"))

        compositions = [
            tuple(stop.resource_id for stop in plan.stops)
            for plan in result.plans
        ]
        self.assertEqual(len(compositions), len(set(compositions)))

    def test_optional_distance_limit_does_not_crash_route_verification(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                candidate("restaurant", ResourceType.RESTAURANT, "附近简餐", ["简餐"]),
            ]
        )
        constraints = planning_constraints(time_end="20:00").model_copy(
            update={"max_distance_km": None}
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=50),
        ).plan(constraints)

        self.assertTrue(result.plans)
        self.assertIsNone(result.conflict)

    def _full_day_constraints(self) -> NormalizedConstraints:
        return planning_constraints(budget=1_000, time_end="20:00").model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="11:00", end="20:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

    def test_meal_roles_anchor_arrival_to_meal_windows(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "lunch",
                    ResourceType.RESTAURANT,
                    "午餐馆",
                    ["午餐"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-14:00"},
                ),
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=300,
                    open_hours={"sat": "11:00-20:00"},
                ),
                candidate(
                    "dinner",
                    ResourceType.RESTAURANT,
                    "晚餐馆",
                    ["晚餐"],
                    duration_minutes=60,
                    open_hours={"sat": "17:00-21:00"},
                ),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(self._full_day_constraints())

        self.assertTrue(any(len(plan.stops) == 3 for plan in result.plans))
        for plan in result.plans:
            for stop in plan.stops:
                if stop.role == StopRole.LUNCH:
                    self.assertTrue(
                        "11:00" <= stop.start <= "14:00",
                        f"lunch off-anchor at {stop.start}",
                    )
                elif stop.role == StopRole.DINNER:
                    self.assertTrue(
                        "17:00" <= stop.start <= "20:30",
                        f"dinner off-anchor at {stop.start}",
                    )

    def test_off_anchor_dinner_plan_is_not_returned(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "lunch",
                    ResourceType.RESTAURANT,
                    "午餐馆",
                    ["午餐"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-14:00"},
                ),
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "短活动",
                    ["展览"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-20:00"},
                ),
                candidate(
                    "dinner",
                    ResourceType.RESTAURANT,
                    "晚餐馆",
                    ["晚餐"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-21:00"},
                ),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(self._full_day_constraints())

        self.assertTrue(result.plans)
        self.assertNotIn(
            "lunch-activity-dinner-v1",
            {plan.skeleton_id for plan in result.plans},
        )

    def test_return_by_appends_return_leg_and_counts_it(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "activity",
                    ResourceType.ACTIVITY,
                    "城市展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "14:00-20:00"},
                ),
                candidate(
                    "restaurant",
                    ResourceType.RESTAURANT,
                    "附近简餐",
                    ["简餐"],
                    duration_minutes=60,
                    open_hours={"sat": "14:00-20:00"},
                ),
            ]
        )
        constraints = planning_constraints(time_end="20:00").model_copy(
            update={
                "return_by": ConstraintValue[str](
                    value="20:00",
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
        ).plan(constraints)

        self.assertTrue(result.plans)
        first = result.plans[0]
        self.assertEqual(first.route_legs[-1].destination_name, "出发地")
        self.assertEqual(len(first.route_legs), len(first.stops) + 1)
        self.assertEqual(
            first.total_duration_minutes,
            sum(stop.duration_minutes for stop in first.stops)
            + sum(leg.duration_minutes for leg in first.route_legs),
        )

    def test_return_by_keeps_route_verification_within_the_leg_budget(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                *[
                    candidate(
                        f"restaurant-{index:02d}",
                        ResourceType.RESTAURANT,
                        f"餐厅 {index:02d}",
                        ["餐厅"],
                    )
                    for index in range(20)
                ],
            ]
        )
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "return_by": ConstraintValue[str](
                    value="23:00",
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )
        route_provider = FixedReplayRouteProvider(duration_minutes=5, distance_km=1)

        result = PlanningService(
            catalog=catalog,
            route_provider=route_provider,
        ).plan(constraints)

        self.assertTrue(result.plans)
        self.assertLessEqual(len(route_provider.requests), 24)

    def test_return_by_violation_reports_conflict(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                candidate("restaurant", ResourceType.RESTAURANT, "附近简餐", ["简餐"]),
            ]
        )
        constraints = planning_constraints(time_end="20:00").model_copy(
            update={
                "return_by": ConstraintValue[str](
                    value="15:00",
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=30, distance_km=2),
        ).plan(constraints)

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.code, "NO_FEASIBLE_PLAN")
        self.assertIn("return_by", result.conflict.fields)

    def test_return_verifier_requires_a_real_leg_back_to_origin_and_honors_boundary(self) -> None:
        resources = [
            candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
            candidate("restaurant", ResourceType.RESTAURANT, "附近简餐", ["简餐"]),
        ]
        constraints = planning_constraints(time_end="20:00").model_copy(
            update={
                "return_by": ConstraintValue[str](
                    value="20:00",
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )
        plan = PlanningService(
            catalog=InMemoryCatalog(resources),
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
        ).plan(constraints).plans[0]
        verifier = PlanVerifier()
        resource_by_id = {item.resource_id: item for item in resources}

        missing_leg = plan.model_copy(update={"route_legs": plan.route_legs[:-1]})
        wrong_destination = plan.model_copy(
            update={
                "route_legs": [
                    *plan.route_legs[:-1],
                    plan.route_legs[-1].model_copy(update={"destination_name": "另一处"}),
                ]
            }
        )
        exactly_on_deadline = plan.model_copy(
            update={
                "route_legs": [
                    *plan.route_legs[:-1],
                    plan.route_legs[-1].model_copy(update={"end": "20:00"}),
                ]
            }
        )
        late_return = plan.model_copy(
            update={
                "route_legs": [
                    *plan.route_legs[:-1],
                    plan.route_legs[-1].model_copy(update={"end": "20:01"}),
                ]
            }
        )

        self.assertIn(
            "return_route_missing",
            {item.code for item in verifier.verify(missing_leg, constraints, resource_by_id).violations},
        )
        self.assertIn(
            "return_route_missing",
            {item.code for item in verifier.verify(wrong_destination, constraints, resource_by_id).violations},
        )
        self.assertTrue(verifier.verify(exactly_on_deadline, constraints, resource_by_id).is_feasible)
        self.assertIn(
            "return_after_deadline",
            {item.code for item in verifier.verify(late_return, constraints, resource_by_id).violations},
        )

    def test_unknown_price_is_not_ranked_as_a_free_low_cost_plan(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"], avg_price=20),
                candidate("known", ResourceType.RESTAURANT, "明确价格餐厅", ["餐厅"], avg_price=10),
                candidate(
                    "unknown",
                    ResourceType.RESTAURANT,
                    "价格待确认餐厅",
                    ["餐厅"],
                    avg_price=None,
                    price_kind=PriceKind.UNKNOWN,
                ),
            ]
        )
        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(budget=1_000, time_end="20:00"))

        incomplete = next(plan for plan in result.plans if plan.price_status.value == "incomplete")
        strategy = next(item for item in incomplete.score_breakdown if item.dimension == "strategy")
        self.assertNotEqual(incomplete.strategy.value, "low_cost")
        self.assertIn("price_status=incomplete", strategy.evidence)
        self.assertIn("cost=uncomparable", strategy.evidence)

    def test_strategy_policies_reorder_verified_candidates_by_real_metrics(self) -> None:
        """每种策略必须对可观察指标作出不同选择，而不是只暴露枚举值。"""
        base_constraints = planning_constraints(budget=1_000, time_end="20:00")
        base = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(base_constraints).plans[0]
        weather = RainyWeatherProvider().get_weather(
            WeatherRequest(
                city="北京市",
                district="朝阳区",
                adcode="110105",
                date=base_constraints.date.value,
            )
        )

        def variant(
            plan_id: str,
            *,
            price: int,
            distance: float,
            resource_id: str,
            preference_points: float = 0.0,
            base_score: float = 10.0,
        ):
            contribution = (
                [
                    ScoreContribution(
                        rule_id="test.preference.v1",
                        dimension="preference",
                        points=preference_points,
                        message="test preference",
                    )
                ]
                if preference_points
                else []
            )
            return base.model_copy(
                update={
                    "plan_id": plan_id,
                    "composition_fingerprint": plan_id,
                    "total_price": price,
                    "price_status": PlanPriceStatus.KNOWN,
                    "total_score": base_score,
                    "stops": [
                        base.stops[0].model_copy(update={"resource_id": resource_id}),
                        base.stops[1].model_copy(update={"resource_id": f"{resource_id}-meal"}),
                    ],
                    "route_legs": [
                        leg.model_copy(update={"distance_km": distance})
                        for leg in base.route_legs
                    ],
                    "score_breakdown": contribution,
                }
            )

        cheap = variant("cheap", price=10, distance=9, resource_id="plain")
        close = variant("close", price=100, distance=1, resource_id="plain", base_score=5.0)
        cost_and_travel = _apply_dynamic_strategies(
            [cheap, close], base_constraints, weather.model_copy(update={"is_adverse": False}), {}
        )
        by_id = {plan.plan_id: plan for plan in cost_and_travel}
        self.assertEqual(by_id["cheap"].strategy.value, "low_cost")
        self.assertEqual(by_id["close"].strategy.value, "low_travel")

        experience_constraints = base_constraints.model_copy(update={"preferences": ["适合拍照"]})
        ordinary = variant("ordinary", price=10, distance=1, resource_id="plain")
        rich = variant(
            "rich",
            price=100,
            distance=9,
            resource_id="experience",
            preference_points=10.0,
        )
        experience = _apply_dynamic_strategies(
            [ordinary, rich], experience_constraints, weather.model_copy(update={"is_adverse": False}), {}
        )
        self.assertEqual({plan.plan_id: plan.strategy.value for plan in experience}["rich"], "experience")

        family_constraints = base_constraints.model_copy(
            update={
                "party": ConstraintValue(
                    value=PartyProfile(adults=1, children=1, child_age=6),
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )
        family = variant("family", price=100, distance=9, resource_id="family")
        plain = variant("plain", price=10, distance=1, resource_id="plain")
        family_candidates = {
            "family": candidate("family", ResourceType.ACTIVITY, "亲子馆", ["亲子"]),
            "plain": candidate("plain", ResourceType.ACTIVITY, "普通馆", ["展览"]),
        }
        family_result = _apply_dynamic_strategies(
            [plain, family], family_constraints, weather.model_copy(update={"is_adverse": False}), family_candidates
        )
        self.assertEqual({plan.plan_id: plan.strategy.value for plan in family_result}["family"], "family_safe")

        indoor = variant("indoor", price=100, distance=9, resource_id="indoor")
        outdoor = variant("outdoor", price=10, distance=1, resource_id="outdoor")
        weather_candidates = {
            "indoor": candidate("indoor", ResourceType.ACTIVITY, "室内馆", ["展览"], weather_sensitive=False),
            "outdoor": candidate("outdoor", ResourceType.ACTIVITY, "户外公园", ["公园"], weather_sensitive=True),
        }
        weather_result = _apply_dynamic_strategies(
            [outdoor, indoor], base_constraints, weather, weather_candidates
        )
        self.assertEqual({plan.plan_id: plan.strategy.value for plan in weather_result}["indoor"], "weather_safe")

    def test_total_distance_constraint_rejects_a_plan(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"]),
                candidate("restaurant", ResourceType.RESTAURANT, "附近简餐", ["简餐"]),
            ]
        )
        constraints = planning_constraints(max_distance_km=30, time_end="20:00").model_copy(
            update={
                "total_distance_km": ConstraintValue[float](
                    value=5.0,
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=10),
        ).plan(constraints)

        self.assertEqual(result.plans, [])
        self.assertEqual(result.conflict.code, "NO_PLAN_AFTER_ROUTE_VERIFICATION")
        self.assertIn("total_distance_km", result.conflict.fields)

    def test_diversification_keeps_a_price_diverse_plan_over_a_similar_cheap_one(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("activity", ResourceType.ACTIVITY, "城市展览", ["展览"], avg_price=30),
                candidate("cheap-1", ResourceType.RESTAURANT, "平价餐厅一", ["餐厅"], avg_price=50),
                candidate("cheap-2", ResourceType.RESTAURANT, "平价餐厅二", ["餐厅"], avg_price=50),
                candidate("cheap-3", ResourceType.RESTAURANT, "平价餐厅三", ["餐厅"], avg_price=50),
                candidate("fancy", ResourceType.RESTAURANT, "精致餐厅", ["餐厅"], avg_price=150),
            ]
        )

        result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(budget=120, time_end="20:00"))

        self.assertEqual(len(result.plans), 3)
        restaurant_ids = {plan.stops[1].resource_id for plan in result.plans}
        self.assertIn("fancy", restaurant_ids)

    def test_diversification_does_not_return_a_small_set_of_near_duplicates(self) -> None:
        base = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(planning_constraints(time_end="20:00")).plans[0]
        shared_stops = [
            *base.stops,
            base.stops[0].model_copy(update={"resource_id": "shared-third"}),
            base.stops[1].model_copy(update={"resource_id": "shared-fourth"}),
        ]
        first = base.model_copy(
            update={"plan_id": "first", "composition_fingerprint": "first", "stops": shared_stops}
        )
        second = first.model_copy(
            update={
                "plan_id": "second",
                "composition_fingerprint": "second",
                "stops": [
                    *shared_stops[:3],
                    shared_stops[3].model_copy(update={"resource_id": "different-fourth"}),
                ],
            }
        )
        third = second.model_copy(
            update={"plan_id": "third", "composition_fingerprint": "third"}
        )

        selected = _diversify_plans([first, second, third], max_count=3)

        self.assertEqual([plan.plan_id for plan in selected], ["first"])


if __name__ == "__main__":
    unittest.main()

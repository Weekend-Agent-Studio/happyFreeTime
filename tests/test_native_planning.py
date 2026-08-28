import unittest
from datetime import datetime, timezone

from app.domain.catalog import (
    CatalogSource,
    PriceKind,
    ResourceType,
    StopCandidate,
    VerificationStatus,
)
from app.domain.constraints import ConstraintSource, ConstraintValue, TimeWindow
from app.domain.providers import (
    GeoPoint,
    ProviderMode,
    ProviderSource,
    WeatherFact,
    WeatherRequest,
)
from app.services.catalog import InMemoryCatalog
from app.services.planning import PlanningService
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
        price_kind=PriceKind.KNOWN,
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


if __name__ == "__main__":
    unittest.main()

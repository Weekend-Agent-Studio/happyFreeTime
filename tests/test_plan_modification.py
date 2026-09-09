import unittest

from app.domain.catalog import ResourceType
from app.domain.constraints import (
    CommandOperation,
    ConstraintPatch,
    ConstraintSource,
    ConstraintValue,
    ConversationCommand,
    RouteObjective,
    SemanticCriterion,
    StopRole,
    TargetReference,
    TimeWindow,
)
from app.domain.planning import LockedStop
from app.domain.providers import AvailabilityStatus, GeoPoint
from app.providers.availability import MockAvailabilityProvider
from app.services.catalog import InMemoryCatalog
from app.services.planning import PlanningService
from tests.test_native_planning import candidate
from tests.test_planning import FixedReplayRouteProvider, planning_constraints


class PlanModificationTest(unittest.TestCase):
    @staticmethod
    def _command() -> ConversationCommand:
        return ConversationCommand(
            operation=CommandOperation.REPLACE,
            target=TargetReference(role=StopRole.ACTIVITY, raw_text="活动"),
            locked_targets=(
                TargetReference(
                    resource_type=ResourceType.RESTAURANT,
                    raw_text="餐厅",
                ),
            ),
            constraint_patch=ConstraintPatch(prefer_shorter_travel=True),
            evidence={"replace": "活动换近一点", "keep": "餐厅保留"},
        )

    def test_replace_activity_keeps_restaurant_and_shortens_verified_route(self) -> None:
        restaurant = candidate(
            "restaurant-kept",
            ResourceType.RESTAURANT,
            "保留餐厅",
            ["餐厅"],
        ).model_copy(update={"location": GeoPoint(latitude=39.9300, longitude=116.4500)})
        near_activity = candidate(
            "activity-near",
            ResourceType.ACTIVITY,
            "近处展览",
            ["展览"],
        ).model_copy(update={"location": GeoPoint(latitude=39.9250, longitude=116.4470)})
        far_activity = candidate(
            "activity-far",
            ResourceType.ACTIVITY,
            "远处展览",
            ["展览"],
        ).model_copy(update={"location": GeoPoint(latitude=39.9400, longitude=116.4600)})
        constraints = planning_constraints(
            budget=1_000,
            max_distance_km=30,
            time_end="20:00",
        )
        service = PlanningService(
            catalog=InMemoryCatalog([restaurant, near_activity, far_activity])
        )
        initial = service.plan(constraints)
        selected = next(
            plan
            for plan in initial.plans
            if any(stop.resource_id == "activity-far" for stop in plan.stops)
        )
        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=self._command(),
        )

        self.assertIsNone(result.question)
        self.assertIsNone(result.candidate_set.conflict)
        self.assertEqual(len(result.candidate_set.plans), 1)
        modified = result.candidate_set.plans[0]
        self.assertEqual(
            {stop.resource_id for stop in modified.stops},
            {"activity-near", "restaurant-kept"},
        )
        self.assertEqual(
            next(stop.resource_id for stop in modified.stops if stop.type == "restaurant"),
            "restaurant-kept",
        )
        self.assertLess(
            sum(leg.distance_km for leg in modified.route_legs),
            sum(leg.distance_km for leg in selected.route_legs),
        )
        self.assertEqual(result.plan_diff.base_plan_id, selected.plan_id)
        self.assertEqual(result.plan_diff.new_plan_id, modified.plan_id)
        self.assertEqual(result.plan_diff.locked_stops[0].resource_id, "restaurant-kept")
        self.assertEqual(result.plan_diff.replacements[0].before_resource_id, "activity-far")
        self.assertEqual(result.plan_diff.replacements[0].after_resource_id, "activity-near")

    def test_ambiguous_activity_reference_asks_instead_of_guessing(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("morning-activity", ResourceType.ACTIVITY, "上午展览", ["展览"], duration_minutes=120, open_hours={"sat": "09:00-12:00"}),
                candidate("lunch", ResourceType.RESTAURANT, "午餐", ["午餐"], duration_minutes=60, open_hours={"sat": "11:00-14:00"}),
                candidate("afternoon-activity", ResourceType.ACTIVITY, "下午展览", ["展览"], duration_minutes=300, open_hours={"sat": "12:00-18:00"}),
                candidate("dinner", ResourceType.RESTAURANT, "晚餐", ["晚餐"], duration_minutes=60, open_hours={"sat": "17:00-21:00"}),
            ]
        )
        constraints = planning_constraints(budget=1_000, max_distance_km=30, time_end="21:00").model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="09:00", end="21:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )
        service = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )
        generated = service.plan(constraints).plans
        rich_plans = [plan for plan in generated if len(plan.stops) == 4]
        self.assertTrue(
            rich_plans,
            [(plan.skeleton_id, [stop.resource_id for stop in plan.stops]) for plan in generated],
        )
        selected = rich_plans[0]

        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=self._command(),
        )

        self.assertIsNotNone(result.question)
        self.assertEqual(result.question.field, "target_reference")
        self.assertIsNone(result.candidate_set)

    def test_resource_id_outside_selected_plan_is_not_authorized(self) -> None:
        restaurant = candidate(
            "restaurant-kept",
            ResourceType.RESTAURANT,
            "保留餐厅",
            ["餐厅"],
        )
        old_activity = candidate(
            "activity-old",
            ResourceType.ACTIVITY,
            "原活动",
            ["展览"],
        )
        constraints = planning_constraints(
            budget=1_000,
            max_distance_km=30,
            time_end="20:00",
        )
        service = PlanningService(
            catalog=InMemoryCatalog([restaurant, old_activity])
        )
        selected = service.plan(constraints).plans[0]
        guessed_command = self._command().model_copy(
            update={
                "target": TargetReference(
                    resource_id="activity-not-in-selected-plan",
                    raw_text="模型猜测的活动",
                )
            }
        )

        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=guessed_command,
        )

        self.assertIsNotNone(result.question)
        self.assertEqual(result.question.field, "target_reference")
        self.assertIsNone(result.candidate_set)

    def test_four_stop_replaces_only_the_explicit_second_activity(self) -> None:
        activity_one = candidate(
            "activity-one",
            ResourceType.ACTIVITY,
            "首站展览",
            ["展览"],
            duration_minutes=60,
            open_hours={"sat": "09:00-18:00"},
        )
        lunch = candidate(
            "lunch-fixed",
            ResourceType.RESTAURANT,
            "固定午餐",
            ["午餐"],
            duration_minutes=60,
            open_hours={"sat": "11:00-14:00"},
        )
        activity_two = candidate(
            "activity-two",
            ResourceType.ACTIVITY,
            "下午展览",
            ["展览"],
            duration_minutes=300,
            open_hours={"sat": "12:00-18:00"},
        )
        dinner = candidate(
            "dinner-fixed",
            ResourceType.RESTAURANT,
            "固定晚餐",
            ["晚餐"],
            duration_minutes=60,
            open_hours={"sat": "17:00-21:00"},
        )
        replacement = candidate(
            "activity-replacement",
            ResourceType.ACTIVITY,
            "替换展览",
            ["展览"],
            duration_minutes=300,
            open_hours={"sat": "12:00-18:00"},
        )
        constraints = planning_constraints(
            budget=1_000,
            max_distance_km=30,
            time_end="21:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="10:00", end="21:00"),
                    source=ConstraintSource.USER_INFERRED,
                )
            }
        )
        service = PlanningService(
            catalog=InMemoryCatalog(
                [activity_one, lunch, activity_two, dinner, replacement]
            ),
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )
        generated = service.plan(constraints)
        selected = next(
            plan
            for plan in generated.plans
            if len(plan.stops) == 4
            and plan.stops[2].resource_id == "activity-two"
        )
        command = ConversationCommand(
            operation=CommandOperation.REPLACE,
            target=TargetReference(
                stop_index=2,
                resource_id="activity-two",
                role=StopRole.ACTIVITY,
                raw_text="下午展览",
            ),
            replacement_criteria=(),
        )

        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=command,
        )

        self.assertIsNone(result.question)
        self.assertEqual(len(result.candidate_set.plans), 1)
        modified = result.candidate_set.plans[0]
        self.assertEqual(len(modified.stops), len(selected.stops))
        self.assertEqual(modified.skeleton_id, selected.skeleton_id)
        self.assertNotEqual(modified.stops[2].resource_id, selected.stops[2].resource_id)
        self.assertEqual(modified.stops[2].role, selected.stops[2].role)
        for index in (0, 1, 3):
            self.assertEqual(
                (modified.stops[index].resource_id, modified.stops[index].role),
                (selected.stops[index].resource_id, selected.stops[index].role),
            )
        self.assertEqual(
            result.plan_diffs[0].locked_stops,
            tuple(
                LockedStop(
                    source_plan_id=selected.plan_id,
                    stop_index=index,
                    resource_id=selected.stops[index].resource_id,
                    role=selected.stops[index].role,
                )
                for index in (0, 1, 3)
            ),
        )

    def test_unavailable_locked_restaurant_is_not_silently_released(self) -> None:
        restaurant = candidate("restaurant-kept", ResourceType.RESTAURANT, "保留餐厅", ["餐厅"])
        old_activity = candidate("activity-old", ResourceType.ACTIVITY, "原活动", ["展览"])
        constraints = planning_constraints(budget=1_000, max_distance_km=30, time_end="20:00")
        selected = PlanningService(
            catalog=InMemoryCatalog([restaurant, old_activity])
        ).plan(constraints).plans[0]
        service = PlanningService(
            catalog=InMemoryCatalog(
                [candidate("activity-new", ResourceType.ACTIVITY, "新活动", ["展览"])]
            )
        )

        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=self._command(),
        )

        self.assertEqual(result.candidate_set.plans, [])
        self.assertEqual(result.candidate_set.conflict.code, "LOCKED_STOP_UNAVAILABLE")
        self.assertIsNone(result.plan_diff)

    def test_replacement_returns_two_verified_candidates_with_candidate_diffs(self) -> None:
        restaurant = candidate(
            "restaurant-fixed",
            ResourceType.RESTAURANT,
            "固定晚餐",
            ["餐厅"],
        )
        old_activity = candidate(
            "activity-old",
            ResourceType.ACTIVITY,
            "旧展览",
            ["展览"],
        )
        quiet_activity = candidate(
            "activity-quiet",
            ResourceType.ACTIVITY,
            "安静展览",
            ["展览"],
            scene_tags=["安静"],
        )
        park_activity = candidate(
            "activity-park",
            ResourceType.ACTIVITY,
            "安静公园",
            ["公园"],
            scene_tags=["安静"],
        )
        constraints = planning_constraints(
            budget=1_000,
            max_distance_km=30,
            time_end="20:00",
        )
        service = PlanningService(
            catalog=InMemoryCatalog(
                [restaurant, old_activity, quiet_activity, park_activity]
            ),
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        )
        selected = next(
            plan
            for plan in service.plan(constraints).plans
            if any(stop.resource_id == "activity-old" for stop in plan.stops)
        )
        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=ConversationCommand(
                operation=CommandOperation.REPLACE,
                target=TargetReference(
                    stop_index=0,
                    resource_id="activity-old",
                    role=StopRole.ACTIVITY,
                    raw_text="旧展览",
                ),
                replacement_criteria=(
                    SemanticCriterion(text="安静"),
                ),
            ),
        )

        self.assertIsNone(result.question)
        self.assertIsNotNone(result.candidate_set)
        self.assertEqual(len(result.candidate_set.plans), 2)
        self.assertEqual(len(result.plan_diffs), 2)
        self.assertEqual(
            {diff.new_plan_id for diff in result.plan_diffs},
            {plan.plan_id for plan in result.candidate_set.plans},
        )
        for plan, diff in zip(result.candidate_set.plans, result.plan_diffs, strict=True):
            self.assertEqual(diff.new_plan_id, plan.plan_id)
            self.assertEqual(len(diff.replacements), 1)
            self.assertEqual(
                [(stop.resource_id, stop.role) for stop in plan.stops[1:]],
                [(stop.resource_id, stop.role) for stop in selected.stops[1:]],
            )
            self.assertTrue(diff.locked_stops)

    def test_replacement_never_verifies_candidates_without_availability_facts(self) -> None:
        restaurant = candidate(
            "restaurant-fixed",
            ResourceType.RESTAURANT,
            "固定晚餐",
            ["餐厅"],
        )
        old_activity = candidate(
            "activity-old",
            ResourceType.ACTIVITY,
            "原活动",
            ["展览"],
        )
        replacements = [
            candidate(
                f"activity-new-{index}",
                ResourceType.ACTIVITY,
                f"候选活动 {index}",
                ["展览"],
            )
            for index in range(8)
        ]
        constraints = planning_constraints(
            budget=1_000,
            max_distance_km=30,
            time_end="20:00",
        )
        selected = PlanningService(
            catalog=InMemoryCatalog([restaurant, old_activity]),
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
        ).plan(constraints).plans[0]

        class RecordingAvailabilityProvider(MockAvailabilityProvider):
            def __init__(self) -> None:
                super().__init__(
                    statuses={},
                    default_status=AvailabilityStatus.AVAILABLE,
                )
                self.requests = []

            def check(self, request):
                self.requests.append(request)
                return super().check(request)

        availability = RecordingAvailabilityProvider()
        service = PlanningService(
            catalog=InMemoryCatalog([restaurant, old_activity, *replacements]),
            route_provider=FixedReplayRouteProvider(duration_minutes=5, distance_km=1),
            availability_provider=availability,
        )
        result = service.modify_selected_plan(
            selected_plan=selected,
            constraints=constraints,
            command=ConversationCommand(
                operation=CommandOperation.REPLACE,
                target=TargetReference(
                    stop_index=0,
                    resource_id="activity-old",
                    role=StopRole.ACTIVITY,
                    raw_text="原活动",
                ),
            ),
        )

        self.assertIsNone(result.question)
        self.assertTrue(result.candidate_set.plans)
        self.assertLessEqual(len(availability.requests), 6)
        checked_ids = {
            check.resource_id
            for request in availability.requests
            for check in request.checks
        }
        returned_replacements = {
            plan.stops[0].resource_id for plan in result.candidate_set.plans
        }
        self.assertTrue(returned_replacements)
        self.assertTrue(returned_replacements <= checked_ids)


if __name__ == "__main__":
    unittest.main()

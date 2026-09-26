import unittest

from app.domain.catalog import ResourceType
from app.domain.constraints import StopRole
from app.domain.providers import GeoPoint
from app.services.itinerary_search import (
    BeamSearchConfig,
    SearchState,
    _state_rank,
    bounded_beam_search,
)
from tests.test_native_planning import candidate
from tests.test_planning import planning_constraints


class BoundedItinerarySearchTest(unittest.TestCase):
    def test_time_overflow_rank_uses_elapsed_duration(self) -> None:
        """An afternoon wall-clock value must not be compared to a duration budget."""

        state = SearchState(
            selected_candidates=(),
            next_slot=1,
            current_location=GeoPoint(latitude=39.9219, longitude=116.4436),
            estimated_time=17 * 60,
            accumulated_cost=0,
            semantic_score=0,
            estimated_distance=0,
        )

        rank = _state_rank(state, maximum_minutes=7 * 60, start_minutes=14 * 60)

        self.assertEqual(rank[1:3], (0, 0))

    def test_search_is_bounded_without_materializing_cartesian_product(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"max_distance_km": None}
        )
        activities = [
            candidate(
                f"activity-{index}",
                ResourceType.ACTIVITY,
                f"活动 {index}",
                ["activity"],
            )
            for index in range(20)
        ]
        dinners = [
            candidate(
                f"dinner-{index}",
                ResourceType.RESTAURANT,
                f"晚餐 {index}",
                ["restaurant"],
            )
            for index in range(20)
        ]
        result = bounded_beam_search(
            role_pools=(activities, dinners, dinners),
            roles=(StopRole.ACTIVITY, StopRole.DINNER, StopRole.DINNER),
            constraints=constraints,
            origin=GeoPoint(latitude=39.9219, longitude=116.4436),
            config=BeamSearchConfig(
                beam_width=4,
                max_expansions=40,
                max_finalists=5,
            ),
        )

        self.assertLessEqual(result.stats.expansions, 40)
        self.assertLessEqual(result.stats.finalists, 5)
        self.assertTrue(all(len(sequence) == 3 for sequence in result.sequences))
        self.assertTrue(
            all(
                len({item.resource_id for item in sequence}) == len(sequence)
                for sequence in result.sequences
            )
        )

    def test_four_slot_trace_reports_theoretical_product_and_bound(self) -> None:
        constraints = planning_constraints(time_end="23:00").model_copy(
            update={"max_distance_km": None}
        )
        activities = [
            candidate(
                f"activity-{index}",
                ResourceType.ACTIVITY,
                f"活动 {index}",
                ["activity"],
            )
            for index in range(10)
        ]
        breaks = [
            candidate(
                f"break-{index}",
                ResourceType.CAFE,
                f"休息 {index}",
                ["cafe"],
            )
            for index in range(10)
        ]
        dinners = [
            candidate(
                f"dinner-{index}",
                ResourceType.RESTAURANT,
                f"晚餐 {index}",
                ["restaurant"],
            )
            for index in range(10)
        ]

        result = bounded_beam_search(
            role_pools=(activities, breaks, activities, dinners),
            roles=(StopRole.ACTIVITY, StopRole.BREAK, StopRole.ACTIVITY, StopRole.DINNER),
            constraints=constraints,
            origin=GeoPoint(latitude=39.9219, longitude=116.4436),
            config=BeamSearchConfig(beam_width=8, max_expansions=80, max_finalists=6),
        )

        self.assertEqual(result.stats.theoretical_combinations, 10_000)
        self.assertLessEqual(result.stats.expansions, 80)
        self.assertLessEqual(result.stats.finalists, 6)

    def test_route_estimate_breaks_equal_semantic_ties(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"max_distance_km": None}
        )
        near = candidate(
            "near-activity",
            ResourceType.ACTIVITY,
            "活动 A",
            ["activity"],
        )
        far = candidate(
            "far-activity",
            ResourceType.ACTIVITY,
            "活动 B",
            ["activity"],
        ).model_copy(
            update={"location": GeoPoint(latitude=40.2, longitude=116.9)}
        )
        dinner = candidate(
            "dinner",
            ResourceType.RESTAURANT,
            "晚餐",
            ["restaurant"],
        )

        result = bounded_beam_search(
            role_pools=((near, far), (dinner,)),
            roles=(StopRole.ACTIVITY, StopRole.DINNER),
            constraints=constraints,
            origin=GeoPoint(latitude=39.9219, longitude=116.4436),
            semantic_scores={
                "near-activity": 1.0,
                "far-activity": 1.0,
                "dinner": 1.0,
            },
        )

        self.assertEqual(result.sequences[0][0].resource_id, "near-activity")

    def test_known_opening_mismatch_is_softly_ranked_after_open_candidate(self) -> None:
        constraints = planning_constraints(time_end="18:00")
        opens_late = candidate(
            "opens-late",
            ResourceType.ACTIVITY,
            "晚开的活动",
            ["activity"],
            open_hours={"sat": "15:00-18:00"},
        )
        opens_on_time = candidate(
            "opens-on-time",
            ResourceType.ACTIVITY,
            "按时开放的活动",
            ["activity"],
            open_hours={"sat": "14:00-18:00"},
        )
        dinner = candidate(
            "dinner",
            ResourceType.RESTAURANT,
            "晚餐",
            ["restaurant"],
            open_hours={"sat": "17:00-22:00"},
        )

        result = bounded_beam_search(
            role_pools=((opens_late, opens_on_time), (dinner,)),
            roles=(StopRole.ACTIVITY, StopRole.DINNER),
            constraints=constraints,
            origin=GeoPoint(latitude=39.9219, longitude=116.4436),
            semantic_scores={
                "opens-late": 1.0,
                "opens-on-time": 0.1,
                "dinner": 1.0,
            },
        )

        self.assertEqual(result.sequences[0][0].resource_id, "opens-on-time")

    def test_search_prunes_duplicate_and_distance_candidates(self) -> None:
        constraints = planning_constraints(max_distance_km=1.0, time_end="22:00")
        near = candidate(
            "near",
            ResourceType.ACTIVITY,
            "近处活动",
            ["activity"],
        )
        far = candidate(
            "far",
            ResourceType.ACTIVITY,
            "远处活动",
            ["activity"],
        ).model_copy(
            update={"location": GeoPoint(latitude=40.2, longitude=116.9)}
        )
        dinner = candidate(
            "dinner",
            ResourceType.RESTAURANT,
            "晚餐",
            ["restaurant"],
        )
        result = bounded_beam_search(
            role_pools=((near, far), (near, dinner)),
            roles=(StopRole.ACTIVITY, StopRole.DINNER),
            constraints=constraints,
            origin=GeoPoint(latitude=39.9219, longitude=116.4436),
        )

        self.assertIn("duplicate_resource", result.rejected_fields)
        self.assertIn("max_distance_km", result.rejected_fields)
        self.assertTrue(all(sequence[0].resource_id == "near" for sequence in result.sequences))

    def test_estimated_route_time_is_not_a_hard_feasibility_prune(self) -> None:
        constraints = planning_constraints(time_end="18:00").model_copy(
            update={"max_distance_km": None}
        )
        far_activity = candidate(
            "far-activity",
            ResourceType.ACTIVITY,
            "远处活动",
            ["activity"],
        ).model_copy(
            update={"location": GeoPoint(latitude=40.8, longitude=116.9)}
        )
        second_activity = candidate(
            "second-activity",
            ResourceType.ACTIVITY,
            "第二个活动",
            ["activity"],
        )

        result = bounded_beam_search(
            role_pools=((far_activity,), (second_activity,)),
            roles=(StopRole.ACTIVITY, StopRole.ACTIVITY),
            constraints=constraints,
            origin=GeoPoint(latitude=39.9219, longitude=116.4436),
        )

        self.assertEqual(
            [item.resource_id for item in result.sequences[0]],
            ["far-activity", "second-activity"],
        )

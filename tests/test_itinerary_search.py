import unittest

from app.domain.catalog import ResourceType
from app.domain.constraints import StopRole
from app.domain.providers import GeoPoint
from app.services.itinerary_search import BeamSearchConfig, bounded_beam_search
from tests.test_native_planning import candidate
from tests.test_planning import planning_constraints


class BoundedItinerarySearchTest(unittest.TestCase):
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


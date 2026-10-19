"""Deterministic DemoWorld quality checks for S-RX3.

These checks are deliberately narrower than the release evaluator.  They
protect the planner/data seam: explicit structure must be preserved, an
all-day request should have at least one broad-coverage candidate, and
semantic evidence must remain grounded in the versioned fixture data.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.domain.constraints import ConstraintSource, ConstraintValue, StopRole, TimeScope
from app.services.catalog import SnapshotCatalog
from app.services.candidate_retriever import RuleBasedCandidateRetriever
from app.services.planning import PlanningService
from app.services.poi_semantic_profiles import load_poi_semantic_profiles
from tests.test_planning import FixedReplayRouteProvider, planning_constraints, with_planning_window


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "data" / "demo_world" / "v1" / "quality_fixtures.json"
PROFILE_PATH = ROOT / "data" / "retrieval" / "v1" / "poi_semantic_profiles.json"


class S_RX3QualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.cases = {
            item["case_id"]: item for item in cls.fixture["cases"]
        }
        cls.catalog = SnapshotCatalog()

    def _request(self, case_id: str):
        case = self.cases[case_id]
        raw = case["request"]
        end = raw.get("end_at", "18:00")
        request = with_planning_window(
            planning_constraints(max_distance_km=30, time_end=end),
            start=raw.get("start_at"),
            end=end,
        )
        updates = {}
        if "exact_stop_count" in raw:
            updates["exact_stop_count"] = ConstraintValue[int](
                value=raw["exact_stop_count"],
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=str(raw["exact_stop_count"]),
            )
        if "required_roles" in raw:
            roles = tuple(StopRole(value) for value in raw["required_roles"])
            updates["required_stop_roles"] = ConstraintValue[tuple[StopRole, ...]](
                value=roles,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text="、".join(raw["required_roles"]),
            )
        if "trip_time_scope" in raw:
            updates["trip_time_scope"] = ConstraintValue[TimeScope](
                value=TimeScope(raw["trip_time_scope"]),
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=raw["trip_time_scope"],
            )
        if "preferences" in raw:
            updates["preferences"] = list(raw["preferences"])
        return request.model_copy(update=updates)

    def _plan(self, case_id: str):
        return PlanningService(
            catalog=self.catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
            candidate_retriever=RuleBasedCandidateRetriever(),
        ).plan(self._request(case_id))

    def test_explicit_single_stop_is_not_padded(self) -> None:
        result = self._plan("explicit_single_activity")
        self.assertTrue(result.plans)
        expected = self.cases["explicit_single_activity"]["expect"]
        for plan in result.plans:
            self.assertEqual(len(plan.stops), expected["exact_stop_count"])
            self.assertEqual(
                [stop.role.value for stop in plan.stops],
                expected["required_role_sequence"],
            )

    def test_explicit_meal_activity_order_is_preserved(self) -> None:
        result = self._plan("explicit_lunch_activity_dinner")
        self.assertTrue(result.plans)
        expected = self.cases["explicit_lunch_activity_dinner"]["expect"]
        for plan in result.plans:
            self.assertEqual(len(plan.stops), expected["exact_stop_count"])
            self.assertEqual(
                [stop.role.value for stop in plan.stops],
                expected["required_role_sequence"],
            )
        self.assertTrue(
            any(trace.skeleton_id == "lunch-activity-dinner-v1" for trace in result.search_traces)
        )

    def test_all_day_top_plan_covers_multiple_periods(self) -> None:
        result = self._plan("all_day_prefers_coverage")
        self.assertTrue(result.plans)
        expected = self.cases["all_day_prefers_coverage"]["expect"]
        top = result.plans[0]
        self.assertGreaterEqual(len(top.stops), expected["minimum_stop_count"])
        self.assertGreaterEqual(
            top.total_duration_minutes,
            expected["minimum_schedule_span_minutes"],
        )
        periods = {
            "morning" if stop.start < "12:00" else
            "afternoon" if stop.start < "17:00" else
            "evening"
            for stop in top.stops
        }
        self.assertTrue(set(expected["required_periods"]).issubset(periods))

    def test_short_window_does_not_pad_required_stops(self) -> None:
        result = self._plan("short_window_does_not_pad_stops")
        self.assertTrue(result.plans)
        expected = self.cases["short_window_does_not_pad_stops"]["expect"]
        for plan in result.plans:
            self.assertEqual(len(plan.stops), expected["exact_stop_count"])
            self.assertEqual(
                [stop.role.value for stop in plan.stops],
                expected["required_role_sequence"],
            )
            self.assertLessEqual(plan.total_duration_minutes, expected["maximum_schedule_span_minutes"])

    def test_quality_fixture_profiles_are_grounded_and_typed(self) -> None:
        profiles = load_poi_semantic_profiles(PROFILE_PATH)
        catalog_ids = {item.resource_id for item in self.catalog.load_candidates()}
        anchor_ids = self.fixture["profile_anchor_ids"]
        expected = self.cases["profile_anchors_are_grounded"]["expect"]

        self.assertGreaterEqual(len(anchor_ids), expected["minimum_profile_anchors"])
        self.assertTrue(set(anchor_ids).issubset(catalog_ids))
        self.assertTrue(set(anchor_ids).issubset(profiles))
        kinds = {
            aspect.kind.value
            for resource_id in anchor_ids
            for aspect in profiles[resource_id].aspects
            if aspect.kind is not None
        }
        self.assertTrue(set(expected["required_profile_kinds"]).issubset(kinds))
        for resource_id in anchor_ids:
            profile = profiles[resource_id]
            self.assertTrue(profile.aspects)
            self.assertTrue(
                all(
                    aspect.source_type == "fixture"
                    and "poi_semantic_profiles.json" in aspect.source_ref
                    for aspect in profile.aspects
                )
            )


if __name__ == "__main__":
    unittest.main()

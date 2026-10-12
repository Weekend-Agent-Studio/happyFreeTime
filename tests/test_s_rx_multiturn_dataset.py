from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.domain.providers import GeocodeRequest, GeocodeResolution
from app.evaluation.resume_release import _EvaluationGeocodingProvider
from evals.run_s_rx_multiturn import (
    _is_publishable_baseline,
    _clarification_resume_succeeded,
    _matches,
    _plan_stats,
    _replacement_diff_invariants,
    _snapshot_delta,
    _step_precondition,
    load_suite,
)
from evals.resume_release_dataset import load_resume_release_dataset


ROOT = Path(__file__).resolve().parents[1]


class SrxMultiturnDatasetTest(unittest.TestCase):
    def test_reviewed_suite_has_expected_case_count_and_unique_ids(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")

        self.assertEqual(suite["review_status"], "reviewed")
        self.assertEqual(len(suite["cases"]), 24)
        self.assertEqual(len({case["case_id"] for case in suite["cases"]}), 24)
        self.assertEqual(suite["evaluation_clock"], "2026-10-11T10:00:00+08:00")
        default_date = next(
            case["expected"]["final"]["date"]
            for case in suite["cases"]
            if case["case_id"] == "missing_date_default_policy"
        )
        self.assertEqual(default_date, "2026-10-17")

    def test_only_full_clean_reviewed_runs_are_publishable_baselines(self) -> None:
        common = {
            "review_status": "reviewed",
            "selected_case_count": 24,
            "suite_case_count": 24,
            "executed_case_count": 24,
            "git_dirty": False,
        }

        self.assertTrue(_is_publishable_baseline(**common))
        self.assertFalse(
            _is_publishable_baseline(**{**common, "selected_case_count": 1})
        )
        self.assertFalse(_is_publishable_baseline(**{**common, "git_dirty": True}))
        self.assertFalse(
            _is_publishable_baseline(**{**common, "executed_case_count": 23})
        )

    def test_all_seed_cases_exist_and_have_oracles(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        seeds = load_resume_release_dataset(ROOT / "evals" / "resume_release_cases.json")
        seed_ids = {case.case_id for case in seeds.cases}

        for case in suite["cases"]:
            with self.subTest(case_id=case["case_id"]):
                self.assertIn(case["seed_case_id"], seed_ids)
                self.assertGreaterEqual(len(case["steps"]), 1)
                self.assertIn("first", case["expected"])
                self.assertIn("final", case["expected"])

    def test_suite_includes_a_multiturn_majority_and_single_turn_controls(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        multi_turn = sum(len(case["steps"]) > 1 for case in suite["cases"])

        self.assertGreaterEqual(multi_turn, 10)
        self.assertGreater(multi_turn, len(suite["cases"]) // 2)

    def test_suite_covers_required_interaction_families(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        kinds = {step["kind"] for case in suite["cases"] for step in case["steps"]}
        tags = {tag for case in suite["cases"] for tag in case.get("tags", [])}

        self.assertTrue({"answer_question", "recovery_action", "stale_recovery_action"}.issubset(kinds))
        self.assertTrue({"context_patch", "replan", "replace_stop", "select_plan"}.issubset(kinds))
        self.assertTrue({"multi_stop", "hard_constraint", "plan_version", "topbar"}.issubset(tags))

    def test_time_patch_oracles_exempt_only_fields_scoped_to_the_time_edit(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        cases = {case["case_id"]: case for case in suite["cases"]}
        departure_fields = set(
            cases["post_plan_afternoon_departure_patch"]["expected"]["final"][
                "preserve_constraints_except"
            ]
        )
        topbar_fields = set(
            cases["topbar_time_patch_replan"]["expected"]["final"][
                "preserve_constraints_except"
            ]
        )

        self.assertIn("planning_window.start_kind", departure_fields)
        self.assertIn("trip_time_scope", topbar_fields)

    def test_meal_role_oracle_accepts_generic_meal_role(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        cases = {case["case_id"]: case for case in suite["cases"]}

        for case_id in (
            "planning_area_not_departure_location",
            "friends_beijing_feature_multi_stop",
        ):
            with self.subTest(case_id=case_id):
                groups = cases[case_id]["expected"]["final"]["required_role_groups"]
                self.assertIn("meal", groups[1])

    def test_clarification_metric_stops_at_the_answer_resume_turn(self) -> None:
        transcript = [
            {
                "kind": "answer_question",
                "execution_status": "executed",
                "assertions_passed": True,
                "status": "completed",
                "plan_count": 2,
            },
            {
                "kind": "context_patch",
                "execution_status": "executed",
                "assertions_passed": False,
                "status": "context_saved",
                "plan_count": 0,
            },
        ]

        self.assertTrue(_clarification_resume_succeeded(transcript))
        transcript[0]["plan_count"] = 0
        self.assertFalse(_clarification_resume_succeeded(transcript))

    def test_user_session_failures_are_preserved_as_replayable_regressions(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        cases = {case["case_id"]: case for case in suite["cases"]}

        area = cases["session_area_answer_no_repeat"]
        self.assertIn("real_session_regression", area["tags"])
        self.assertEqual(area["steps"][1]["content"], "望京SOHO")
        self.assertEqual(area["steps"][1]["expect"]["status"], "completed")
        self.assertEqual(area["steps"][2]["kind"], "context_patch")

        no_area = cases["planning_area_not_required_without_area"]
        self.assertIsNone(no_area["expected"]["first"]["question_field"])

        child = cases["child_indoor_exploration"]["expected"]["final"]
        self.assertEqual(child["required_party_values"], {"children": 1, "child_age": 6})

        departure = cases["post_plan_morning_departure_clarify"]
        self.assertIn("real_session_regression", departure["tags"])
        self.assertEqual(departure["steps"][-1]["content"], "早上九点吧")
        self.assertEqual(departure["steps"][-1]["expect"]["status"], "completed")

        conflict = cases["session_departure_return_conflict_recovery"]
        self.assertIn("real_session_regression", conflict["tags"])
        self.assertEqual(conflict["steps"][1]["content"], "九点")
        self.assertEqual(conflict["steps"][2]["expect"]["recovery_code"], "DEPARTURE_NOT_BEFORE_RETURN_BY")
        self.assertEqual(conflict["steps"][3]["field_value"], "21:30")

    def test_database_findings_have_explicit_regression_cases(self) -> None:
        suite = load_suite(ROOT / "evals" / "s_rx_multiturn_v1.json")
        cases = {case["case_id"]: case for case in suite["cases"]}

        trace = cases["automatic_recovery_reenters_planner_trace"]
        self.assertIn("same_http_run", trace["tags"])
        self.assertEqual(
            trace["steps"][0]["expect"]["minimum_trace_stage_starts"]["structure"],
            2,
        )

        reset = cases["start_new_request_after_recovery"]["expected"]["final"]
        self.assertIn("total_distance_km", reset["absent_constraint_fields"])
        self.assertIn("max_distance_km", reset["absent_constraint_fields"])

        self.assertIn("single_stop_replacement_no_candidate", cases)
        self.assertIn("single_stop_replacement_success", cases)
        no_candidate = cases["single_stop_replacement_no_candidate"]
        self.assertEqual(
            set(no_candidate["fixtures"]["catalog_resource_allowlist"]),
            {"osm-way-24476204", "osm-node-10962410448"},
        )
        self.assertIn(
            "geocoding_points_by_location",
            cases["automatic_recovery_reenters_planner_trace"]["fixtures"],
        )
        self.assertFalse(cases["cancel_recovery_turn"]["counts_as_task_success"])

        distance = cases["total_distance_five_km_scope"]
        self.assertEqual(
            distance["expected"]["final"]["required_constraint_values"]["total_distance_km"],
            5.0,
        )

        cancellation = cases["cancel_recovery_turn"]["expected"]["final"]
        self.assertTrue(cancellation["no_plans"])
        self.assertIn("total_distance_km", cancellation["absent_constraint_fields"])

    def test_cancelled_draft_is_discarded_without_replanning(self) -> None:
        response = {
            "status": "completed",
            "recovery_resolution": "cancel_turn",
            "plans": [],
        }
        expected = {
            "status": "completed",
            "recovery_resolution": "cancel_turn",
            "no_plans": True,
            "absent_constraint_fields": ["total_distance_km"],
        }

        self.assertTrue(_matches(response, expected, {"active_constraints": {}}))
        response["plans"] = [{"stops": []}]
        self.assertFalse(_matches(response, expected, {"active_constraints": {}}))

    def test_matcher_checks_role_groups_and_displayed_date(self) -> None:
        response = {
            "status": "completed",
            "plans": [{"stops": [{"role": "ACTIVITY"}, {"role": "DINNER"}]}],
            "planning_context": {
                "when": {"date": {"value": "2026-10-10"}},
                "who": {"value": {"children": 1, "child_age": 6}},
            },
        }
        expected = {
            "status": "completed",
            "date": "2026-10-10",
            "required_role_groups": [["activity"], ["lunch", "dinner"]],
            "required_party_values": {"children": 1, "child_age": 6},
        }

        self.assertTrue(_matches(response, expected, {}))
        expected["date"] = "2026-10-11"
        self.assertFalse(_matches(response, expected, {}))

    def test_schedule_diagnostics_measure_stop_span_and_period_coverage(self) -> None:
        stats = _plan_stats(
            {
                "plans": [
                    {
                        "stops": [
                            {"role": "lunch", "start": "11:00", "end": "12:00"},
                            {"role": "activity", "start": "14:00", "end": "17:00"},
                            {"role": "dinner", "start": "18:30", "end": "20:00"},
                        ]
                    }
                ]
            }
        )

        self.assertEqual(stats["schedule_spans_minutes"], [540])
        self.assertEqual(stats["scheduled_periods"], [["morning", "afternoon", "evening"]])

    def test_context_patch_router_assertion_uses_only_current_turn_delta(self) -> None:
        response = {
            "status": "context_saved",
            "runtime_decisions": [
                {"stage": "turn_interpreter", "model_invoked": True, "attempts": 2}
            ],
        }

        self.assertFalse(_matches(response, {"router_attempts": 0}, {}))
        self.assertTrue(
            _matches(
                response,
                {"router_attempts": 0},
                {},
                runtime_decisions=[],
            )
        )

    def test_replacement_invariants_follow_plan_diff_not_selected_plan(self) -> None:
        response = {
            "plans": [
                {
                    "plan_id": "new-plan",
                    "stops": [
                        {"resource_id": "new-activity"},
                        {"resource_id": "same-dinner"},
                    ],
                }
            ],
            "plan_diffs": [
                {
                    "new_plan_id": "new-plan",
                    "replacements": [
                        {
                            "stop_index": 0,
                            "before_resource_id": "old-activity",
                            "after_resource_id": "new-activity",
                        }
                    ],
                    "locked_stops": [
                        {"stop_index": 1, "resource_id": "same-dinner"}
                    ],
                }
            ],
        }

        self.assertEqual(
            _replacement_diff_invariants(response, ["old-activity", "same-dinner"], 0),
            (True, True),
        )
        response["plans"][0]["stops"][1]["resource_id"] = "changed-dinner"
        self.assertEqual(
            _replacement_diff_invariants(response, ["old-activity", "same-dinner"], 0),
            (False, True),
        )

    def test_dependent_steps_without_interaction_are_not_sent(self) -> None:
        self.assertEqual(
            _step_precondition(
                None,
                "unused",
                {"kind": "answer_question", "content": "回答"},
                {},
            ),
            "no_pending_question",
        )
        self.assertEqual(
            _step_precondition(
                None,
                "unused",
                {"kind": "message", "content": "新需求"},
                {"status": "needs_recovery"},
            ),
            "pending_interaction_requires_typed_answer_step",
        )
        self.assertEqual(
            _step_precondition(
                None,
                "unused",
                {"kind": "recovery_action", "action_kind": "start_new_request"},
                {},
            ),
            "matching_recovery_action_missing",
        )

    def test_runtime_snapshot_delta_does_not_recount_prior_decisions(self) -> None:
        first = [{"stage": "turn_interpreter", "attempts": 1}]
        second = [
            *first,
            {"stage": "planning_intent", "attempts": 1},
        ]

        self.assertEqual(_snapshot_delta(first, first), [])
        self.assertEqual(_snapshot_delta(first, second), second[1:])
        self.assertEqual(_snapshot_delta(second, first), [])

    def test_geocoder_fixture_can_resolve_the_answer_without_masking_initial_ambiguity(self) -> None:
        provider = _EvaluationGeocodingProvider(
            resolution="ambiguous",
            resolution_by_location={"太古里": "resolved"},
            clock=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )

        initial = provider.geocode(GeocodeRequest(location_text="某个公园"))
        answer = provider.geocode(GeocodeRequest(location_text=" 太古里 "))

        self.assertEqual(initial.resolution, GeocodeResolution.AMBIGUOUS)
        self.assertEqual(answer.resolution, GeocodeResolution.RESOLVED)

    def test_geocoder_fixture_can_replay_a_case_specific_origin_point(self) -> None:
        provider = _EvaluationGeocodingProvider(
            resolution="resolved",
            point_by_location={
                "测试起点": {"latitude": 39.6, "longitude": 116.44}
            },
            district_by_location={"测试起点": "大兴区"},
            clock=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )

        fact = provider.geocode(GeocodeRequest(location_text=" 测试起点 "))

        self.assertEqual(fact.point.latitude, 39.6)
        self.assertEqual(fact.point.longitude, 116.44)
        self.assertEqual(fact.district, "大兴区")


if __name__ == "__main__":
    unittest.main()

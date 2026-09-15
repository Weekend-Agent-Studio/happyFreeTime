import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.evaluation.resume_release import (
    EvalAssertion,
    _EvaluationDependencies,
    EvaluationConfigurationError,
    _EvaluationGeocodingProvider,
    _annotate_runtime_timing,
    _aggregate_results,
    _failure_details,
    _runtime_summary,
    _score_case,
    load_evaluation_variant,
    run_resume_release_evaluation,
)
from app.domain.providers import GeocodeRequest
from evals.resume_release_dataset import load_resume_release_dataset


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evals" / "resume_release_cases.json"


class ResumeReleaseEvaluationTest(unittest.TestCase):
    def test_reviewed_cases_are_selectable_without_draft_opt_in(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)

        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["plan_dinner_only_light"],
        )
        self.assertEqual(report.case_count, 1)
        self.assertEqual(report.label_status_counts["reviewed"], 1)

    def test_real_llm_variant_requires_explicit_opt_in(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)

        with self.assertRaises(EvaluationConfigurationError):
            run_resume_release_evaluation(
                dataset,
                variant="B0_DOWNSTREAM_RULE",
                case_ids=["plan_quiet_date_chat"],
                allow_draft=True,
            )

    def test_offline_case_runs_through_http_and_reports_assertions(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)

        with tempfile.TemporaryDirectory() as output_dir:
            report = run_resume_release_evaluation(
                dataset,
                variant="offline_sanity",
                case_ids=["plan_dinner_only_light"],
                allow_draft=True,
                output_dir=Path(output_dir),
            )

            self.assertEqual(report.case_count, 1)
            self.assertEqual(report.label_status_counts["reviewed"], 1)
            result = report.case_results[0]
            self.assertEqual(result.case_id, "plan_dinner_only_light")
            self.assertTrue(result.transcript)
            self.assertEqual(result.transcript[0]["action"], "message")
            self.assertTrue(
                any(item.metric == "outcome" for item in result.assertions)
            )
            self.assertTrue((Path(output_dir) / "report.json").exists())
            self.assertTrue((Path(output_dir) / "report.md").exists())
            payload = json.loads(
                (Path(output_dir) / "report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["variant"]["variant_id"], "offline_sanity")
            self.assertEqual(payload["label_status_counts"]["reviewed"], 1)

    def test_transcript_records_router_and_planning_semantic_stages(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)

        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["plan_all_day_date_relaxed"],
            allow_draft=True,
        )

        trace = report.case_results[0].transcript[0]["semantic_stage_trace"]
        self.assertEqual(
            trace["turn_interpreter"]["semantic_fields"]["scene_tags"],
            ["约会"],
        )
        self.assertIn(
            "romantic",
            trace["planning_intent"]["objective_kinds"],
        )

    def test_modification_transcript_contains_selection_and_two_candidate_diffs(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)

        report = run_resume_release_evaluation(
            dataset,
            variant=load_evaluation_variant("offline_sanity"),
            case_ids=["modify_activity_shorter"],
            allow_draft=True,
        )

        result = report.case_results[0]
        self.assertEqual(
            [item["action"] for item in result.transcript],
            ["message", "select_plan", "replace_stop"],
        )
        replacement = result.transcript[-1]
        self.assertIn(replacement["status_code"], {200, 409})
        if replacement["status_code"] == 200:
            self.assertLessEqual(len(replacement["plan_diffs"]), 2)
            self.assertEqual(
                len(replacement["plan_diffs"]),
                len(replacement["plans"]),
            )

    def test_report_marks_no_model_usage_for_offline_variant(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["plan_quiet_date_chat"],
            allow_draft=True,
        )

        runtime = report.case_results[0].runtime_summary
        self.assertEqual(runtime["model_invocation_count"], 0)
        self.assertEqual(runtime["known_input_tokens"], 0)
        self.assertEqual(runtime["known_output_tokens"], 0)

    def test_provider_postconditions_are_explicit_in_the_transcript(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["plan_dinner_only_light", "plan_rain_indoor_fallback"],
            allow_draft=True,
        )

        by_case = {item.case_id: item for item in report.case_results}
        dinner_assertions = {
            item.metric: item for item in by_case["plan_dinner_only_light"].assertions
        }
        rainy_assertions = {
            item.metric: item for item in by_case["plan_rain_indoor_fallback"].assertions
        }
        self.assertEqual(
            dinner_assertions["opening_hours_postconditions"].status,
            "passed",
        )
        self.assertEqual(
            rainy_assertions["weather_safe_plan_postcondition"].status,
            "passed",
        )
        self.assertTrue(
            by_case["plan_dinner_only_light"].transcript[0]["provider_facts"]
        )
        self.assertIn(
            "availability_observation_coverage",
            dinner_assertions,
        )

    def test_all_unavailable_fixture_is_not_silently_counted_as_a_valid_plan(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["conflict_dinner_unavailable"],
            allow_draft=True,
        )

        result = report.case_results[0]
        assertion = next(
            item
            for item in result.assertions
            if item.metric == "availability_fixture_enforced"
        )
        self.assertEqual(assertion.status, "passed")
        self.assertEqual(result.actual_outcome, "conflict")
        self.assertFalse(result.transcript[-1].get("plans"))
        self.assertIn(
            "availability",
            (result.transcript[-1].get("conflict") or {}).get("fields", []),
        )

    def test_location_question_uses_explicit_geocoding_fixture(self) -> None:
        provider = _EvaluationGeocodingProvider(
            resolution="not_found",
            clock=datetime(2026, 8, 15, 10, tzinfo=timezone.utc),
        )
        fact = provider.geocode(
            GeocodeRequest(location_text="我公司附近", city="北京市")
        )
        self.assertEqual(fact.resolution.value, "not_found")
        self.assertIsNone(fact.point)

    def test_evaluator_marks_only_first_dense_retrieval_as_cold(self) -> None:
        dependencies = _EvaluationDependencies(
            catalog=object(),
            router_delegate=object(),
            planning_intent_provider=object(),
            candidate_retriever=object(),
            recommendation_advisor=object(),
        )
        first = _annotate_runtime_timing(
            {
                "runtime_decisions": [
                    {
                        "stage": "candidate_retrieval",
                        "adapter": "bge_hybrid",
                        "query_count": 1,
                        "fallback_reason": None,
                    }
                ]
            },
            dependencies,
        )
        second = _annotate_runtime_timing(
            {
                "runtime_decisions": [
                    {
                        "stage": "candidate_retrieval",
                        "adapter": "bge_hybrid",
                        "query_count": 1,
                        "fallback_reason": None,
                    }
                ]
            },
            dependencies,
        )
        self.assertTrue(first["runtime_decisions"][0]["evaluation_cold_start"])
        self.assertFalse(second["runtime_decisions"][0]["evaluation_cold_start"])

    def test_failure_details_keep_assertion_and_runtime_codes_bounded(self) -> None:
        details = _failure_details(
            assertions=[
                EvalAssertion(
                    metric="semantic_objective_recall",
                    status="failed",
                    expected=["low_fatigue"],
                    actual=["romantic"],
                )
            ],
            transcript=[
                {
                    "runtime_decisions": [
                        {
                            "stage": "recommendation_advisor",
                            "adapter": "fallback",
                            "fallback_reason": "invalid_proposal_contract:ungrounded_text",
                            "attempts": 1,
                            "latency_ms": 120,
                        }
                    ]
                }
            ],
            error=None,
            label_status="draft",
        )
        codes = {item.code for item in details}
        self.assertIn("assertion.semantic_objective_recall", codes)
        self.assertIn(
            "runtime_fallback.invalid_proposal_contract:ungrounded_text",
            codes,
        )
        self.assertIn("draft_label", codes)

    def test_runtime_summary_separates_decisions_from_provider_attempts(self) -> None:
        summary = _runtime_summary(
            [
                {
                    "runtime_decisions": [
                        {
                            "stage": "turn_interpreter",
                            "adapter": "llm",
                            "model_invoked": True,
                            "attempts": 2,
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "fallback_reason": None,
                        },
                        {
                            "stage": "planning_intent",
                            "adapter": "not_run",
                            "model_invoked": False,
                            "attempts": 0,
                            "fallback_reason": "planning cannot start before normalized constraints are complete",
                        },
                        {
                            "stage": "candidate_retrieval",
                            "adapter": "rule_fallback",
                            "model_invoked": False,
                            "attempts": 0,
                            "fallback_reason": "index_missing",
                        },
                    ]
                }
            ]
        )

        self.assertEqual(summary["model_decision_count"], 1)
        self.assertEqual(summary["provider_attempt_count"], 2)
        self.assertEqual(summary["model_invocation_count"], 1)
        self.assertEqual(summary["fallback_count"], 0)
        self.assertEqual(summary["stage"]["planning_intent"]["fallback_count"], 0)
        self.assertEqual(summary["stage"]["candidate_retrieval"]["fallback_count"], 0)

        details = _failure_details(
            assertions=[],
            transcript=[
                {
                    "runtime_decisions": [
                        {
                            "stage": "planning_intent",
                            "adapter": "not_run",
                            "model_invoked": False,
                            "attempts": 0,
                            "fallback_reason": "planning cannot start before normalized constraints are complete",
                        },
                        {
                            "stage": "candidate_retrieval",
                            "adapter": "rule_fallback",
                            "model_invoked": False,
                            "attempts": 0,
                            "fallback_reason": "index_missing",
                        },
                    ]
                }
            ],
            error=None,
            label_status="reviewed",
        )
        self.assertNotIn(
            "runtime_fallback.planning_cannot_start_before_normalized_constraints_are_complete",
            {item.code for item in details},
        )
        self.assertIn("runtime_fallback.index_missing", {item.code for item in details})

    def test_downstream_assertions_are_not_evaluable_after_missing_plan(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        case = next(item for item in dataset.cases if item.case_id == "modify_activity_shorter")
        result = _score_case(
            case,
            load_evaluation_variant("offline_sanity"),
            repeat=1,
            transcript=[
                {
                    "action": "message",
                    "plans": [],
                    "status": "needs_input",
                    "question": {"field": "date"},
                },
                {"action": "select_plan", "plans": []},
                {
                    "action": "replace_stop",
                    "plans": [],
                    "plan_diffs": [],
                    "status": "error",
                },
            ],
            final_view=None,
            last_payload=None,
            elapsed_ms=1,
            error=None,
        )

        statuses = {
            item.metric: item.status
            for item in result.assertions
            if item.metric != "outcome"
        }
        self.assertTrue(statuses)
        self.assertTrue(all(status == "not_evaluable" for status in statuses.values()))
        self.assertEqual(
            {item.code for item in result.failure_details},
            {"assertion.outcome"},
        )
        aggregate = _aggregate_results([result])
        self.assertGreater(aggregate["not_evaluable_assertion_count"], 0)
        self.assertEqual(aggregate["required_assertion_pass_rate"]["denominator"], 1)

    def test_empty_plan_cannot_turn_a_downstream_pass_into_success(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        case = next(
            item for item in dataset.cases if item.case_id == "plan_quiet_date_chat"
        )
        # Simulate a future scorer using all([]), which would otherwise report a
        # false pass despite the missing prerequisite plan.
        with patch(
            "app.evaluation.resume_release._score_planning_shape",
            side_effect=lambda _case, _row, add: add(
                "synthetic_empty_collection_check",
                "passed",
                expected="at least one plan",
                actual=[],
            ),
        ):
            result = _score_case(
                case,
                load_evaluation_variant("offline_sanity"),
                repeat=1,
                transcript=[
                    {
                        "action": "message",
                        "plans": [],
                        "status": "needs_input",
                        "question": {"field": "date"},
                    }
                ],
                final_view=None,
                last_payload=None,
                elapsed_ms=1,
                error=None,
            )

        assertion = next(
            item
            for item in result.assertions
            if item.metric == "synthetic_empty_collection_check"
        )
        self.assertEqual(assertion.status, "not_evaluable")

    def test_aggregate_reports_unique_cases_and_execution_count(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["plan_dinner_only_light", "plan_quiet_date_chat"],
            repeats=2,
            allow_draft=True,
        )

        aggregate = report.aggregate
        self.assertEqual(aggregate["unique_case_count"], 2)
        self.assertEqual(aggregate["execution_count"], 4)
        self.assertEqual(aggregate["reviewed_unique_case_count"], 2)


if __name__ == "__main__":
    unittest.main()

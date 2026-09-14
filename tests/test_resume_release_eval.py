import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.resume_release import (
    EvalAssertion,
    _EvaluationDependencies,
    EvaluationConfigurationError,
    _EvaluationGeocodingProvider,
    _annotate_runtime_timing,
    _failure_details,
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


if __name__ == "__main__":
    unittest.main()

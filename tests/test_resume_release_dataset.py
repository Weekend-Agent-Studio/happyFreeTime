import json
import unittest
from pathlib import Path

from evals.resume_release_dataset import load_resume_release_dataset


ROOT = Path(__file__).resolve().parents[1]


class ResumeReleaseDatasetTest(unittest.TestCase):
    def test_dataset_has_unique_cases_and_required_product_coverage(self) -> None:
        dataset = load_resume_release_dataset(
            ROOT / "evals" / "resume_release_cases.json"
        )

        self.assertGreaterEqual(len(dataset.cases), 30)
        self.assertEqual(
            len({case.case_id for case in dataset.cases}),
            len(dataset.cases),
        )
        categories = {case.category for case in dataset.cases}
        self.assertTrue(
            {"planning", "clarification", "conflict", "modification"}.issubset(
                categories
            )
        )
        self.assertTrue(all(case.split == "holdout" for case in dataset.cases))
        # AI-generated labels are candidates for manual review, not resume-ready
        # ground truth.  The later evaluator must not silently call them gold.
        self.assertTrue(all(case.label_status == "draft" for case in dataset.cases))

    def test_modification_cases_define_selection_target_and_invariants(self) -> None:
        dataset = load_resume_release_dataset(
            ROOT / "evals" / "resume_release_cases.json"
        )

        modifications = [
            case for case in dataset.cases if case.category == "modification"
        ]
        self.assertGreaterEqual(len(modifications), 8)
        for case in modifications:
            actions = [step.action for step in case.steps]
            self.assertIn("select_plan", actions)
            self.assertIn("replace_stop", actions)
            self.assertTrue(case.expected.require_plan_diff)
            self.assertTrue(case.expected.preserve_non_target_stops)
            self.assertEqual(case.expected.max_replacement_candidates, 2)

    def test_external_fact_cases_pin_replay_fixtures(self) -> None:
        dataset = load_resume_release_dataset(
            ROOT / "evals" / "resume_release_cases.json"
        )
        by_id = {case.case_id: case for case in dataset.cases}

        self.assertEqual(by_id["plan_rain_indoor_fallback"].fixtures.weather, "rainy")
        self.assertEqual(
            by_id["conflict_dinner_unavailable"].fixtures.availability,
            "all_unavailable",
        )
        self.assertEqual(dataset.evaluation_clock.isoformat(), "2026-08-15T10:00:00+08:00")

    def test_retrieval_holdout_is_separate_and_draft_labeled(self) -> None:
        path = ROOT / "evals" / "retrieval_holdout_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))

        training_ids = {
            item["case_id"]
            for item in json.loads(
                (ROOT / "evals" / "retrieval_cases.json").read_text(encoding="utf-8")
            )
        }
        self.assertGreaterEqual(len(cases), 15)
        self.assertFalse(training_ids & {item["case_id"] for item in cases})
        self.assertTrue(all(item["split"] == "holdout" for item in cases))
        self.assertTrue(all(item["label_status"] == "draft" for item in cases))
        self.assertTrue(all("query" in item and "relevance" in item for item in cases))
        profile_ids = {
            item["resource_id"]
            for item in json.loads(
                (
                    ROOT
                    / "data"
                    / "retrieval"
                    / "v1"
                    / "poi_semantic_profiles.json"
                ).read_text(encoding="utf-8")
            )["profiles"]
        }
        referenced_ids = {
            resource_id
            for item in cases
            for resource_id in item["relevance"]
        }
        self.assertTrue(referenced_ids.issubset(profile_ids))


if __name__ == "__main__":
    unittest.main()

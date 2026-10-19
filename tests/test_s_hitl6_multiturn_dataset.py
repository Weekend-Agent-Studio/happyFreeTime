from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class SHitl6MultiturnDatasetTest(unittest.TestCase):
    def test_focus_manifest_selects_eight_reviewed_source_cases(self) -> None:
        manifest = json.loads(
            (ROOT / "evals" / "s_hitl6_multiturn_v1.json").read_text(encoding="utf-8")
        )
        source = json.loads(
            (ROOT / "evals" / "s_rx_multiturn_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], "s-hitl6-multiturn.v1")
        self.assertEqual(manifest["review_status"], "reviewed deterministic cases selected from S-RX4")
        self.assertEqual(len(manifest["cases"]), 8)
        self.assertEqual(len({case["case_id"] for case in manifest["cases"]}), 8)
        source_ids = {case["case_id"] for case in source["cases"]}
        self.assertTrue(
            all(case["source_case_id"] in source_ids for case in manifest["cases"])
        )

    def test_focus_manifest_covers_the_hitl6_metric_families(self) -> None:
        manifest = json.loads(
            (ROOT / "evals" / "s_hitl6_multiturn_v1.json").read_text(encoding="utf-8")
        )
        expected = {
            "silent_failure_count",
            "unexplained_repeat_question_count",
            "recovery_action_applied_rate",
            "recovery_to_feasible_plan_rate",
            "bottleneck_improvement_rate",
            "diagnosis_action_alignment_rate",
            "repeated_ineffective_strategy_count",
            "new_request_constraint_leak_count",
            "failure_diagnosis_coverage",
            "modification_failure_preservation_rate",
        }
        covered = {
            family
            for case in manifest["cases"]
            for family in case["metric_families"]
        }
        self.assertEqual(covered, expected)
        self.assertEqual(set(manifest["metric_definitions"]), expected)


if __name__ == "__main__":
    unittest.main()

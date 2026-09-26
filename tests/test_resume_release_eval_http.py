import unittest
from pathlib import Path

from app.evaluation.resume_release import run_resume_release_evaluation
from evals.resume_release_dataset import load_resume_release_dataset


ROOT = Path(__file__).resolve().parents[1]


class ResumeReleaseEvaluationHttpTest(unittest.TestCase):
    def test_cases_use_isolated_sessions_and_continue_after_a_case_failure(self) -> None:
        dataset = load_resume_release_dataset(ROOT / "evals" / "resume_release_cases.json")

        report = run_resume_release_evaluation(
            dataset,
            variant="offline_sanity",
            case_ids=["plan_dinner_only_light", "clarify_unknown_date"],
            allow_draft=True,
        )

        self.assertEqual(report.case_count, 2)
        self.assertEqual(len(report.case_results), 2)
        self.assertTrue(all(result.transcript for result in report.case_results))
        session_ids = {
            item["session_id"]
            for result in report.case_results
            for item in result.transcript
            if item.get("session_id")
        }
        self.assertEqual(len(session_ids), 2)


if __name__ == "__main__":
    unittest.main()

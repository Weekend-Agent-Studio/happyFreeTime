import unittest

from evals.run_retrieval_eval import _aggregate


class RetrievalEvalAggregationTest(unittest.TestCase):
    @staticmethod
    def _row(case_id: str, *, metric_eligible: bool) -> dict:
        return {
            "case_id": case_id,
            "metric_eligible": metric_eligible,
            "ranked_ids": ["poi-1"],
            "relevance": {"poi-1": 3.0},
            "latency_ms": 1.0,
            "fallback_reason": None,
            "hits_at_5": True,
            "invalid_provenance": 0.0,
        }

    def test_explicit_metric_eligibility_controls_quality_denominator(self) -> None:
        report = _aggregate(
            (
                self._row("eligible", metric_eligible=True),
                self._row("safety", metric_eligible=False),
            )
        )

        self.assertEqual(report["evaluated_case_count"], 1)
        self.assertEqual(report["no_ground_truth_case_count"], 1)
        self.assertEqual(report["mrr"], 1.0)


if __name__ == "__main__":
    unittest.main()

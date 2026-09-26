import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app.domain.constraints import Intent, Interpretation
from app.services.demo_router import DemoRouter
from app.services.router_extractor import RouterContext
from app.evaluation.resume_release import EvaluationConfigurationError, run_resume_release_evaluation
from evals.frozen_interpretations import (
    FrozenFixtureError,
    FrozenInterpretationFixture,
    FrozenInterpretationSet,
    FrozenTurnInterpreter,
    fixture_coverage,
    input_sha256,
    load_frozen_interpretations,
)
from evals.resume_release_dataset import load_resume_release_dataset


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evals" / "resume_release_cases.json"


def _interpretation() -> Interpretation:
    return Interpretation(
        primary_intent=Intent.PLAN_OUTING,
        intent_scores={Intent.PLAN_OUTING: 1.0},
    )


def _fixture(*, status: str = "reviewed", text: str = "明天出去玩") -> FrozenInterpretationFixture:
    return FrozenInterpretationFixture(
        case_id="case_one",
        step_index=0,
        input_sha256=input_sha256(text),
        interpretation=_interpretation(),
        generated_by_model="deepseek-flash",
        generated_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        review_status=status,
        review_note="manual review",
    )


class FrozenInterpretationTest(unittest.TestCase):
    def test_reviewed_fixture_is_served_without_model_invocation(self) -> None:
        fixture_set = FrozenInterpretationSet(
            fixture_set_id="test-set",
            fixtures=(_fixture(),),
        )
        adapter = FrozenTurnInterpreter(fixture_set, case_id="case_one")

        result, runtime = adapter.interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 9, 15)),
        )

        self.assertEqual(result, _interpretation())
        self.assertEqual(runtime.adapter, "frozen_fixture")
        self.assertFalse(runtime.model_invoked)
        self.assertEqual(runtime.attempts, 0)

    def test_draft_fixture_is_rejected_by_runtime_loader(self) -> None:
        fixture_set = FrozenInterpretationSet(
            fixture_set_id="draft-set",
            fixtures=(_fixture(status="draft"),),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures.json"
            path.write_text(fixture_set.model_dump_json(), encoding="utf-8")
            with self.assertRaises(FrozenFixtureError) as context:
                load_frozen_interpretations(path)
        self.assertEqual(context.exception.code, "fixture_draft_rejected")

    def test_missing_and_hash_mismatch_are_not_fallbacks(self) -> None:
        fixture_set = FrozenInterpretationSet(
            fixture_set_id="test-set",
            fixtures=(_fixture(text="明天出去玩"),),
        )
        adapter = FrozenTurnInterpreter(fixture_set, case_id="case_one")

        with self.assertRaises(FrozenFixtureError) as context:
            adapter.interpret("后天出去玩", RouterContext(current_date=date(2026, 9, 15)))
        self.assertEqual(context.exception.code, "fixture_hash_mismatch")

        missing_set = FrozenInterpretationSet(fixture_set_id="empty-set")
        with self.assertRaises(FrozenFixtureError) as context:
            FrozenTurnInterpreter(missing_set, case_id="case_one").interpret(
                "明天出去玩",
                RouterContext(current_date=date(2026, 9, 15)),
            )
        self.assertEqual(context.exception.code, "fixture_missing")

    def test_fixture_coverage_reports_missing_and_hash_mismatch(self) -> None:
        fixture_set = FrozenInterpretationSet(
            fixture_set_id="test-set",
            fixtures=(_fixture(text="正确输入"),),
        )
        self.assertEqual(
            fixture_coverage(
                fixture_set,
                {"case_one": ((0, "错误输入"),), "case_two": ((0, "缺失"),)},
            ),
            (1, 1, 0),
        )

    def test_frozen_variant_requires_explicit_fixture_path(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        with self.assertRaises(EvaluationConfigurationError):
            run_resume_release_evaluation(
                dataset,
                variant="C0_FROZEN_RULE_INTENT",
                case_ids=["plan_dinner_only_light"],
                allow_draft=True,
            )

    def test_c0_reuses_fixture_and_reports_zero_router_model_calls(self) -> None:
        dataset = load_resume_release_dataset(DATASET_PATH)
        text = next(
            item for item in dataset.cases if item.case_id == "plan_dinner_only_light"
        ).steps[0].user_input or ""
        interpretation = DemoRouter().interpret(
            text,
            RouterContext(current_date=date(2026, 8, 15)),
        )
        fixture = FrozenInterpretationFixture(
            case_id="plan_dinner_only_light",
            step_index=0,
            input_sha256=input_sha256(text),
            interpretation=interpretation,
            generated_by_model="manual-reviewed-fixture",
            generated_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
            review_status="reviewed",
            review_note="test fixture",
        )
        fixture_set = FrozenInterpretationSet(
            fixture_set_id="c0-test-set",
            fixtures=(fixture,),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures.json"
            path.write_text(
                json.dumps(
                    fixture_set.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            report = run_resume_release_evaluation(
                dataset,
                variant="C0_FROZEN_RULE_INTENT",
                case_ids=["plan_dinner_only_light"],
                frozen_interpretations=path,
                output_dir=None,
            )

        result = report.case_results[0]
        self.assertEqual(report.metadata["fixture_set_id"], "c0-test-set")
        self.assertEqual(report.metadata["reviewed_fixture_count"], 1)
        self.assertEqual(report.metadata["fixture_miss_count"], 0)
        self.assertEqual(report.metadata["fixture_hash_mismatch_count"], 0)
        self.assertEqual(report.metadata["router_model_calls"], 0)
        self.assertEqual(result.runtime_summary["model_invocation_count"], 0)
        self.assertTrue(
            all(
                decision.get("model_invoked") is False
                for row in result.transcript
                for decision in row.get("runtime_decisions", [])
            )
        )


if __name__ == "__main__":
    unittest.main()

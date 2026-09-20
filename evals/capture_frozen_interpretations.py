"""Capture draft Interpretation fixtures for the controlled C0/C1 pilot.

The command is deliberately opt-in because it makes real model calls.  It
stores only validated Interpretation objects and input hashes; provider
responses, prompts, user text, headers and exceptions never enter the file.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.evaluation.resume_release import DEFAULT_DATASET_PATH
from app.services.router_extractor import RouterContext, build_default_turn_interpreter
from evals.frozen_interpretations import (
    FROZEN_FIXTURE_SET_SCHEMA_VERSION,
    FrozenInterpretationFixture,
    FrozenInterpretationSet,
    input_sha256,
)
from evals.resume_release_dataset import load_resume_release_dataset


PILOT_CASE_IDS = (
    "plan_exact_departure_1520",
    "plan_all_day_date_relaxed",
    "plan_dinner_only_light",
    "plan_parents_novel_not_tiring",
    "plan_quiet_date_chat",
    "clarify_strict_budget_without_amount",
    "conflict_departure_after_return",
    "modify_activity_shorter",
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--max-model-calls", type=int, default=16)
    parser.add_argument("--fixture-set-id", default="pilot-v1")
    args = parser.parse_args()

    if not args.allow_llm:
        parser.error("capturing real fixtures requires --allow-llm")
    if args.max_model_calls < 1:
        parser.error("--max-model-calls must be positive")
    if args.output.exists():
        parser.error("refusing to overwrite an existing fixture file")
    load_dotenv()
    if not os.getenv("LLM_API"):
        parser.error("LLM_API is required before capturing fixtures")

    dataset = load_resume_release_dataset(args.dataset)
    requested = tuple(args.case_ids or PILOT_CASE_IDS)
    by_id = {case.case_id: case for case in dataset.cases}
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        parser.error("unknown case id(s): " + ", ".join(unknown))

    clock = dataset.evaluation_clock
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    interpreter = build_default_turn_interpreter()
    captured_at = datetime.now(timezone.utc)
    fixtures: list[FrozenInterpretationFixture] = []
    used_model_calls = 0

    for case_id in requested:
        case = by_id[case_id]
        has_plans = False
        has_selected_plan = False
        for step_index, step in enumerate(case.steps):
            if step.action == "select_plan":
                has_selected_plan = True
                continue
            # Structured replacement requests bypass Router in the product
            # API, so no Interpretation fixture is needed for that step.
            if step.action != "message" or not step.user_input:
                continue
            if used_model_calls >= args.max_model_calls:
                parser.error("model call budget exhausted before capture completed")
            interpretation, runtime = interpreter.interpret_with_runtime(
                step.user_input,
                RouterContext(
                    current_date=clock.date(),
                    timezone=str(clock.tzinfo),
                    has_plans=has_plans,
                    has_selected_plan=has_selected_plan,
                ),
            )
            used_model_calls += runtime.attempts
            diagnostic_code = runtime.diagnostic_code or "none"
            diagnostic_paths = ",".join(runtime.diagnostic_paths) or "none"
            print(
                "capture_diagnostic "
                f"case_id={case_id} "
                f"step_index={step_index} "
                f"attempts={runtime.attempts} "
                f"adapter={runtime.adapter} "
                f"model={runtime.model_name or 'none'} "
                f"fallback={runtime.fallback_reason or 'none'} "
                f"diagnostic_code={diagnostic_code} "
                f"diagnostic_paths={diagnostic_paths} "
                f"input_tokens={runtime.input_tokens if runtime.input_tokens is not None else 'none'} "
                f"output_tokens={runtime.output_tokens if runtime.output_tokens is not None else 'none'} "
                f"latency_ms={runtime.latency_ms if runtime.latency_ms is not None else 'none'}",
                file=sys.stderr,
            )
            if runtime.fallback_reason or runtime.adapter != "llm":
                code = runtime.diagnostic_code or runtime.fallback_reason or "capture_failed"
                parser.error("model did not produce a usable Interpretation: " + str(code))
            fixtures.append(
                FrozenInterpretationFixture(
                    case_id=case_id,
                    step_index=step_index,
                    input_sha256=input_sha256(step.user_input),
                    interpretation=interpretation,
                    generated_by_model=runtime.model_name or os.getenv("MODEL_NAME", "unknown"),
                    generated_at=captured_at,
                    review_status="draft",
                    review_note="captured from a validated model decision; manual review required",
                )
            )
            has_plans = interpretation.primary_intent.value == "plan_outing"

    fixture_set = FrozenInterpretationSet(
        schema_version=FROZEN_FIXTURE_SET_SCHEMA_VERSION,
        fixture_set_id=args.fixture_set_id,
        fixtures=tuple(fixtures),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        fixture_set.model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(
        f"captured {len(fixtures)} draft fixtures; provider attempts={used_model_calls}; "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

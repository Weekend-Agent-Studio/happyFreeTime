"""Capture draft Interpretation fixtures for controlled frozen evaluations.

The command is deliberately opt-in because it makes real model calls.  It
stores only validated Interpretation objects and input hashes; provider
responses, prompts, user text, headers and exceptions never enter the file.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.evaluation.resume_release import DEFAULT_DATASET_PATH
from app.services.router_extractor import RouterContext, build_default_turn_interpreter
from evals.frozen_interpretations import (
    FROZEN_FIXTURE_SET_SCHEMA_VERSION,
    FrozenInterpretationFixture,
    FrozenInterpretationSet,
    load_frozen_interpretations,
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
    "modify_dinner_shorter",
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument(
        "--all-reviewed",
        action="store_true",
        help="capture the message step from every reviewed dataset case",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume an existing output, preserving matching fixtures",
    )
    parser.add_argument(
        "--seed",
        type=Path,
        help="seed a new output with an existing reviewed fixture set",
    )
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--max-model-calls", type=int, default=16)
    parser.add_argument("--fixture-set-id", default=None)
    args = parser.parse_args()

    if not args.allow_llm:
        parser.error("capturing real fixtures requires --allow-llm")
    if args.max_model_calls < 1:
        parser.error("--max-model-calls must be positive")
    if args.all_reviewed and args.case_ids:
        parser.error("--all-reviewed cannot be combined with --case-id")
    if args.seed is not None and args.output.exists() and not args.resume:
        parser.error("--seed with an existing output requires --resume")
    if args.output.exists() and not args.resume:
        parser.error("refusing to overwrite an existing fixture file")
    if args.seed is not None and not args.seed.exists():
        parser.error("seed fixture file does not exist")
    load_dotenv()
    if not os.getenv("LLM_API"):
        parser.error("LLM_API is required before capturing fixtures")

    dataset = load_resume_release_dataset(args.dataset)
    requested = tuple(
        case.case_id
        for case in dataset.cases
        if args.all_reviewed and case.label_status == "reviewed"
    ) if args.all_reviewed else tuple(args.case_ids or PILOT_CASE_IDS)
    if not requested:
        parser.error("no reviewed cases selected")
    by_id = {case.case_id: case for case in dataset.cases}
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        parser.error("unknown case id(s): " + ", ".join(unknown))

    clock = dataset.evaluation_clock
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    fixture_set_id = args.fixture_set_id or (
        "resume-release-v1" if args.all_reviewed else "pilot-v1"
    )
    existing: dict[tuple[str, int], FrozenInterpretationFixture] = {}
    if args.output.exists():
        try:
            loaded = load_frozen_interpretations(args.output, require_reviewed=False)
        except ValueError as error:
            parser.error(f"cannot resume invalid fixture file: {error}")
        if loaded.fixture_set_id != fixture_set_id:
            parser.error("existing fixture_set_id does not match requested set")
        existing = {(item.case_id, item.step_index): item for item in loaded.fixtures}
    elif args.seed is not None:
        try:
            loaded = load_frozen_interpretations(args.seed, require_reviewed=False)
        except ValueError as error:
            parser.error(f"cannot load seed fixture file: {error}")
        existing = {(item.case_id, item.step_index): item for item in loaded.fixtures}

    interpreter = build_default_turn_interpreter()
    captured_at = datetime.now(timezone.utc)
    fixtures_by_key = dict(existing)
    used_model_calls = 0
    attempted_steps = 0
    failed_steps = 0

    def write_checkpoint() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fixture_set = FrozenInterpretationSet(
            schema_version=FROZEN_FIXTURE_SET_SCHEMA_VERSION,
            fixture_set_id=fixture_set_id,
            fixtures=tuple(
                fixtures_by_key[key] for key in sorted(fixtures_by_key)
            ),
        )
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{args.output.name}.",
            suffix=".tmp",
            dir=args.output.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(fixture_set.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, args.output)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

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
            key = (case_id, step_index)
            expected_hash = input_sha256(step.user_input)
            previous = fixtures_by_key.get(key)
            if previous is not None:
                if previous.input_sha256 != expected_hash:
                    parser.error(
                        "fixture hash mismatch for existing entry "
                        f"{case_id}:{step_index}"
                    )
                has_plans = previous.interpretation.primary_intent.value == "plan_outing"
                continue
            if used_model_calls >= args.max_model_calls:
                parser.error("model call budget exhausted before capture completed")
            attempted_steps += 1
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
                failed_steps += 1
                code = runtime.diagnostic_code or runtime.fallback_reason or "capture_failed"
                if failed_steps / attempted_steps > 0.2:
                    write_checkpoint()
                    parser.error(
                        "capture failure rate exceeded 20%: " + str(code)
                    )
                write_checkpoint()
                parser.error("model did not produce a usable Interpretation: " + str(code))
            fixtures_by_key[key] = FrozenInterpretationFixture(
                case_id=case_id,
                step_index=step_index,
                input_sha256=expected_hash,
                interpretation=interpretation,
                generated_by_model=runtime.model_name or os.getenv("MODEL_NAME", "unknown"),
                generated_at=captured_at,
                review_status="draft",
                review_note="captured from a validated model decision; manual review required",
            )
            write_checkpoint()
            has_plans = interpretation.primary_intent.value == "plan_outing"

    print(
        f"captured {len(fixtures_by_key)} fixtures; new draft fixtures="
        f"{len(fixtures_by_key) - len(existing)}; provider attempts={used_model_calls}; "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

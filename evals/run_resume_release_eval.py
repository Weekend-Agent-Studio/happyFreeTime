"""Run the bounded Resume Release evaluation pilot.

The command is intentionally explicit: draft cases and real LLM calls require
opt-in flags.  The MVP runs one variant at a time; matrix orchestration comes
after the pilot proves that the HTTP runner and labels are trustworthy.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.resume_release import (
    EvaluationConfigurationError,
    run_resume_release_evaluation,
)


def main() -> int:
    # PowerShell on some Windows installations still exposes a legacy code
    # page.  Reports contain Chinese fixture text, so make CLI output explicit
    # UTF-8 without changing the report bytes written to disk.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("resume_release_cases.json"),
    )
    parser.add_argument("--variant", default="offline_sanity")
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--category", choices=["planning", "clarification", "conflict", "modification"])
    parser.add_argument("--label-status", choices=["reviewed", "draft", "all"], default="reviewed")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--max-model-calls", type=int)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_only")
    args = parser.parse_args()

    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = Path("artifacts") / "evals" / f"{args.variant}_{stamp}"
    try:
        report = run_resume_release_evaluation(
            args.dataset,
            variant=args.variant,
            case_ids=args.case_ids,
            category=args.category,
            label_status=args.label_status,
            allow_draft=args.allow_draft,
            allow_llm=args.allow_llm,
            repeats=args.repeats,
            max_model_calls=args.max_model_calls,
            output_dir=output_dir,
        )
    except EvaluationConfigurationError as error:
        parser.error(str(error))
        return 2
    if args.json_only:
        print(report.model_dump_json(indent=2))
    else:
        print(
            json.dumps(
                {
                    "run_id": report.run_id,
                    "variant": report.variant.variant_id,
                    "case_count": report.case_count,
                    "aggregate": report.aggregate,
                    "output_dir": str(output_dir),
                    "draft_results_are_not_resume_grade": report.metadata[
                        "draft_results_are_not_resume_grade"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

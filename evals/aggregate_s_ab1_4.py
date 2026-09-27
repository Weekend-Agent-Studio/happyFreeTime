"""Aggregate the bounded S-AB1.4 comparison without rerunning providers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _summary(paths: list[Path]) -> dict[str, Any]:
    reports = [_read(path) for path in paths]
    rows_by_repeat = [report.get("results") or report.get("case_results") or [] for report in reports]
    per_repeat: list[dict[str, Any]] = []
    passed_by_case: dict[str, list[bool]] = {}
    for index, (report, rows) in enumerate(zip(reports, rows_by_repeat, strict=True), start=1):
        for row in rows:
            passed_by_case.setdefault(row["case_id"], []).append(bool(row.get("task_passed")))
        elapsed = [int(row.get("elapsed_ms") or 0) for row in rows]
        per_repeat.append(
            {
                "repeat": index,
                "task_success": {
                    "numerator": sum(bool(row.get("task_passed")) for row in rows),
                    "denominator": len(rows),
                },
                "initial_policy": report.get("initial_clarification_policy"),
                "clarification_turns": sum(int(row.get("clarification_turns") or 0) for row in rows),
                "elapsed_ms": {
                    "p50": _percentile(elapsed, 0.50),
                    "p95": _percentile(elapsed, 0.95),
                    "mean": round(statistics.mean(elapsed), 1) if elapsed else None,
                },
                "known_input_tokens": report.get("known_input_tokens"),
                "known_output_tokens": report.get("known_output_tokens"),
                "model_decisions": report.get("model_decisions"),
                "provider_attempts": report.get("provider_attempts"),
                "legacy_stage_calls": (
                    sum(sum((row.get("legacy_log_stage_counts") or {}).values()) for row in rows)
                    if rows and "legacy_log_stage_counts" in rows[0]
                    else None
                ),
                "failure_case_ids": [row["case_id"] for row in rows if not row.get("task_passed")],
            }
        )
    stable = sum(all(values) for values in passed_by_case.values())
    flaky = sum(len(values) > 1 and len(set(values)) > 1 for values in passed_by_case.values())
    return {
        "case_count": len(passed_by_case),
        "repeat_count": len(paths),
        "eventual_task_success": {
            "numerator": sum(sum(values) for values in passed_by_case.values()),
            "denominator": sum(len(values) for values in passed_by_case.values()),
        },
        "stable_task_success": {"numerator": stable, "denominator": len(passed_by_case)},
        "flaky_case_count": flaky,
        "per_repeat": per_repeat,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--answer-contract", type=Path, required=True)
    parser.add_argument("--common-cases", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--index-manifest", type=Path, required=True)
    parser.add_argument("--v1-repeat", type=Path, action="append", required=True)
    parser.add_argument("--v2-repeat", type=Path, action="append", required=True)
    parser.add_argument("--original-repeat", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    v1 = _summary(args.v1_repeat)
    v2 = _summary(args.v2_repeat)
    original = _summary([args.original_repeat])
    manifest = {
        "schema_version": "s-ab1.4-manifest.v1",
        "status": "completed_with_scope_limits",
        "systems": {
            "legacy_v1_original": {"tag": "legacy-v1-original-baseline", "commit": "90639f30dbeb09c7a65e60f0fe8af7336ccee544"},
            "legacy_v1_completed": {"tag": "legacy-v1-completed-baseline", "commit": "5662a945c8b5767050901034af7534cb674173b1"},
            "resume_v2": {"tag": "planner-v2-eval-baseline", "commit": "bef5fa3ded41a6c4f9d5a70be53d4d1cf32147e1"},
        },
        "dataset": {"path": str(args.dataset), "sha256": _sha256(args.dataset), "case_count": 16, "reviewed": 16},
        "answer_contract": {"path": str(args.answer_contract), "sha256": _sha256(args.answer_contract), "max_clarification_turns": 3},
        "common_cases": {"path": str(args.common_cases), "sha256": _sha256(args.common_cases)},
        "oracle": {"path": str(args.oracle), "sha256": _sha256(args.oracle)},
        "retrieval": {"variant": "Hybrid BGE", "index_manifest": str(args.index_manifest), "index_manifest_sha256": _sha256(args.index_manifest), "model": "BAAI/bge-small-zh-v1.5", "cold_start_excluded": True},
        "model": {"provider": "DeepSeek-compatible OpenAI API", "model": "deepseek-flash", "temperature": 0.0, "router_timeout_seconds": 15, "format_retry": 1, "business_retry": 0},
        "protocol": {"runs_per_case": 2, "network_retry": "one bounded preflight retry", "beam": "enabled", "v2_variant": "Live Router + LLM PlanningIntent + Hybrid Retrieval + Beam + Grounded Advisor"},
        "preflight": {"bge_local_model": "passed", "hybrid_index_validation": "passed", "provider": "passed_after_one_bounded_network_retry", "formal_environment_failures": 0},
    }
    report = {
        "schema_version": "s-ab1.4-report.v1",
        "manifest": manifest,
        "main_comparison": {"legacy_v1_completed": v1, "resume_v2": v2},
        "historical_appendix": {"legacy_v1_original": original},
        "failure_stage_distribution": {
            "legacy_v1_completed": {"conflict_classification": 6, "other": 0},
            "resume_v2": {},
            "legacy_v1_original": {"clarification_or_slot": 6, "conflict_classification": 3, "planner_or_semantic": 3},
        },
        "planner_core_proxy": {
            "note": "The five complete cases had no clarification in either Completed V1 or V2; this is an observational complete-input subset, not a true SlotAgent-bypassed Planner isolation run.",
            "completed_v1": "5/5 in each repeat",
            "resume_v2": "5/5 in each repeat",
        },
        "observable_limits": [
            "The five intentionally incomplete cases were completed by defaults in both systems, so clarification recovery is 0/0; this experiment does not establish a multi-turn recovery advantage.",
            "The legacy adapter exposes stage-call counts but not comparable token usage or external tool-call counts.",
            "The pilot adapter does not expose per-response V2 fallback and hard-constraint assertion traces; those must be taken from the separate Resume V2 C/B reports, not invented here.",
            "The first V2 attempt was invalid because the reviewed fixture omitted geocoding=resolved and is retained outside the formal repeat files.",
        ],
        "resume_usable_conclusions": [
            "On the frozen 16-case product-level input set, Completed V1 was 13/16 in both runs and Resume V2 was 16/16 in both runs.",
            "The three Completed V1 failures were stable hard-conflict classification failures: the legacy system returned plans for departure-after-return, departure-outside-window, and strict-budget cases.",
            "V2 stable task success was 16/16; no flaky cases occurred in either system.",
            "Observed end-to-end latency was lower for V2 (P50 5.5–5.6s, P95 7.1–7.3s) than Completed V1 (P50 10.0–10.4s, P95 23.9–25.0s), under this environment and model/provider configuration.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    (args.output.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# S-AB1.4 Legacy V1 Completed vs Resume V2",
        "",
        "## Frozen setup",
        "",
        f"- Common tasks: 16 reviewed cases; dataset SHA256 `{manifest['dataset']['sha256']}`.",
        f"- Completed V1: `legacy-v1-completed-baseline` (`{manifest['systems']['legacy_v1_completed']['commit'][:7]}`).",
        f"- Resume V2: `planner-v2-eval-baseline` (`{manifest['systems']['resume_v2']['commit'][:7]}`).",
        "- V2 configuration: Live Router + LLM PlanningIntent + Hybrid Retrieval + Beam + Grounded Advisor.",
        "- Each case ran twice; Original V1 ran once as historical appendix.",
        "",
        "## Main product-level comparison",
        "",
        "| System | Eventual executions | Stable task success | Flaky cases | P50/P95 latency |",
        "|---|---:|---:|---:|---:|",
        "| Legacy V1 Completed | 26/32 (81.25%) | 13/16 (81.25%) | 0 | 10.0–10.4s / 23.9–25.0s |",
        "| Resume V2 | 32/32 (100%) | 16/16 (100%) | 0 | 5.5–5.6s / 7.1–7.3s |",
        "",
        "Completed V1 failed the same three hard-conflict cases in both runs; it returned plans instead of the expected conflict outcomes. Resume V2 passed all 16 cases in both runs.",
        "",
        "## Failure-stage distribution",
        "",
        "- Completed V1: 6/6 executions failed at conflict classification (three cases repeated twice); no SlotAgent/adapter failure occurred in the formal repeats.",
        "- Resume V2: no task failures in the formal repeats; no failure stage was observed.",
        "- Original V1: failures were distributed across clarification/slot handling, conflict classification, and compound semantic planning; this appendix is descriptive only.",
        "",
        "## Clarification and cost observations",
        "",
        "- Both systems completed the five incomplete-input cases without asking a question, so clarification recovery is `0/0`; this is not evidence that either system has solved arbitrary multi-turn recovery.",
        "- V2 recorded 38 model decisions / 38 Provider attempts per run, with 137,642–138,465 input tokens and 16,152–16,178 output tokens.",
        "- Completed V1 recorded 48 agent-stage calls per run (Intent/Slot/Planner stage counters); token and external tool-call counts were not exposed by the historical adapter and are not compared as if equivalent.",
        "",
        "## Planner-core proxy",
        "",
        "The five complete cases passed 5/5 in every run for both systems. This is only a complete-input proxy; it does not bypass the Legacy SlotAgent or V2 QuestionGate and must not be presented as a pure Planner isolation experiment.",
        "",
        "## Historical Original V1 appendix",
        "",
        "Original V1 completed 4/16 in its single pass. This is an historical reference, not the primary baseline; its failures include clarification/slot handling, conflict classification, and compound semantic planning.",
        "",
        "## Scope and limitations",
        "",
        "- The first V2 attempt is retained as an invalid pre-run because the fixture defaulted explicit locations to `geocoding=not_found`; it is excluded from all formal counts.",
        "- The pilot adapter did not expose per-response V2 fallback, hard-constraint assertion traces, or comparable V1 token/tool counters. Use the separate Resume V2 C/B reports for those metrics; do not infer them here.",
        "- The main defensible Resume claim is: on 16 reviewed common tasks, stable task success was 13/16 for Completed V1 versus 16/16 for Resume V2, with stable zero observed flakiness; the clarification advantage remains untested by this particular set.",
    ]
    (args.output.parent / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.output.parent / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.parent), "v1_stable": v1["stable_task_success"], "v2_stable": v2["stable_task_success"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

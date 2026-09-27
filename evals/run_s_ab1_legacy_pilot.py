"""Run a bounded, read-only pilot against the historical Multi-Agent graph.

The legacy graph is loaded from a separate checkout so this runner cannot
silently execute Resume V2 services.  It records only the public outcome,
bounded timing and safe log counters; model responses and secrets are never
written to the report.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # The project .env uses trailing inline comments (for example
        # ``BASE_URL=https://... # provider``).  Do not pass that comment to
        # the OpenAI-compatible client.
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key and value:
            os.environ.setdefault(key, value)


def _load_cases(
    dataset_path: Path,
    case_ids: list[str],
    contract_path: Path | None = None,
) -> list[dict[str, Any]]:
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = {item["case_id"]: item for item in data["cases"]}
    missing = [case_id for case_id in case_ids if case_id not in cases]
    if missing:
        raise ValueError(f"unknown case ids: {', '.join(missing)}")
    contracts = (data.get("clarification_contracts") or {}) if contract_path is None else json.loads(
        contract_path.read_text(encoding="utf-8")
    ).get("clarification_contracts", {})
    selected: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = dict(cases[case_id])
        case["clarification_contract"] = contracts.get(case_id, {})
        selected.append(case)
    return selected


def _question_field(question: str) -> str | None:
    text = question or ""
    patterns = {
        "date": r"日期|哪天|哪一日|周几|星期几|哪一天",
        "location": r"地点|位置|地标|附近|从哪里|从哪儿|出发地",
        "budget_per_person": r"预算|人均|花费|多少钱|费用|预算范围",
        "return_by": r"回家|返程|回来|几点前",
        "companions": r"谁同行|同行人|和谁|几个人|几位|多少人|家人|朋友|父母|同伴",
        "preferences": r"偏好|喜欢|风格|想要|要求|特殊|其他要求|其他限制|倾向|希望怎么|想去什么|希望安排",
        "time_hint": r"出发时间|时间段|时段|什么时候方便|何时|几点|大概多久|多长时间|时间安排|出发",
    }
    for field, pattern in patterns.items():
        if re.search(pattern, text):
            return field
    return None


def _question_category(question: str) -> str:
    return _question_field(question) or "unclassified_question"


def _run_case(app: Any, case: dict[str, Any]) -> dict[str, Any]:
    user_input = next(
        step["user_input"]
        for step in case["steps"]
        if step.get("action") == "message"
    )
    thread_id = f"s-ab1-legacy-{case['case_id']}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    output = io.StringIO()
    started = time.perf_counter()
    error: str | None = None
    error_module: str | None = None
    result: dict[str, Any] = {}
    question: str | None = None
    initial_outcome = "unknown"
    initial_question_field: str | None = None
    question_fields: list[str | None] = []
    question_categories: list[str] = []
    clarification_turns = 0
    clarification_failure: str | None = None
    contract = case.get("clarification_contract") or {}
    answers = contract.get("clarification_answers") or {}
    allowed_fields = set(contract.get("allowed_question_fields") or answers)
    max_turns = int(contract.get("max_clarification_turns") or 3)
    try:
        from langgraph.types import Command

        with contextlib.redirect_stdout(output):
            result = app.invoke({"user_input": user_input}, config=config) or {}
            initial_snapshot = app.get_state(config)
            if initial_snapshot.next and initial_snapshot.interrupts:
                initial_question = str(
                    (initial_snapshot.interrupts[0].value or {}).get("question", "")
                )
                initial_question_field = _question_field(initial_question)
                initial_outcome = "question"
            elif result.get("plans", {}).get("plans"):
                initial_outcome = "plan"
            elif result.get("reply"):
                initial_outcome = "reply"
            while True:
                snapshot = app.get_state(config)
                if not (snapshot.next and snapshot.interrupts):
                    break
                payload = snapshot.interrupts[0].value or {}
                question = str(payload.get("question", ""))
                field = _question_field(question)
                question_fields.append(field)
                question_categories.append(_question_category(question))
                if clarification_turns >= max_turns:
                    clarification_failure = "max_clarification_turns"
                    break
                if field in question_fields[:-1]:
                    clarification_failure = "repeated_question_field"
                    break
                if field is None or field not in allowed_fields or field not in answers:
                    clarification_failure = "question_field_not_in_contract"
                    break
                clarification_turns += 1
                result = app.invoke(Command(resume=str(answers[field])), config=config) or result
    except Exception as exc:  # bounded diagnostic; do not expose provider text
        error = type(exc).__name__
        if isinstance(exc, ModuleNotFoundError):
            error_module = getattr(exc, "name", None)
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    # A question can have been answered successfully.  Only retain it as a
    # pending question when the graph is still interrupted or the contract
    # explicitly stopped recovery.
    if error is None:
        try:
            pending = app.get_state(config)
            if not (pending.next and pending.interrupts):
                question = None
        except Exception:
            pass

    if error:
        actual_outcome = "error"
    elif question:
        actual_outcome = "question"
    elif result.get("plans", {}).get("plans"):
        actual_outcome = "plan"
    elif result.get("reply"):
        actual_outcome = "reply"
    else:
        actual_outcome = "unknown"

    expected = (contract.get("expected_final_outcome") or case.get("expected", {}).get("outcome"))
    expected_initial = contract.get("expected_initial_outcome")
    expected_initial_field = contract.get("expected_initial_question_field")
    initial_policy_passed = (
        expected_initial is None
        or (
            initial_outcome == expected_initial
            and (
                expected_initial_field is None
                or initial_question_field == expected_initial_field
            )
        )
    )
    task_passed = actual_outcome == expected and clarification_failure is None
    logs = output.getvalue()
    llm_rounds = {
        "intent": len(re.findall(r"\[intent_agent\]", logs)),
        "slot": len(re.findall(r"\[slot_agent\]", logs)),
        "planner": len(re.findall(r"\[planner_agent\]", logs)),
        "executor": len(re.findall(r"\[executor_agent\]", logs)),
    }
    return {
        "case_id": case["case_id"],
        "expected_outcome": expected,
        "initial_outcome": initial_outcome,
        "initial_question_field": initial_question_field,
        "initial_policy_passed": initial_policy_passed,
        "actual_outcome": actual_outcome,
        "task_passed": task_passed,
        "question_field": _question_field(question or ""),
        "question_present": bool(question),
        "question_fields": question_fields,
        "question_categories": question_categories,
        "clarification_turns": clarification_turns,
        "clarification_failure": clarification_failure,
        "eventual_outcome": actual_outcome,
        "time_to_first_plan_ms": elapsed_ms if actual_outcome in {"plan", "conflict"} else None,
        "elapsed_ms": elapsed_ms,
        "legacy_log_stage_counts": llm_rounds,
        "error_type": error,
        "error_module": error_module,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("evals/resume_release_cases.json"))
    parser.add_argument("--case-id", action="append", dest="case_ids", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--common-contract",
        type=Path,
        default=Path("evals/s_ab1_common_cases.json"),
    )
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("legacy pilot makes real model calls; pass --allow-llm explicitly")

    legacy_root = args.legacy_root.resolve()
    if not (legacy_root / "Agents" / "graph.py").exists():
        parser.error(f"legacy graph not found under {legacy_root}")
    if args.env_file:
        _load_env_file(args.env_file.resolve())

    # Import the historical graph only after putting its checkout first.
    sys.path.insert(0, str(legacy_root))
    # The historical repository declares an MCP weather bridge, but its
    # original environment does not contain the external ``mcp`` package.
    # Use the same deterministic weather fixture for the pilot without
    # changing any historical Agent/Planner code.
    import Tools as legacy_tools  # type: ignore import-not-found
    from langchain_core.tools import tool

    @tool
    def _pilot_get_weather(latitude: str, longitude: str) -> str:
        """Return the fixed weather fixture used by the common pilot."""
        return json.dumps(
            {"condition": "clear", "temperature": 25, "source": "s-ab1-fixed"},
            ensure_ascii=False,
        )

    legacy_tools.get_weather = _pilot_get_weather
    from Agents.graph import app  # type: ignore import-not-found

    cases = _load_cases(
        args.dataset.resolve(),
        args.case_ids,
        args.common_contract.resolve(),
    )
    results = [_run_case(app, case) for case in cases]
    passed = sum(1 for item in results if item["task_passed"])
    report = {
        "schema_version": "s-ab1-legacy-multiturn-pilot.v2",
        "legacy_commit": "develop@90639f3",
        "case_count": len(results),
        "task_success": {"numerator": passed, "denominator": len(results)},
        "eventual_task_success": {"numerator": passed, "denominator": len(results)},
        "initial_clarification_policy": {
            "numerator": sum(
                item["initial_policy_passed"]
                for item in results
                if (next(
                    case.get("clarification_contract") or {}
                    for case in cases
                    if case["case_id"] == item["case_id"]
                ).get("expected_initial_outcome"))
            ),
            "denominator": sum(
                bool(
                    next(
                        case.get("clarification_contract") or {}
                        for case in cases
                        if case["case_id"] == item["case_id"]
                    ).get("expected_initial_outcome")
                )
                for item in results
            ),
        },
        "clarification_recovery_success": {
            "numerator": sum(
                item["task_passed"] and item["clarification_turns"] > 0
                for item in results
            ),
            "denominator": sum(item["clarification_turns"] > 0 for item in results),
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_count": len(results), "task_success": report["task_success"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

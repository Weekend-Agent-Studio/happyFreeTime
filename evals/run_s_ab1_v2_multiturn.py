"""Run the product-level S-AB1 clarification-recovery pilot for Resume V2.

This runner deliberately uses the public HTTP contract and the live V2
Router. Answers are fixed, reviewed fixture values; no second LLM is used to
answer a clarification. Raw provider responses and secrets are never saved.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.evaluation.resume_release import (
    _build_evaluation_app,
    _build_evaluation_dependencies,
    _headers,
    load_evaluation_variant,
)
from evals.resume_release_dataset import load_resume_release_dataset


def _load_contracts(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")).get(
        "clarification_contracts", {}
    )


def _request_id(case_id: str, index: int) -> str:
    return f"s-ab1-v2-{case_id[:42]}-{index}"[:64]


def _outcome(data: dict[str, Any]) -> str:
    if data.get("plans"):
        return "plan"
    if data.get("conflict"):
        return "conflict"
    if data.get("question") or data.get("status") == "needs_input":
        return "question"
    if data.get("reply"):
        return "reply"
    return "unknown"


def _safe_runtime_totals(data: dict[str, Any]) -> dict[str, int]:
    decisions = data.get("runtime_decisions") or []
    return {
        "model_decisions": sum(1 for item in decisions if item.get("model_invoked")),
        "provider_attempts": sum(int(item.get("attempts") or 0) for item in decisions),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in decisions),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in decisions),
    }


def _run_case(app: Any, case: Any, contract: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    answers = contract.get("clarification_answers") or {}
    allowed = set(contract.get("allowed_question_fields") or answers)
    max_turns = int(contract.get("max_clarification_turns") or 3)
    question_fields: list[str | None] = []
    clarification_turns = 0
    recovery_failure: str | None = None
    initial_outcome = "unknown"
    initial_question_field: str | None = None
    first_plan_ms: int | None = None
    totals = {"model_decisions": 0, "provider_attempts": 0, "input_tokens": 0, "output_tokens": 0}
    response_data: dict[str, Any] = {}

    with TestClient(app) as client:
        created = client.post("/api/sessions", headers=_headers())
        session_id = created.json()["data"]["session_id"]
        initial = next(step.user_input for step in case.steps if step.action == "message")
        response = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=_headers(),
            json={"request_id": _request_id(case.case_id, 0), "content": initial},
        )
        response_data = (response.json() or {}).get("data") or {}
        initial_outcome = _outcome(response_data)
        initial_question_field = (
            (response_data.get("question") or {}).get("field")
            if isinstance(response_data.get("question"), dict)
            else None
        )
        running = _safe_runtime_totals(response_data)
        for key in totals:
            totals[key] += running[key]

        while response_data.get("question"):
            question = response_data["question"]
            field = question.get("field")
            question_fields.append(field)
            if clarification_turns >= max_turns:
                recovery_failure = "max_clarification_turns"
                break
            if field in question_fields[:-1]:
                recovery_failure = "repeated_question_field"
                break
            if field is None or field not in allowed or field not in answers:
                recovery_failure = "question_field_not_in_contract"
                break
            clarification_turns += 1
            answer = str(answers[field])
            response = client.post(
                f"/api/sessions/{session_id}/messages",
                headers=_headers(),
                json={
                    "request_id": _request_id(case.case_id, clarification_turns),
                    "content": answer,
                    "clarification_reply": {
                        "clarification_id": question.get("clarification_id"),
                        "action": "answer",
                        "value": answer,
                    },
                },
            )
            response_data = (response.json() or {}).get("data") or {}
            running = _safe_runtime_totals(response_data)
            for key in totals:
                totals[key] += running[key]

        final_outcome = _outcome(response_data)
        if final_outcome in {"plan", "conflict"}:
            first_plan_ms = round((time.perf_counter() - started) * 1000)
        elapsed_ms = round((time.perf_counter() - started) * 1000)

    expected = contract.get("expected_final_outcome") or case.expected.outcome
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
    passed = recovery_failure is None and final_outcome == expected
    return {
        "case_id": case.case_id,
        "expected_final_outcome": expected,
        "initial_outcome": initial_outcome,
        "initial_question_field": initial_question_field,
        "initial_policy_passed": initial_policy_passed,
        "final_outcome": final_outcome,
        "task_passed": passed,
        "clarification_recovered": passed and clarification_turns > 0,
        "clarification_turns": clarification_turns,
        "question_fields": question_fields,
        "recovery_failure": recovery_failure,
        "time_to_first_plan_ms": first_plan_ms,
        "elapsed_ms": elapsed_ms,
        **totals,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evals/resume_release_cases.json"))
    parser.add_argument("--common-contract", type=Path, default=Path("evals/s_ab1_common_cases.json"))
    parser.add_argument("--case-id", action="append", dest="case_ids", required=True)
    parser.add_argument("--variant", default="B0_DOWNSTREAM_RULE")
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("pass --allow-llm for the live V2 Router")

    dataset = load_resume_release_dataset(args.dataset.resolve())
    cases = {case.case_id: case for case in dataset.cases}
    contracts = _load_contracts(args.common_contract.resolve())
    variant = load_evaluation_variant(args.variant)
    dependencies = _build_evaluation_dependencies(variant)
    results: list[dict[str, Any]] = []
    for case_id in args.case_ids:
        case = cases[case_id]
        with tempfile.TemporaryDirectory(prefix="hft-s-ab1-v2-") as temp_dir:
            app, _ = _build_evaluation_app(
                Path(temp_dir) / "case.db",
                case,
                variant,
                dataset.evaluation_clock,
                dependencies=dependencies,
            )
            results.append(_run_case(app, case, contracts.get(case_id, {})))

    passed = sum(item["task_passed"] for item in results)
    initial_cases = [
        item for item in results
        if contracts.get(item["case_id"], {}).get("expected_initial_outcome")
    ]
    recovered = sum(item["clarification_recovered"] for item in results)
    asked = sum(item["clarification_turns"] > 0 for item in results)
    report = {
        "schema_version": "s-ab1-v2-multiturn-pilot.v1",
        "variant": args.variant,
        "case_count": len(results),
        "eventual_task_success": {"numerator": passed, "denominator": len(results)},
        "initial_clarification_policy": {
            "numerator": sum(item["initial_policy_passed"] for item in initial_cases),
            "denominator": len(initial_cases),
        },
        "clarification_recovery_success": {"numerator": recovered, "denominator": asked},
        "average_clarification_turns": round(
            sum(item["clarification_turns"] for item in results) / len(results), 3
        ),
        "model_decisions": sum(item["model_decisions"] for item in results),
        "provider_attempts": sum(item["provider_attempts"] for item in results),
        "known_input_tokens": sum(item["input_tokens"] for item in results),
        "known_output_tokens": sum(item["output_tokens"] for item in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "results"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

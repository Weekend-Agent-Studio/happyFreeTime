"""Run three bounded, redacted natural-language command-contract probes.

The probe records only safe compiled-command metadata and runtime counters.
It never writes model payloads, prompts, credentials, headers, or user text.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.services.router_extractor import RouterContext, build_default_turn_interpreter


PROBES = (
    (
        "replace_activity_shorter",
        "餐厅保留，只把活动换近一点",
        "activity_shorter",
    ),
    (
        "replace_second_stop",
        "把第二站换掉",
        "second_stop",
    ),
    (
        "replace_ambiguous_place",
        "把那个地方换一下",
        "needs_resolution",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--max-provider-attempts", type=int, default=6)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real model calls require --allow-llm")
    if args.max_provider_attempts < 3:
        parser.error("--max-provider-attempts must allow at least one attempt per probe")

    load_dotenv()
    interpreter = build_default_turn_interpreter()
    rows: list[dict[str, object]] = []
    total_attempts = 0
    failed = False
    for case_id, user_input, expectation in PROBES:
        interpretation, runtime = interpreter.interpret_with_runtime(
            user_input,
            RouterContext(
                current_date=date(2026, 9, 20),
                has_plans=True,
                has_selected_plan=True,
            ),
        )
        total_attempts += runtime.attempts
        command = interpretation.conversation_command
        target = command.target if command is not None else None
        target_fields = tuple(
            field
            for field, value in (
                ("role", target.role if target is not None else None),
                ("resource_type", target.resource_type if target is not None else None),
                ("stop_index", target.stop_index if target is not None else None),
            )
            if value is not None
        )
        locked_target_fields = tuple(
            tuple(
                field
                for field, value in (
                    ("role", item.role),
                    ("resource_type", item.resource_type),
                    ("stop_index", item.stop_index),
                )
                if value is not None
            )
            for item in (command.locked_targets if command is not None else ())
        )
        passed = _matches_expectation(
            expectation,
            interpretation.primary_intent.value,
            command,
            target,
            runtime.fallback_reason,
            runtime.diagnostic_code,
        )
        failed = failed or not passed
        rows.append(
            {
                "case_id": case_id,
                "passed": passed,
                "primary_intent": interpretation.primary_intent.value,
                "operation": command.operation.value if command is not None else None,
                "target_fields": target_fields,
                "locked_target_fields": locked_target_fields,
                "prefer_shorter_travel": (
                    command.constraint_patch.prefer_shorter_travel
                    if command is not None
                    else None
                ),
                "attempts": runtime.attempts,
                "diagnostic_code": runtime.diagnostic_code,
                "fallback_reason": runtime.fallback_reason,
                "input_tokens": runtime.input_tokens,
                "output_tokens": runtime.output_tokens,
                "latency_ms": runtime.latency_ms,
            }
        )
        if total_attempts > args.max_provider_attempts:
            parser.error("provider attempt budget exceeded")

    print(
        json.dumps(
            {
                "schema_version": "command-contract-probe.v1",
                "provider_attempt_count": total_attempts,
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


def _matches_expectation(
    expectation: str,
    primary_intent: str,
    command: object,
    target: object,
    fallback_reason: str | None,
    diagnostic_code: str | None,
) -> bool:
    if primary_intent != "refine_plan" or fallback_reason is not None:
        return False
    if expectation == "needs_resolution":
        return command is None and diagnostic_code == "target_resolution_required"
    if command is None or target is None or command.operation.value != "replace":
        return False
    if expectation == "activity_shorter":
        return (
            target.role is not None
            and target.role.value == "activity"
            and command.constraint_patch.prefer_shorter_travel
            and any(
                item.resource_type is not None
                and item.resource_type.value == "restaurant"
                for item in command.locked_targets
            )
        )
    if expectation == "second_stop":
        return target.stop_index == 1
    return False


if __name__ == "__main__":
    raise SystemExit(main())

"""Run a bounded, redacted structured-output compatibility probe.

The command deliberately uses two small, frozen probes rather than the full
Resume Release matrix:

* a two-field schema repeated five times;
* the real ``Interpretation`` schema on three representative inputs,
  repeated three times each.

It records only safe outcome/diagnostic metadata.  Raw model messages,
arguments, prompts and credentials are never written to the report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import Interpretation
from app.services.llm_compat import thinking_extra_body, structured_output_schema
from app.services.model_errors import model_failure_reason
from app.services.model_usage import extract_token_usage
from app.services.router_extractor import (
    RouterContext,
    StructuredOutputDiagnostic,
    TurnInterpreter,
    classify_structured_output_failure,
)


class TinyStructuredProbe(BaseModel):
    """A deliberately small schema for isolating provider compatibility."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["plan_outing", "refine_plan", "chitchat"]
    confidence: float = Field(ge=0, le=1)


class _AttemptRecord:
    def __init__(
        self,
        *,
        success: bool,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        diagnostic: StructuredOutputDiagnostic | None = None,
        transport_reason: str | None = None,
    ) -> None:
        self.success = success
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.diagnostic = diagnostic
        self.transport_reason = transport_reason


class _RecordingStructuredModel:
    """Record one safe row per provider attempt while preserving behavior."""

    def __init__(self, delegate: Any, schema: type[BaseModel]) -> None:
        self._delegate = delegate
        self._schema = schema
        self.records: list[_AttemptRecord] = []

    def invoke(self, messages: list[object]) -> object:
        started_at = perf_counter()
        try:
            result = self._delegate.invoke(messages)
        except Exception as error:
            self.records.append(
                _AttemptRecord(
                    success=False,
                    latency_ms=_elapsed_ms(started_at),
                    input_tokens=None,
                    output_tokens=None,
                    transport_reason=model_failure_reason(error),
                )
            )
            raise

        usage = extract_token_usage(result)
        try:
            _validate_probe_result(result, self._schema)
        except Exception as error:
            self.records.append(
                _AttemptRecord(
                    success=False,
                    latency_ms=_elapsed_ms(started_at),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    diagnostic=classify_structured_output_failure(result, error),
                )
            )
        else:
            self.records.append(
                _AttemptRecord(
                    success=True,
                    latency_ms=_elapsed_ms(started_at),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--tiny-repeats", type=int, default=5)
    parser.add_argument("--full-repeats", type=int, default=3)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real model calls require --allow-llm")
    if args.tiny_repeats < 1 or args.full_repeats < 1:
        parser.error("repeat counts must be positive")

    load_dotenv()
    api_key = os.getenv("LLM_API")
    if not api_key:
        parser.error("LLM_API is required; no credential is written to the report")

    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    base_url = os.getenv("BASE_URL", "https://api.deepseek.com")
    timeout = _timeout_seconds()
    report = {
        "schema_version": "structured-output-diagnostic.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "model_name": model_name,
            "base_url_host": urlparse(base_url).hostname or "configured",
            "router_timeout_seconds": timeout,
            "max_retries": 0,
            "tiny_repeats": args.tiny_repeats,
            "full_repeats": args.full_repeats,
        },
        "tiny_schema": _run_tiny_probe(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            repeats=args.tiny_repeats,
        ),
        "full_interpretation": _run_full_probe(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            repeats=args.full_repeats,
        ),
    }
    output_dir = args.output_dir
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(
            _render_markdown(report),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _run_tiny_probe(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    timeout: float,
    repeats: int,
) -> dict[str, Any]:
    model = _build_model(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        schema=TinyStructuredProbe,
        max_tokens=256,
    )
    recorder = _RecordingStructuredModel(model, TinyStructuredProbe)
    messages = [
        SystemMessage(
            content=(
                "只输出 TinyStructuredProbe JSON。intent 只能是 "
                "plan_outing、refine_plan 或 chitchat；confidence 为 0 到 1。"
            )
        ),
        HumanMessage(content="请判断：用户想安排明天下午的活动。"),
    ]
    decision_records: list[list[_AttemptRecord]] = []
    for _ in range(repeats):
        before = len(recorder.records)
        try:
            result = recorder.invoke(messages)
            _validate_probe_result(result, TinyStructuredProbe)
        except Exception:
            # The recorder already captured the safe classification.  A tiny
            # probe has no repair attempt: it isolates provider compatibility.
            pass
        decision_records.append(recorder.records[before:])
    return _summarize_probe(decision_records)


def _run_full_probe(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    timeout: float,
    repeats: int,
) -> dict[str, Any]:
    cases = (
        (
            "quiet_date_chat",
            "想和对象安静约会，最好能慢慢聊天",
            RouterContext(current_date=date(2026, 9, 15)),
        ),
        (
            "modify_activity_shorter",
            "餐厅保留，只把活动换近一点",
            RouterContext(
                current_date=date(2026, 9, 15),
                has_plans=True,
                has_selected_plan=True,
            ),
        ),
        (
            "modify_dinner_less_spicy",
            "活动保留，晚餐换成不那么辣的",
            RouterContext(
                current_date=date(2026, 9, 15),
                has_plans=True,
                has_selected_plan=True,
            ),
        ),
    )
    by_case: dict[str, Any] = {}
    all_decisions: list[list[_AttemptRecord]] = []
    for case_id, user_input, context in cases:
        model = _build_model(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            schema=Interpretation,
            max_tokens=2048,
        )
        recorder = _RecordingStructuredModel(model, Interpretation)
        interpreter = TurnInterpreter(recorder, model_name=model_name)
        decision_records: list[list[_AttemptRecord]] = []
        for _ in range(repeats):
            before = len(recorder.records)
            interpreter.interpret_with_runtime(user_input, context)
            decision_records.append(recorder.records[before:])
            all_decisions.append(decision_records[-1])
        by_case[case_id] = _summarize_probe(decision_records)
    return {"cases": by_case, "aggregate": _summarize_probe(all_decisions)}


def _build_model(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    timeout: float,
    schema: type[BaseModel],
    max_tokens: int,
) -> Any:
    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,
        timeout=timeout,
        max_retries=0,
        max_tokens=max_tokens,
        extra_body=thinking_extra_body(model_name),
    )
    return llm.with_structured_output(
        structured_output_schema(model_name, schema),
        method="function_calling",
        include_raw=True,
    )


def _validate_probe_result(result: object, schema: type[BaseModel]) -> BaseModel:
    if schema is Interpretation:
        return TurnInterpreter._validate(result)
    if isinstance(result, Mapping) and (
        "parsed" in result or "parsing_error" in result
    ):
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            if isinstance(parsing_error, BaseException):
                raise parsing_error
            raise ValueError("provider structured parser failed")
        result = result.get("parsed")
        if result is None:
            raise ValueError("provider structured parser returned no result")
    if isinstance(result, str):
        result = json.loads(result)
    return schema.model_validate(result)


def _summarize_probe(decisions: list[list[_AttemptRecord]]) -> dict[str, Any]:
    flattened = [record for rows in decisions for record in rows]
    first_successes = [bool(rows and rows[0].success) for rows in decisions]
    initial_failures = [rows for rows in decisions if rows and not rows[0].success]
    repair_successes = sum(bool(rows and rows[-1].success) for rows in initial_failures)
    diagnostics = Counter(
        record.diagnostic.code
        for record in flattened
        if record.diagnostic is not None
    )
    diagnostic_paths = Counter(
        path
        for record in flattened
        if record.diagnostic is not None
        for path in record.diagnostic.paths
    )
    diagnostic_error_types = Counter(
        error_type
        for record in flattened
        if record.diagnostic is not None
        for error_type in record.diagnostic.error_types
    )
    transport = Counter(
        record.transport_reason
        for record in flattened
        if record.transport_reason is not None
    )
    latencies = [record.latency_ms for record in flattened]
    observed_tokens = [
        record
        for record in flattened
        if record.input_tokens is not None and record.output_tokens is not None
    ]
    return {
        "decision_count": len(decisions),
        "provider_attempt_count": len(flattened),
        "first_success_count": sum(first_successes),
        "first_success_rate": _rate(sum(first_successes), len(decisions)),
        "initial_failure_count": len(initial_failures),
        "repair_success_count": repair_successes,
        "repair_success_rate": _rate(repair_successes, len(initial_failures)),
        "final_success_count": sum(bool(rows and rows[-1].success) for rows in decisions),
        "diagnostic_code_counts": dict(sorted(diagnostics.items())),
        "diagnostic_path_counts": dict(sorted(diagnostic_paths.items())),
        "diagnostic_error_type_counts": dict(sorted(diagnostic_error_types.items())),
        "transport_failure_counts": dict(sorted(transport.items())),
        "timeout_count": sum(
            count for reason, count in transport.items() if str(reason).startswith("timeout")
        ),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "token_coverage_rate": _rate(len(observed_tokens), len(flattened)),
        "known_input_tokens": (
            sum(record.input_tokens for record in observed_tokens)
            if len(observed_tokens) == len(flattened)
            else None
        ),
        "known_output_tokens": (
            sum(record.output_tokens for record in observed_tokens)
            if len(observed_tokens) == len(flattened)
            else None
        ),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Structured Output Diagnostic",
        "",
        f"- Model: `{report['config']['model_name']}`",
        f"- Base URL host: `{report['config']['base_url_host']}`",
        "- Raw provider responses are intentionally not stored.",
        "",
        "## Tiny schema",
        "",
    ]
    _append_summary(lines, report["tiny_schema"])
    lines.extend(["", "## Full Interpretation schema", ""])
    for case_id, summary in report["full_interpretation"]["cases"].items():
        lines.append(f"### {case_id}")
        _append_summary(lines, summary)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _append_summary(lines: list[str], summary: Mapping[str, Any]) -> None:
    for key in (
        "decision_count",
        "provider_attempt_count",
        "first_success_rate",
        "repair_success_rate",
        "final_success_count",
        "diagnostic_code_counts",
        "diagnostic_path_counts",
        "diagnostic_error_type_counts",
        "transport_failure_counts",
        "timeout_count",
        "latency_ms",
        "token_coverage_rate",
    ):
        lines.append(f"- {key}: `{summary.get(key)}`")


def _timeout_seconds() -> float:
    raw = os.getenv("HFT_ROUTER_TIMEOUT_SECONDS", "15")
    try:
        value = float(raw)
    except ValueError as error:
        raise SystemExit("HFT_ROUTER_TIMEOUT_SECONDS must be a positive number") from error
    if value <= 0 or value != value or value in (float("inf"), float("-inf")):
        raise SystemExit("HFT_ROUTER_TIMEOUT_SECONDS must be a positive number")
    return value


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


if __name__ == "__main__":
    raise SystemExit(main())

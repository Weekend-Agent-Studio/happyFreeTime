"""Replay reviewed S-RX multi-turn cases through the public HTTP API.

Draft suites require an explicit opt-in. Reports retain only bounded response
summaries, never full assistant text, prompts, provider payloads, or model output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.evaluation.resume_release import (
    _build_evaluation_app,
    _build_evaluation_dependencies,
    _headers,
    load_evaluation_variant,
)
from evals.resume_release_dataset import load_resume_release_dataset


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evals" / "s_rx_multiturn_v1.json"
DEFAULT_SEEDS = ROOT / "evals" / "resume_release_cases.json"
SUPPORTED_STEPS = {
    "message",
    "answer_question",
    "recovery_action",
    "stale_recovery_action",
    "context_patch",
    "replan",
    "select_plan",
    "replace_stop",
}


def load_suite(path: Path) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != "s-rx-multiturn.v1":
        raise ValueError("unsupported S-RX suite schema")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not 16 <= len(cases) <= 24:
        raise ValueError("S-RX suite must contain 16–24 cases")
    ids = [item.get("case_id") for item in cases]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("every case requires a case_id")
    if len(set(ids)) != len(ids):
        raise ValueError("case_id values must be unique")
    for case in cases:
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"case {case['case_id']} requires steps")
        unknown = {step.get("kind") for step in steps} - SUPPORTED_STEPS
        if unknown:
            raise ValueError(f"case {case['case_id']} has unsupported steps: {sorted(unknown)}")
        for index, step in enumerate(steps):
            if step["kind"] in {"recovery_action", "stale_recovery_action"} and not (
                step.get("action_kind")
                or step.get("action_id_prefix")
                or step.get("action_field")
            ):
                raise ValueError(
                    f"case {case['case_id']} step {index} must identify a recovery action"
                )
        fixtures = case.get("fixtures") or {}
        by_location = fixtures.get("geocoding_by_location", {})
        allowed_geocoding = {"resolved", "not_found", "ambiguous"}
        if not isinstance(by_location, dict) or any(
            value not in allowed_geocoding for value in by_location.values()
        ):
            raise ValueError(
                f"case {case['case_id']} has invalid geocoding_by_location fixture"
            )
        points_by_location = fixtures.get("geocoding_points_by_location", {})
        if not isinstance(points_by_location, dict) or any(
            not isinstance(value, dict)
            or not isinstance(value.get("latitude"), (int, float))
            or not isinstance(value.get("longitude"), (int, float))
            or not -90 <= value["latitude"] <= 90
            or not -180 <= value["longitude"] <= 180
            for value in points_by_location.values()
        ):
            raise ValueError(
                f"case {case['case_id']} has invalid geocoding_points_by_location fixture"
            )
        resource_allowlist = fixtures.get("catalog_resource_allowlist", [])
        if not isinstance(resource_allowlist, list) or any(
            not isinstance(value, str) or not value.strip()
            for value in resource_allowlist
        ) or len(resource_allowlist) != len(set(resource_allowlist)):
            raise ValueError(
                f"case {case['case_id']} has invalid catalog_resource_allowlist fixture"
            )
    return suite


class _ResourceAllowlistCatalog:
    """Per-case catalog projection for deterministic sparse-candidate tests."""

    def __init__(self, delegate: Any, allowed_resource_ids: set[str]) -> None:
        self._delegate = delegate
        self._allowed_resource_ids = allowed_resource_ids

    def recall(self, constraints: Any):
        result = self._delegate.recall(constraints)
        return result.model_copy(
            update={
                "candidates": [
                    item
                    for item in result.candidates
                    if item.resource_id in self._allowed_resource_ids
                ]
            }
        )


def _request_id(case_id: str, index: int) -> str:
    return f"s-rx-{case_id[:44]}-{index}"[:64]


def _is_publishable_baseline(
    *,
    review_status: str,
    selected_case_count: int,
    suite_case_count: int,
    executed_case_count: int,
    git_dirty: bool,
) -> bool:
    return (
        review_status == "reviewed"
        and selected_case_count == suite_case_count
        and executed_case_count == selected_case_count
        and not git_dirty
    )


def _load_live_model_environment() -> None:
    """Load the project-local live-model configuration without exposing it."""

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if not os.getenv("LLM_API"):
        raise RuntimeError("LLM_API is required before starting a live S-RX evaluation")


def _session_view(client: TestClient, session_id: str) -> dict[str, Any]:
    response = client.get(f"/api/sessions/{session_id}", headers=_headers())
    if response.status_code != 200:
        return {}
    return (response.json() or {}).get("data") or {}


def _constraint_snapshot(view: dict[str, Any]) -> dict[str, Any]:
    request = view.get("active_constraints") or {}
    if request:
        return {
            key: value
            for key, value in request.items()
            if key != "revision"
        }
    context = view.get("planning_context") or {}
    when = context.get("when") or {}
    return {
        "where": (context.get("where") or {}).get("value"),
        "date": (when.get("date") or {}).get("value"),
        "start_at": (when.get("start_at") or {}).get("value"),
        "end_at": (when.get("end_at") or {}).get("value"),
        "who": (context.get("who") or {}).get("value"),
        "budget": (context.get("budget") or {}).get("value"),
        "preferences": (context.get("preferences") or {}).get("value"),
    }


_DIAGNOSTIC_REQUEST_FIELDS = (
    "trip_time_scope",
    "duration_minutes",
    "budget_per_person",
    "max_distance_km",
    "total_distance_km",
    "exact_stop_count",
    "required_stop_roles",
    "activity_time_scope",
    "preferences",
    "diet_tags",
    "scene_tags",
    "avoid",
    "strict_budget",
    "require_availability_confirmation",
)


def _safe_constraint_value(value: Any) -> Any:
    """Keep normalized values and provenance while excluding raw user text."""

    if isinstance(value, dict):
        return {
            key: value[key]
            for key in ("value", "source")
            if key in value
        }
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _diagnostic_constraint_snapshot(view: dict[str, Any]) -> dict[str, Any]:
    """Project only planning values useful for recovery diagnosis.

    Location values and raw evidence text are intentionally omitted. The snapshot
    is for fixed reviewed fixtures, and keeps distance/time/structure scopes
    explicit so Router/Compiler field mistakes can be distinguished.
    """

    request = view.get("active_constraints") or {}
    if not request:
        return {"present": False}
    result: dict[str, Any] = {
        "revision": request.get("revision"),
        "present": True,
        "planning_window": {},
    }
    window = request.get("planning_window") or {}
    for field in ("date", "start_at", "end_at"):
        if window.get(field) is not None:
            result["planning_window"][field] = _safe_constraint_value(window[field])
    for field in ("start_kind", "end_kind"):
        if window.get(field) is not None:
            result["planning_window"][field] = window[field]
    for field in _DIAGNOSTIC_REQUEST_FIELDS:
        value = request.get(field)
        if value is not None and value != []:
            result[field] = _safe_constraint_value(value)
    return result


def _safe_trace_public_details(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("public_details") or {}
    if not isinstance(details, dict):
        return {}
    return {
        key: details[key]
        for key in ("mode", "candidate_count", "reason_code")
        if key in details and isinstance(details[key], (str, int, float, bool, type(None)))
    }


def _safe_search_trace(trace: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "skeleton_id",
        "theoretical_combinations",
        "expansions",
        "finalists",
        "local_schedule_passes",
        "route_candidates",
        "route_provider_requests",
        "route_leg_count",
        "final_selected",
    )
    result = {field: trace[field] for field in fields if field in trace}
    rejected_by = trace.get("rejected_by")
    if isinstance(rejected_by, dict):
        result["rejected_by"] = {
            str(key): int(value)
            for key, value in rejected_by.items()
            if isinstance(value, (int, float))
        }
    return result


def _safe_recovery_diagnostics(reason: dict[str, Any]) -> dict[str, Any]:
    diagnostics = reason.get("diagnostics") or {}
    if not isinstance(diagnostics, dict):
        return {}
    fields = (
        "current_max_distance_km",
        "nearest_candidate_distance_km",
        "requested_stop_count",
        "required_role_count",
        "available_minutes",
        "optimistic_duration_minutes",
        "rejected_by_time_window",
        "rejected_by_route",
        "rejected_by_availability",
        "structures_considered",
        "combinations_expanded",
        "route_candidates",
        "route_provider_requests",
        "route_verification_failures",
        "rejected_by_hard_filter",
    )
    return {field: diagnostics[field] for field in fields if field in diagnostics}


def _request_constraint_value(view: dict[str, Any], field: str) -> Any:
    request = view.get("active_constraints") or {}
    if field in {"date", "start_at", "end_at", "departure_at", "return_by"}:
        window = request.get("planning_window") or {}
        mapping = {
            "date": "date",
            "start_at": "start_at",
            "end_at": "end_at",
            "departure_at": "explicit_departure",
            "return_by": "explicit_return_deadline",
        }
        value = window.get(mapping[field])
    else:
        value = request.get(field)
    return value.get("value") if isinstance(value, dict) else value


def _snapshot_delta(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only appended records from an API snapshot, never its old prefix."""

    common = 0
    for left, right in zip(previous, current):
        if left != right:
            break
        common += 1
    if common == len(current):
        return []
    return current[common:]


def _step_precondition(
    client: TestClient | None,
    session_id: str,
    step: dict[str, Any],
    previous: dict[str, Any],
) -> str | None:
    kind = step["kind"]
    if kind == "message" and previous.get("status") in {"needs_input", "needs_recovery"}:
        return "pending_interaction_requires_typed_answer_step"
    if kind == "answer_question" and not (
        (previous.get("question") or {}).get("clarification_id")
    ):
        return "no_pending_question"
    if kind in {"recovery_action", "stale_recovery_action"} and _action(
        previous, step
    ) is None:
        return "matching_recovery_action_missing"
    if kind == "select_plan":
        view = _session_view(client, session_id) if client is not None else {}
        plans = view.get("plans") or []
        strategy = step.get("strategy", "first")
        if not plans or (strategy == "second" and len(plans) < 2):
            return "plan_selection_target_missing"
        if strategy == "recommended":
            recommended_id = (previous.get("recommendation_advice") or {}).get(
                "recommended_plan_id"
            )
            if not recommended_id or not any(
                plan.get("plan_id") == recommended_id for plan in plans
            ):
                return "recommended_plan_missing"
    if kind == "replace_stop":
        view = _session_view(client, session_id) if client is not None else {}
        selected_id = view.get("selected_plan_id")
        selected = next(
            (plan for plan in view.get("plans", []) if plan.get("plan_id") == selected_id),
            None,
        )
        stop_index = step.get("stop_index")
        if selected is None or not isinstance(stop_index, int):
            return "selected_plan_or_stop_missing"
        if not 0 <= stop_index < len(selected.get("stops") or []):
            return "stop_index_out_of_range"
    return None


def _response_data(response) -> tuple[int, dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, (body.get("data") or {}) if isinstance(body, dict) else {}


def _patch_action_fields(action: dict[str, Any]) -> set[str]:
    patch = action.get("patch") or {}
    set_fields = patch.get("set_fields") or {}
    clear_fields = patch.get("clear_fields") or ()
    fields: set[str] = set()
    if isinstance(set_fields, dict):
        fields.update(str(field) for field in set_fields)
    fields.update(str(field) for field in clear_fields)
    return fields


def _recovery_action_fields(action: dict[str, Any]) -> set[str]:
    fields = {str(action["field"])} if action.get("field") else set()
    return fields | _patch_action_fields(action)


def _safe_recovery_action(action: dict[str, Any]) -> dict[str, Any]:
    """Keep identifiers and typed scope, not user-facing text or patch values."""

    fields = sorted(_patch_action_fields(action))
    return {
        "action_id": action.get("action_id"),
        "kind": action.get("kind"),
        "field": action.get("field"),
        "patch_fields": fields,
        "input_type": action.get("input_type"),
        "request_revision": action.get("request_revision"),
        "plan_version_id": action.get("plan_version_id"),
    }


def _action(interaction: dict[str, Any], step: dict[str, Any]) -> dict[str, Any] | None:
    recovery = interaction.get("recovery") or {}
    actions = recovery.get("actions") or []
    action_kind = step.get("action_kind")
    prefix = step.get("action_id_prefix")
    action_field = step.get("action_field")
    return next(
        (
            item
            for item in actions
            if (action_kind is None or item.get("kind") == action_kind)
            and (prefix is None or str(item.get("action_id", "")).startswith(prefix))
            and (action_field is None or action_field in _recovery_action_fields(item))
        ),
        None,
    )


def _perform_step(
    client: TestClient,
    session_id: str,
    case_id: str,
    index: int,
    step: dict[str, Any],
    previous: dict[str, Any],
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    kind = step["kind"]
    request_id = _request_id(case_id, index)
    headers = _headers()
    payload: dict[str, Any] | None = None
    method = "POST"
    path = f"/api/sessions/{session_id}/messages"
    content = step.get("content", "")
    if kind == "message":
        payload = {"request_id": request_id, "content": content}
    elif kind == "answer_question":
        question = previous.get("question") or {}
        if not question.get("clarification_id"):
            return 422, {}, {"runner_error": "no_pending_question"}
        content = step.get("content", "")
        payload = {
            "request_id": request_id,
            "content": content or step.get("action", "answer"),
            "clarification_reply": {
                "clarification_id": question["clarification_id"],
                "request_revision": question.get("request_revision", 0),
                "action": step.get("action", "answer"),
                "value": content or None if step.get("action", "answer") == "answer" else None,
            },
        }
    elif kind in {"recovery_action", "stale_recovery_action"}:
        action = _action(previous, step)
        if action is None:
            return 422, {}, {"runner_error": "matching_recovery_action_missing"}
        recovery = previous.get("recovery") or {}
        interaction_id = recovery.get("interaction_id")
        if kind == "stale_recovery_action":
            interaction_id = "stale-" + str(interaction_id or "interaction")
        content = action.get("label", "执行恢复操作")
        payload = {
            "request_id": request_id,
            "content": content,
            "recovery_action": {
                "interaction_id": interaction_id,
                "action_id": action.get("action_id"),
                "request_revision": action.get("request_revision"),
                "plan_version_id": action.get("plan_version_id"),
                "field_value": step.get("field_value"),
            },
        }
        if payload["recovery_action"]["field_value"] is None:
            payload["recovery_action"].pop("field_value")
    elif kind == "context_patch":
        method = "PATCH"
        path = f"/api/sessions/{session_id}/planning-context"
        patch = dict(step["patch"])
        current = previous.get("planning_context") or {}
        patch["base_revision"] = current.get("request_revision", 0)
        payload = {"request_id": request_id, "patch": patch}
    elif kind == "replan":
        path = f"/api/sessions/{session_id}/planning-context/replan"
        payload = {"request_id": request_id}
    elif kind == "select_plan":
        view = _session_view(client, session_id)
        plans = view.get("plans") or []
        strategy = step.get("strategy", "first")
        selected = next(
            (plan for plan in plans if strategy == "first"), None
        ) if strategy == "first" else next(
            (plan for plan in plans if plan.get("plan_id") == view.get("selected_plan_id")), None
        )
        if strategy == "second":
            selected = plans[1] if len(plans) > 1 else None
        if strategy == "recommended":
            advice = previous.get("recommendation_advice") or {}
            selected = next(
                (plan for plan in plans if plan.get("plan_id") == advice.get("recommended_plan_id")),
                None,
            )
        if selected is None:
            return 422, {}, {"runner_error": "plan_selection_target_missing"}
        response = client.post(
            f"/api/sessions/{session_id}/plans/{selected['plan_id']}/select",
            headers=headers,
        )
        return _response_data(response)[0], _response_data(response)[1], {
            "kind": kind,
            "plan_id": selected.get("plan_id"),
        }
    elif kind == "replace_stop":
        view = _session_view(client, session_id)
        selected_id = view.get("selected_plan_id")
        selected_plan = next(
            (plan for plan in view.get("plans", []) if plan.get("plan_id") == selected_id),
            None,
        )
        stop_index = step.get("stop_index")
        if selected_plan is None or not isinstance(stop_index, int):
            return 422, {}, {"runner_error": "selected_plan_or_stop_missing"}
        stops = selected_plan.get("stops") or []
        if not 0 <= stop_index < len(stops):
            return 422, {}, {"runner_error": "stop_index_out_of_range"}
        target = stops[stop_index]
        criteria = []
        if step.get("preset") == "shorter_travel":
            criteria.append({"kind": "route_objective", "metric": "total_route_distance", "direction": "decrease", "strength": "required"})
        elif step.get("user_input"):
            criteria.append({"kind": "semantic", "text": step["user_input"], "strength": "preferred"})
        content = step.get("user_input", "替换这一站")
        payload = {
            "request_id": request_id,
            "content": content,
            "conversation_command": {
                "operation": "replace",
                "base_plan_version_id": view.get("active_plan_version_id"),
                "base_plan_id": selected_id,
                "target": {
                    "stop_index": stop_index,
                    "resource_id": target.get("resource_id"),
                    "role": target.get("role"),
                    "raw_text": target.get("name") or f"第 {stop_index + 1} 站",
                },
                "replacement_criteria": criteria,
            },
        }
    else:
        return 422, {}, {"runner_error": "unsupported_step"}

    response = client.patch(path, headers=headers, json=payload) if method == "PATCH" else client.post(path, headers=headers, json=payload)
    status_code, data = _response_data(response)
    meta = {"kind": kind, "request_id": request_id}
    if kind in {"recovery_action", "stale_recovery_action"} and action is not None:
        meta["selected_recovery_action"] = _safe_recovery_action(action)
    return status_code, data, meta


def _plan_stats(
    data: dict[str, Any],
    *,
    runtime_decisions: list[dict[str, Any]] | None = None,
    search_traces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    plans = data.get("plans") or []
    stop_counts = [len(item.get("stops") or []) for item in plans]
    durations = [int(item.get("total_duration_minutes") or 0) for item in plans]
    schedule_spans: list[int | None] = []
    scheduled_periods: list[list[str]] = []
    role_sequences = [
        [str(stop.get("role", "")).lower() for stop in item.get("stops") or []]
        for item in plans
    ]
    for plan in plans:
        stop_times = [
            (_clock_minutes(stop.get("start")), _clock_minutes(stop.get("end")))
            for stop in (plan.get("stops") or [])
        ]
        valid_times = [
            (start, end)
            for start, end in stop_times
            if start is not None and end is not None and end >= start
        ]
        schedule_spans.append(
            max((end for _, end in valid_times), default=0)
            - min((start for start, _ in valid_times), default=0)
            if valid_times
            else None
        )
        periods = []
        for period, period_start, period_end in (
            ("morning", 9 * 60, 12 * 60),
            ("afternoon", 12 * 60, 18 * 60),
            ("evening", 18 * 60, 21 * 60),
        ):
            if any(start < period_end and end > period_start for start, end in valid_times):
                periods.append(period)
        scheduled_periods.append(periods)
    meaningful_stop_counts = [
        sum(role != "break" for role in roles)
        for roles in role_sequences
    ]
    context = data.get("planning_context") or {}
    when = context.get("when") or {}
    party = ((context.get("who") or {}).get("value") or {})
    intent = (data.get("planning_intent_decision") or {}).get("intent") or {}
    semantic = intent.get("semantic_request") or {}
    objectives = [
        item.get("kind") for item in semantic.get("objectives", [])
        if isinstance(item, dict) and item.get("kind")
    ]
    time_coverage = intent.get("time_coverage") or {}
    runtime = runtime_decisions if runtime_decisions is not None else data.get("runtime_decisions") or []
    traces = search_traces if search_traces is not None else data.get("search_traces") or []
    trace_events = ((data.get("run_trace") or {}).get("events") or [])
    recovery_reason = ((data.get("recovery") or {}).get("reason") or {})
    return {
        "status": data.get("status"),
        "question_field": (data.get("question") or {}).get("field"),
        "recovery_kind": ((data.get("recovery") or {}).get("reason") or {}).get("kind"),
        "recovery_code": ((data.get("recovery") or {}).get("reason") or {}).get("code"),
        "recovery_diagnostics": _safe_recovery_diagnostics(recovery_reason),
        "recovery_actions": [
            _safe_recovery_action(item)
            for item in (data.get("recovery") or {}).get("actions", [])
        ],
        "recovery_action_kinds": [
            item.get("kind") for item in (data.get("recovery") or {}).get("actions", [])
        ],
        "recovery_resolution": data.get("recovery_resolution"),
        "plan_count": len(plans),
        "objectives": objectives,
        "time_coverage_periods": [
            str(item).lower() for item in time_coverage.get("target_periods", [])
        ],
        "router_attempts": sum(
            int(item.get("attempts") or 0)
            for item in runtime
            if item.get("stage") == "turn_interpreter" and item.get("model_invoked")
        ),
        "stop_counts": stop_counts,
        "meaningful_stop_counts": meaningful_stop_counts,
        "durations_minutes": durations,
        "schedule_spans_minutes": schedule_spans,
        "scheduled_periods": scheduled_periods,
        "role_sequences": role_sequences,
        "planning_context": {
            "request_revision": context.get("request_revision"),
            "date": ((when.get("date") or {}).get("value")),
            "start_at": ((when.get("start_at") or {}).get("value")),
            "end_at": ((when.get("end_at") or {}).get("value")),
            "where": ((context.get("where") or {}).get("display_value")),
            "party": party,
        },
        "runtime_decisions": [
            {
                "stage": item.get("stage"),
                "model_invoked": bool(item.get("model_invoked")),
                "attempts": int(item.get("attempts") or 0),
                "latency_ms": item.get("latency_ms"),
                "input_tokens": item.get("input_tokens"),
                "output_tokens": item.get("output_tokens"),
                "fallback_reason": item.get("fallback_reason"),
                "candidate_count": item.get("candidate_count"),
            }
            for item in (data.get("runtime_decisions") or [])
        ],
        "trace_events": [
            {
                "stage": item.get("stage"),
                "status": item.get("status"),
                "public_details": _safe_trace_public_details(item),
            }
            for item in trace_events
        ],
        "search_traces": [_safe_search_trace(item) for item in traces],
        "trace_run_id": (data.get("run_trace") or {}).get("run_id"),
        "trace_stage_starts": {
            stage: sum(
                item.get("stage") == stage and item.get("status") == "started"
                for item in trace_events
            )
            for stage in sorted({item.get("stage") for item in trace_events if item.get("stage")})
        },
        "route_provider_requests": sum(
            int(item.get("route_provider_requests") or 0)
            for item in traces
        ),
    }


def _clock_minutes(value: Any) -> int | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def _matches(
    data: dict[str, Any],
    expected: dict[str, Any],
    view: dict[str, Any],
    *,
    runtime_decisions: list[dict[str, Any]] | None = None,
    failures: list[str] | None = None,
) -> bool:
    actual = _plan_stats(data, runtime_decisions=runtime_decisions)
    matched = True

    def require(condition: bool, assertion: str) -> None:
        nonlocal matched
        if not condition:
            matched = False
            if failures is not None:
                failures.append(assertion)

    if "status" in expected:
        require(actual["status"] == expected["status"], "status")
    if "status_any" in expected:
        require(actual["status"] in expected["status_any"], "status_any")
    if "question_field" in expected:
        require(actual["question_field"] == expected["question_field"], "question_field")
    if "recovery_code" in expected:
        require(actual["recovery_code"] == expected["recovery_code"], "recovery_code")
    if "recovery_resolution" in expected:
        require(
            actual["recovery_resolution"] == expected["recovery_resolution"],
            "recovery_resolution",
        )
    require(
        len(data.get("plans") or []) >= int(expected.get("minimum_plans", 0)),
        "minimum_plans",
    )
    if actual["status"] == "completed":
        require(
            len(data.get("plans") or [])
            >= int(expected.get("minimum_plans_when_completed", 0)),
            "minimum_plans_when_completed",
        )
    if expected.get("no_plans"):
        require(not data.get("plans"), "no_plans")
    if (
        actual["status"] == "completed"
        and expected.get("stop_count_when_completed") is not None
    ):
        require(
            expected["stop_count_when_completed"] in actual["stop_counts"],
            "stop_count_when_completed",
        )
    if expected.get("stop_count") is not None:
        require(expected["stop_count"] in actual["stop_counts"], "stop_count")
    if expected.get("minimum_stops") is not None:
        require(
            any(count >= expected["minimum_stops"] for count in actual["stop_counts"]),
            "minimum_stops",
        )
    if expected.get("minimum_duration_minutes") is not None:
        require(
            any(
                value >= expected["minimum_duration_minutes"]
                for value in actual["durations_minutes"]
            ),
            "minimum_duration_minutes",
        )
    if expected.get("minimum_schedule_span_minutes") is not None:
        require(
            any(
                value is not None and value >= expected["minimum_schedule_span_minutes"]
                for value in actual["schedule_spans_minutes"]
            ),
            "minimum_schedule_span_minutes",
        )
    if expected.get("required_roles") is not None:
        require(
            any(
                _contains_ordered(sequence, expected["required_roles"])
                for sequence in actual["role_sequences"]
            ),
            "required_roles",
        )
    role_groups = expected.get("required_role_groups") or []
    if role_groups:
        require(
            any(
                all(any(role in sequence for role in group) for group in role_groups)
                for sequence in actual["role_sequences"]
            ),
            "required_role_groups",
        )
    if expected.get("required_objectives") is not None:
        require(
            set(expected["required_objectives"]).issubset(actual["objectives"]),
            "required_objectives",
        )
    if expected.get("required_time_coverage_periods") is not None:
        require(
            set(expected["required_time_coverage_periods"]).issubset(
                actual["time_coverage_periods"]
            ),
            "required_time_coverage_periods",
        )
    if expected.get("required_scheduled_periods") is not None:
        require(
            any(
                set(expected["required_scheduled_periods"]).issubset(periods)
                for periods in actual["scheduled_periods"]
            ),
            "required_scheduled_periods",
        )
    if expected.get("required_party_values"):
        for field, value in expected["required_party_values"].items():
            require(
                actual["planning_context"]["party"].get(field) == value,
                f"required_party_values.{field}",
            )
    if "router_attempts" in expected:
        require(actual["router_attempts"] == expected["router_attempts"], "router_attempts")
    if expected.get("forbid_recovery_action_kinds"):
        require(
            not (
                set(expected["forbid_recovery_action_kinds"])
                & set(actual["recovery_action_kinds"])
            ),
            "forbid_recovery_action_kinds",
        )
    if expected.get("required_recovery_action_kinds"):
        require(
            set(expected["required_recovery_action_kinds"]).issubset(
                actual["recovery_action_kinds"]
            ),
            "required_recovery_action_kinds",
        )
    if expected.get("required_recovery_action_fields"):
        available_fields = {
            field
            for action in actual["recovery_actions"]
            for field in ([action.get("field")] + action["patch_fields"])
            if field
        }
        require(
            set(expected["required_recovery_action_fields"]).issubset(available_fields),
            "required_recovery_action_fields",
        )
    if expected.get("required_constraint_values"):
        values = {
            item.get("field"): item.get("value")
            for item in data.get("constraint_summary", [])
            if isinstance(item, dict)
        }
        for field, value in expected["required_constraint_values"].items():
            require(values.get(field) == value, f"required_constraint_values.{field}")
    if expected.get("different_constraint_values"):
        for field, value in expected["different_constraint_values"].items():
            require(
                _request_constraint_value(view, field) != value,
                f"different_constraint_values.{field}",
            )
    for field in ("date", "start_at", "end_at"):
        if field in expected:
            require(
                actual["planning_context"][field] == expected[field],
                f"planning_context.{field}",
            )
    if expected.get("preserve_active_plan_version"):
        require(bool(view.get("active_plan_version_id")), "preserve_active_plan_version")
    for stage, count in expected.get("minimum_trace_stage_starts", {}).items():
        require(
            actual["trace_stage_starts"].get(stage, 0) >= int(count),
            f"minimum_trace_stage_starts.{stage}",
        )
    if expected.get("absent_constraint_fields"):
        for field in expected["absent_constraint_fields"]:
            require(
                _request_constraint_value(view, field) is None,
                f"absent_constraint_fields.{field}",
            )
    if actual["status"] == "needs_recovery":
        if expected.get("preserve_active_plan_version_when_recovering"):
            require(
                bool(view.get("active_plan_version_id")),
                "preserve_active_plan_version_when_recovering",
            )
        if expected.get("required_recovery_action_kinds_when_recovering"):
            require(
                set(expected["required_recovery_action_kinds_when_recovering"]).issubset(
                    actual["recovery_action_kinds"]
                ),
                "required_recovery_action_kinds_when_recovering",
            )
    return matched


def _snapshot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict) and value:
            result: dict[str, Any] = {}
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                result.update(flatten(child, path))
            return result
        return {prefix: value} if prefix else {}

    before_values = flatten(before)
    after_values = flatten(after)
    return {
        field: {"before": before_values.get(field), "after": after_values.get(field)}
        for field in sorted(before_values.keys() | after_values.keys())
        if before_values.get(field) != after_values.get(field)
    }


def _contains_ordered(actual: list[str], expected: list[str]) -> bool:
    cursor = 0
    for item in actual:
        if cursor < len(expected) and item == expected[cursor]:
            cursor += 1
    return cursor == len(expected)


def _without_constraint_fields(
    snapshot: dict[str, Any], excluded_fields: list[str]
) -> dict[str, Any]:
    result = json.loads(json.dumps(snapshot))
    for field in excluded_fields:
        parts = field.split(".")
        current: Any = result
        for part in parts[:-1]:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if isinstance(current, dict):
            current.pop(parts[-1], None)
    return result


def _replacement_diff_invariants(
    response: dict[str, Any],
    before_stop_ids: list[str | None] | None,
    target_index: int | None,
) -> tuple[bool | None, bool | None]:
    """Check returned replacement plans rather than session selection state.

    A successful replacement returns a new candidate PlanVersion but does not
    implicitly select one of its plan cards. ``PlanDiff`` is the response
    contract for target and locked non-target stop identities.
    """

    if before_stop_ids is None or target_index is None:
        return None, None
    plans_by_id = {
        item.get("plan_id"): item for item in response.get("plans", [])
    }
    diffs = list(response.get("plan_diffs") or [])
    if not diffs and response.get("plan_diff"):
        diffs = [response["plan_diff"]]
    non_target_results: list[bool] = []
    target_results: list[bool] = []
    for diff in diffs:
        plan = plans_by_id.get(diff.get("new_plan_id"))
        if plan is None:
            continue
        stops = plan.get("stops") or []
        replacements = diff.get("replacements") or []
        target = next(
            (item for item in replacements if item.get("stop_index") == target_index),
            None,
        )
        if target is None or not 0 <= target_index < len(stops):
            continue
        final_ids = [item.get("resource_id") for item in stops]
        non_target_results.append(
            len(final_ids) == len(before_stop_ids)
            and all(
                before_id == after_id
                for index, (before_id, after_id) in enumerate(
                    zip(before_stop_ids, final_ids)
                )
                if index != target_index
            )
            and all(
                0 <= int(item.get("stop_index", -1)) < len(final_ids)
                and final_ids[int(item["stop_index"])] == item.get("resource_id")
                for item in (diff.get("locked_stops") or [])
            )
        )
        target_results.append(
            target.get("before_resource_id") != target.get("after_resource_id")
            and final_ids[target_index] == target.get("after_resource_id")
            and target.get("before_resource_id") == before_stop_ids[target_index]
        )
    if not non_target_results:
        return False, False
    return any(non_target_results), any(target_results)


def _clarification_resume_succeeded(transcript: list[dict[str, Any]]) -> bool:
    """Measure the answer/resume turn, not unrelated later interactions."""

    return any(
        item.get("kind") == "answer_question"
        and item.get("execution_status") == "executed"
        and item.get("assertions_passed") is True
        and item.get("status") == "completed"
        and int(item.get("plan_count") or 0) > 0
        for item in transcript
    )


def _run_case(app: Any, case: dict[str, Any], *, label_status: str) -> dict[str, Any]:
    started = time.perf_counter()
    transcript: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    active_before_replacement: str | None = None
    constraints_before_replacement: dict[str, Any] | None = None
    constraints_before_update: dict[str, Any] | None = None
    replacement_stop_ids_before: list[str | None] | None = None
    replacement_target_index: int | None = None
    target_stop_changed: bool | None = None
    repeated_question_fields: list[str] = []
    observed_question_fields: list[str] = []
    recovery_steps = 0
    recovery_steps_ok = 0
    stale_action_steps = 0
    stale_action_steps_rejected = 0
    executed_steps = 0
    not_exercised_steps: list[dict[str, Any]] = []
    application_http_errors = 0
    application_exception_types: list[str] = []
    step_expectations_passed = True
    first_policy_passed: bool | None = None
    first_policy_failures: list[str] = []
    final_assertion_failures: list[str] = []
    runner_error: str | None = None
    flow_status = "completed"
    runtime_snapshot: list[dict[str, Any]] = []

    with TestClient(app) as client:
        created = client.post("/api/sessions", headers=_headers())
        if created.status_code != 201:
            return {"case_id": case["case_id"], "executed": False, "runner_error": "session_creation_failed"}
        session_id = created.json()["data"]["session_id"]
        for index, step in enumerate(case["steps"]):
            try:
                missing_precondition = _step_precondition(
                    client, session_id, step, current
                )
            except Exception as exc:
                exception_type = type(exc).__name__
                application_exception_types.append(exception_type)
                flow_status = "application_exception"
                transcript.append(
                    {
                        "step": index,
                        "kind": step["kind"],
                        "execution_status": "application_exception",
                        "exception_type": exception_type,
                        "assertions_passed": False,
                    }
                )
                break
            if missing_precondition is not None:
                not_exercised_steps.append(
                    {
                        "step": index,
                        "kind": step["kind"],
                        "reason": missing_precondition,
                        **(
                            {
                                "available_recovery_actions": _plan_stats(current).get(
                                    "recovery_actions", []
                                )
                            }
                            if missing_precondition == "matching_recovery_action_missing"
                            else {}
                        ),
                    }
                )
                transcript.append(
                    {
                        "step": index,
                        "kind": step["kind"],
                        "execution_status": "not_exercised",
                        "precondition": missing_precondition,
                        "assertions_passed": None,
                    }
                )
                flow_status = "not_exercised_precondition"
                break
            constraint_snapshot_before_step = _diagnostic_constraint_snapshot(
                _session_view(client, session_id)
            )
            if step["kind"] == "replace_stop":
                before = _session_view(client, session_id)
                active_before_replacement = before.get("active_plan_version_id")
                constraints_before_replacement = _constraint_snapshot(before)
                selected_id = before.get("selected_plan_id")
                selected_plan = next(
                    (
                        item for item in before.get("plans", [])
                        if item.get("plan_id") == selected_id
                    ),
                    None,
                )
                replacement_stop_ids_before = [
                    item.get("resource_id") for item in (selected_plan or {}).get("stops", [])
                ]
                replacement_target_index = step.get("stop_index")
            elif (
                constraints_before_update is None
                and (
                    step["kind"] == "context_patch"
                    or (step["kind"] == "message" and index > 0 and current.get("status") == "completed")
                )
            ):
                constraints_before_update = _constraint_snapshot(
                    _session_view(client, session_id)
                )
            try:
                status_code, data, step_meta = _perform_step(
                    client, session_id, case["case_id"], index, step, current
                )
            except Exception as exc:
                exception_type = type(exc).__name__
                application_exception_types.append(exception_type)
                flow_status = "application_exception"
                transcript.append(
                    {
                        "step": index,
                        "kind": step["kind"],
                        "execution_status": "application_exception",
                        "exception_type": exception_type,
                        "assertions_passed": False,
                    }
                )
                break
            executed_steps += 1
            if step_meta.get("runner_error"):
                runner_error = step_meta["runner_error"]
                flow_status = "runner_error"
                break
            step_expected = step.get(
                "expect_http_status",
                409 if step["kind"] == "stale_recovery_action" else 200,
            )
            http_passed = status_code == step_expected
            if step["kind"] == "recovery_action":
                recovery_steps += 1
                recovery_steps_ok += int(http_passed)
            elif step["kind"] == "stale_recovery_action":
                stale_action_steps += 1
                stale_action_steps_rejected += int(status_code == 409)
            current_runtime = data.get("runtime_decisions") or []
            current_search = data.get("search_traces") or []
            runtime_delta = _snapshot_delta(runtime_snapshot, current_runtime)
            response_events = ((data.get("run_trace") or {}).get("events") or [])
            planning_ran = any(
                item.get("stage") in {"structure", "retrieve", "construct", "verify"}
                and item.get("status") == "started"
                for item in response_events
            )
            search_delta = current_search if planning_ran else []
            safe = _plan_stats(
                data,
                runtime_decisions=runtime_delta,
                search_traces=search_delta,
            )
            constraint_snapshot_after_step = _diagnostic_constraint_snapshot(
                _session_view(client, session_id)
            )
            safe.update({"step": index, "kind": step["kind"], "http_status": status_code})
            safe["request_constraints_before"] = constraint_snapshot_before_step
            safe["request_constraints_after"] = constraint_snapshot_after_step
            safe["request_constraint_changes"] = _snapshot_diff(
                constraint_snapshot_before_step,
                constraint_snapshot_after_step,
            )
            if step_meta.get("selected_recovery_action") is not None:
                safe["selected_recovery_action"] = step_meta["selected_recovery_action"]
            safe["execution_status"] = "executed"
            safe["assertions_passed"] = http_passed
            step_failures: list[str] = []
            if not http_passed:
                step_failures.append("http_status")
            if "expect" in step and status_code == 200:
                matches_step = _matches(
                    data,
                    step["expect"],
                    {},
                    runtime_decisions=runtime_delta,
                    failures=step_failures,
                )
                safe["assertions_passed"] = http_passed and matches_step
            safe["assertion_failures"] = step_failures
            step_expectations_passed = step_expectations_passed and bool(safe["assertions_passed"])
            transcript.append(safe)
            if status_code == 200:
                current = data
                runtime_snapshot = current_runtime
                question_field = (data.get("question") or {}).get("field")
                if question_field:
                    if question_field in observed_question_fields:
                        repeated_question_fields.append(question_field)
                    observed_question_fields.append(question_field)
            if index == 0:
                first_policy_passed = _matches(
                    data,
                    case.get("expected", {}).get("first", {}),
                    {},
                    failures=first_policy_failures,
                )
            if status_code >= 500:
                application_http_errors += 1
                flow_status = "application_http_error"
                break
            if not http_passed and status_code >= 400:
                flow_status = "unexpected_http_status"
                break

        final_view = _session_view(client, session_id)

    expected_final = case.get("expected", {}).get("final", {})
    path_completed = not not_exercised_steps and flow_status == "completed"
    final_matches = _matches(
        current,
        expected_final,
        final_view,
        failures=final_assertion_failures,
    )
    if not path_completed:
        final_assertion_failures.append(f"path_not_completed:{flow_status}")
    final_assertion_failures.extend(
        f"step_{row.get('step')}:assertions_failed"
        for row in transcript
        if row.get("assertions_passed") is False
    )
    final_passed = (
        path_completed
        and step_expectations_passed
        and final_matches
    )
    minimum_meaningful_increase = expected_final.get(
        "minimum_meaningful_stop_increase_over_initial_max"
    )
    if minimum_meaningful_increase is not None:
        initial_counts = (
            transcript[0].get("meaningful_stop_counts", []) if transcript else []
        )
        final_counts = _plan_stats(current).get("meaningful_stop_counts", [])
        required_count = (max(initial_counts) if initial_counts else 0) + int(
            minimum_meaningful_increase
        )
        final_passed = final_passed and any(
            count >= required_count for count in final_counts
        )
        if not any(count >= required_count for count in final_counts):
            final_assertion_failures.append("minimum_meaningful_stop_increase_over_initial_max")
    preserved_version: bool | None = None
    advanced_version: bool | None = None
    constraints_preserved: bool | None = None
    if active_before_replacement is not None:
        if expected_final.get("preserve_active_plan_version") or (
            expected_final.get("preserve_active_plan_version_when_recovering")
            and current.get("status") == "needs_recovery"
        ):
            preserved_version = final_view.get("active_plan_version_id") == active_before_replacement
            final_passed = final_passed and preserved_version
            if not preserved_version:
                final_assertion_failures.append("preserve_active_plan_version")
        elif (
            expected_final.get("new_active_plan_version_when_completed")
            and current.get("status") == "completed"
        ):
            advanced_version = (
                bool(final_view.get("active_plan_version_id"))
                and final_view.get("active_plan_version_id") != active_before_replacement
            )
            final_passed = final_passed and advanced_version
            if not advanced_version:
                final_assertion_failures.append("new_active_plan_version_when_completed")
    if constraints_before_replacement is not None:
        constraints_preserved = _constraint_snapshot(final_view) == constraints_before_replacement
    if expected_final.get("preserve_all_constraints"):
        constraints_preserved = (
            constraints_before_replacement is not None
            and _constraint_snapshot(final_view) == constraints_before_replacement
        )
        final_passed = final_passed and constraints_preserved
        if not constraints_preserved:
            final_assertion_failures.append("preserve_all_constraints")
    if expected_final.get("preserve_constraints_except"):
        constraints_preserved = (
            constraints_before_update is not None
            and _without_constraint_fields(
                constraints_before_update,
                expected_final["preserve_constraints_except"],
            )
            == _without_constraint_fields(
                _constraint_snapshot(final_view),
                expected_final["preserve_constraints_except"],
            )
        )
        final_passed = final_passed and constraints_preserved
        if not constraints_preserved:
            final_assertion_failures.append("preserve_constraints_except")
    non_target_stops_preserved: bool | None = None
    if expected_final.get("preserve_non_target_stops") and current.get("status") == "completed":
        non_target_stops_preserved, target_stop_changed = _replacement_diff_invariants(
            current,
            replacement_stop_ids_before,
            replacement_target_index,
        )
        final_passed = final_passed and bool(non_target_stops_preserved)
        if not non_target_stops_preserved:
            final_assertion_failures.append("preserve_non_target_stops")
        if expected_final.get("target_stop_changed"):
            final_passed = final_passed and bool(target_stop_changed)
            if not target_stop_changed:
                final_assertion_failures.append("target_stop_changed")
    if expected_final.get("target_stop_changed") and current.get("status") == "completed":
        if target_stop_changed is None:
            _, target_stop_changed = _replacement_diff_invariants(
                current,
                replacement_stop_ids_before,
                replacement_target_index,
            )
            final_passed = final_passed and bool(target_stop_changed)
            if not target_stop_changed:
                final_assertion_failures.append("target_stop_changed")
    constraint_snapshot_before = (
        constraints_before_update
        if constraints_before_update is not None
        else constraints_before_replacement
    )
    constraint_changes = (
        _snapshot_diff(constraint_snapshot_before, _constraint_snapshot(final_view))
        if constraint_snapshot_before is not None
        else {}
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    counts_as_task_success = bool(case.get("counts_as_task_success", True))
    return {
        "case_id": case["case_id"],
        "label_status": label_status,
        "first_policy_passed": first_policy_passed,
        "first_policy_failures": first_policy_failures,
        "expected_interaction_outcome_success": final_passed,
        "final_assertion_failures": final_assertion_failures,
        "counts_as_task_success": counts_as_task_success,
        "eventual_task_success": final_passed and counts_as_task_success,
        "clarification_attempted": any(row.get("kind") == "answer_question" and row.get("execution_status") == "executed" for row in transcript),
        "clarification_recovered": _clarification_resume_succeeded(transcript),
        "recovery_action_steps": recovery_steps,
        "recovery_action_steps_http_success": recovery_steps_ok,
        "stale_action_steps": stale_action_steps,
        "stale_action_steps_rejected": stale_action_steps_rejected,
        "repeated_question_fields": repeated_question_fields,
        "question_fields": observed_question_fields,
        "scripted_step_count": len(case["steps"]),
        "interaction_count": executed_steps,
        "path_completed": path_completed,
        "flow_status": flow_status,
        "not_exercised_steps": not_exercised_steps,
        "application_http_errors": application_http_errors,
        "application_exception_types": application_exception_types,
        "active_plan_version_preserved": preserved_version,
        "active_plan_version_advanced": advanced_version,
        "unchanged_constraints_preserved": constraints_preserved,
        "constraint_change_fields": sorted(constraint_changes),
        "non_target_stops_preserved": non_target_stops_preserved,
        "target_stop_changed": target_stop_changed,
        "elapsed_ms": elapsed_ms,
        "runner_error": runner_error,
        "input_tokens": sum(
            int(decision.get("input_tokens") or 0)
            for row in transcript for decision in row.get("runtime_decisions", [])
        ),
        "output_tokens": sum(
            int(decision.get("output_tokens") or 0)
            for row in transcript for decision in row.get("runtime_decisions", [])
        ),
        "router_attempts": sum(
            int(decision.get("attempts", 0))
            for row in transcript for decision in row.get("runtime_decisions", [])
            if decision.get("stage") == "turn_interpreter" and decision.get("model_invoked")
        ),
        "planning_intent_attempts": sum(
            int(decision.get("attempts", 0))
            for row in transcript for decision in row.get("runtime_decisions", [])
            if decision.get("stage") == "planning_intent" and decision.get("model_invoked")
        ),
        "advisor_attempts": sum(
            int(decision.get("attempts", 0))
            for row in transcript for decision in row.get("runtime_decisions", [])
            if decision.get("stage") == "recommendation_advisor" and decision.get("model_invoked")
        ),
        "route_provider_requests": sum(
            int(row.get("route_provider_requests", 0)) for row in transcript
        ),
        "planning_construction_runs": sum(
            item.get("stage") == "construct" and item.get("status") == "started"
            for row in transcript for item in row.get("trace_events", [])
        ),
        "transcript": transcript,
        "final_session": {
            "active_plan_version_id": final_view.get("active_plan_version_id"),
            "selected_plan_id": final_view.get("selected_plan_id"),
            "plan_count": len(final_view.get("plans") or []),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--variant", default="offline_sanity")
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--max-model-calls", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = load_suite(args.suite.resolve())
    if suite.get("review_status", "").startswith("draft") and not args.allow_draft:
        parser.error("suite labels are draft; pass --allow-draft for exploratory execution")
    variant = load_evaluation_variant(args.variant)
    if variant.router_mode == "frozen":
        parser.error("S-RX uses live or Demo Router variants; frozen variants require per-turn fixture wiring")
    uses_live_model = (
        variant.router_mode == "llm"
        or variant.planning_intent_mode == "llm"
        or variant.advisor_mode == "llm"
    )
    if uses_live_model and not args.allow_llm:
        parser.error("pass --allow-llm to send requests to live model components")
    if uses_live_model and args.max_model_calls is None:
        parser.error("live-model runs require --max-model-calls as a conservative budget")
    seeds = load_resume_release_dataset(args.seeds.resolve())
    seed_by_id = {item.case_id: item for item in seeds.cases}
    selected = [
        item for item in suite["cases"]
        if args.case_ids is None or item["case_id"] in set(args.case_ids)
    ]
    if not selected:
        parser.error("no matching cases selected")
    estimated_budget = sum(_estimate_max_calls(item, variant) for item in selected)
    if args.max_model_calls is not None and estimated_budget > args.max_model_calls:
        parser.error(
            f"conservative upper bound is {estimated_budget} model attempts; "
            f"select fewer cases or raise --max-model-calls"
        )
    if uses_live_model:
        try:
            _load_live_model_environment()
        except RuntimeError as error:
            parser.error(str(error))
    dependencies = _build_evaluation_dependencies(variant)
    temp_root = ROOT / "artifacts" / "evals"
    temp_root.mkdir(parents=True, exist_ok=True)
    results = []
    for case in selected:
        seed = seed_by_id.get(case.get("seed_case_id"))
        if seed is None:
            results.append({"case_id": case["case_id"], "executed": False, "runner_error": "unknown_seed_case"})
            continue
        case_fixtures = {**seed.fixtures.model_dump(), **case.get("fixtures", {})}
        allowlisted_resources = set(case_fixtures.get("catalog_resource_allowlist", []))
        case_dependencies = (
            replace(
                dependencies,
                catalog=_ResourceAllowlistCatalog(
                    dependencies.catalog,
                    allowlisted_resources,
                ),
            )
            if allowlisted_resources
            else dependencies
        )
        with tempfile.TemporaryDirectory(prefix="hft-s-rx-", dir=temp_root) as temp_dir:
            app, _ = _build_evaluation_app(
                Path(temp_dir) / "case.db",
                SimpleNamespace(
                    case_id=case["case_id"],
                    fixtures=SimpleNamespace(**case_fixtures),
                ),
                variant,
                datetime.fromisoformat(suite["evaluation_clock"]),
                dependencies=case_dependencies,
            )
            results.append(_run_case(app, case, label_status=suite["review_status"]))

    executed = [item for item in results if item.get("executed", True)]
    first_checked = [item for item in executed if item.get("first_policy_passed") is not None]
    asked = [item for item in executed if item.get("clarification_attempted")]
    complete_paths = [item for item in executed if item.get("path_completed")]
    task_success_cases = [item for item in executed if item.get("counts_as_task_success", True)]
    non_task_terminal_cases = [item for item in executed if not item.get("counts_as_task_success", True)]
    git_dirty = bool(_git_output("status", "--porcelain"))
    report = {
        "schema_version": "s-rx-multiturn-report.v1",
        "suite": suite["suite"],
        "review_status": suite["review_status"],
        "is_publishable_baseline": _is_publishable_baseline(
            review_status=suite["review_status"],
            selected_case_count=len(selected),
            suite_case_count=len(suite["cases"]),
            executed_case_count=len(executed),
            git_dirty=git_dirty,
        ),
        "variant": variant.variant_id,
        "case_count": len(selected),
        "executed_count": len(executed),
        "suite_sha256": hashlib.sha256(args.suite.read_bytes()).hexdigest(),
        "seed_dataset_sha256": hashlib.sha256(args.seeds.read_bytes()).hexdigest(),
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_dirty": git_dirty,
        "estimated_model_attempt_upper_bound": estimated_budget,
        "metrics": {
            "first_interaction_policy_accuracy": {
                "numerator": sum(bool(item["first_policy_passed"]) for item in first_checked),
                "denominator": len(first_checked),
            },
            "eventual_task_success": {
                "numerator": sum(bool(item.get("eventual_task_success")) for item in task_success_cases),
                "denominator": len(task_success_cases),
            },
            "non_task_terminal_outcome_success": {
                "numerator": sum(bool(item.get("expected_interaction_outcome_success")) for item in non_task_terminal_cases),
                "denominator": len(non_task_terminal_cases),
            },
            "clarification_recovery_success": {
                "numerator": sum(bool(item.get("clarification_recovered")) for item in asked),
                "denominator": len(asked),
            },
            "recovery_action_http_success": {
                "numerator": sum(int(item.get("recovery_action_steps_http_success", 0)) for item in executed),
                "denominator": sum(int(item.get("recovery_action_steps", 0)) for item in executed),
            },
            "stale_action_rejection": {
                "numerator": sum(int(item.get("stale_action_steps_rejected", 0)) for item in executed),
                "denominator": sum(int(item.get("stale_action_steps", 0)) for item in executed),
            },
            "repeated_question_count": sum(len(item.get("repeated_question_fields", [])) for item in executed),
            "scripted_path_completion": {
                "numerator": len(complete_paths),
                "denominator": len(executed),
            },
            "not_exercised_step_count": sum(
                len(item.get("not_exercised_steps", [])) for item in executed
            ),
            "application_http_error_count": sum(
                int(item.get("application_http_errors", 0)) for item in executed
            ),
            "application_exception_count": sum(
                len(item.get("application_exception_types", [])) for item in executed
            ),
            "average_interaction_count": (
                round(
                    sum(int(item.get("interaction_count", 0)) for item in complete_paths)
                    / len(complete_paths),
                    2,
                )
                if complete_paths
                else None
            ),
            "unchanged_constraints_preserved": {
                "numerator": sum(item.get("unchanged_constraints_preserved") is True for item in executed),
                "denominator": sum(item.get("unchanged_constraints_preserved") is not None for item in executed),
            },
            "failed_modification_plan_version_preserved": {
                "numerator": sum(item.get("active_plan_version_preserved") is True for item in executed),
                "denominator": sum(item.get("active_plan_version_preserved") is not None for item in executed),
            },
            "model_decisions": sum(
                decision.get("model_invoked", False)
                for item in executed for row in item.get("transcript", [])
                for decision in row.get("runtime_decisions", [])
            ),
            "model_provider_attempts": sum(
                int(decision.get("attempts", 0))
                for item in executed for row in item.get("transcript", [])
                for decision in row.get("runtime_decisions", [])
                if decision.get("model_invoked")
            ),
            "calls_by_stage": _calls_by_stage(executed),
            "router_attempts": sum(int(item.get("router_attempts", 0)) for item in executed),
            "planning_intent_attempts": sum(int(item.get("planning_intent_attempts", 0)) for item in executed),
            "advisor_attempts": sum(int(item.get("advisor_attempts", 0)) for item in executed),
            "known_input_tokens": sum(int(item.get("input_tokens", 0)) for item in executed),
            "known_output_tokens": sum(int(item.get("output_tokens", 0)) for item in executed),
            "planning_construction_runs": sum(int(item.get("planning_construction_runs", 0)) for item in executed),
            "route_provider_requests": sum(int(item.get("route_provider_requests", 0)) for item in executed),
        },
        "note": (
            "A reviewed suite is a publishable baseline only when every case executes from a clean working tree; "
            "partial runs are diagnostics, and draft-labeled results are not reviewed baselines. "
            "Dependent steps without their required question/action are not sent; "
            "they are reported as not exercised. Runtime decisions and search traces "
            "are scoped to new runtime-decision suffixes and HTTP responses that ran Planning, "
            "not re-counted from prior turns or context-only edits."
        ),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("runner_error") is None for item in results) else 1


def _calls_by_stage(results: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for result in results:
        for row in result.get("transcript", []):
            for decision in row.get("runtime_decisions", []):
                if not decision.get("model_invoked"):
                    continue
                stage = str(decision.get("stage") or "unknown")
                totals[stage] = totals.get(stage, 0) + int(decision.get("attempts", 0))
    return totals


def _estimate_max_calls(case: dict[str, Any], variant: Any) -> int:
    """Conservative upper bound: at most two provider attempts per model stage."""

    total = 0
    for step in case["steps"]:
        kind = step["kind"]
        if kind == "message":
            total += 2 if variant.router_mode == "llm" else 0
            total += 2 if variant.planning_intent_mode == "llm" else 0
            total += 2 if variant.advisor_mode == "llm" else 0
        elif kind in {"answer_question", "recovery_action", "replan"}:
            total += 2 if variant.planning_intent_mode == "llm" else 0
            total += 2 if variant.advisor_mode == "llm" else 0
        elif kind == "replace_stop":
            total += 2 if variant.advisor_mode == "llm" else 0
    return total


def _git_output(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())

"""Reproducible end-to-end evaluation for the Resume Release slice.

This module is deliberately outside the product graph.  It builds a fresh
``create_app`` instance and isolated SQLite database for every case, while
sharing only stateless/expensive dependencies (model clients and the lazy
retrieval index) within one serial run.  It injects deterministic providers,
runs the public HTTP contract with ``TestClient`` and records assertions
against the returned product data.  It does not tune prompts or alter
business logic when a case fails.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Literal, Sequence

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from app.api.application import create_app
from app.domain.constraints import GeoLocation
from app.domain.providers import (
    AvailabilityCheck,
    AvailabilityFact,
    AvailabilityStatus,
    GeocodeRequest,
    GeocodeResolution,
    GeocodingFact,
    GeoPoint,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteMode,
    RouteRequest,
    WeatherFact,
    WeatherRequest,
)
from app.services.catalog import Catalog, SnapshotCatalog
from app.services.demo_router import DemoRouter
from app.services.demo_world import DemoCatalog, DemoWorld
from app.services.enrichment import EnvironmentContext
from app.services.opening_hours import visit_fits_opening_hours
from app.services.planning_intent import build_default_planning_intent_provider
from app.services.candidate_retriever import build_default_candidate_retriever
from app.services.recommendation_advisor import build_default_recommendation_advisor
from app.services.router_extractor import build_default_turn_interpreter
from evals.resume_release_dataset import (
    EvaluationStep,
    ResumeReleaseCase,
    ResumeReleaseDataset,
    load_resume_release_dataset,
)


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[2] / "evals" / "resume_release_cases.json"
)
DEFAULT_LOCATION = GeoLocation(
    city="北京市",
    district="朝阳区",
    address="北京市朝阳区",
    latitude=39.9219,
    longitude=116.4436,
)

VariantId = Literal[
    "offline_sanity",
    "B0_DOWNSTREAM_RULE",
    "B1_LLM_INTENT",
    "B2_HYBRID_RETRIEVAL",
    "B3_GROUNDED_ADVICE",
]


class _RecordingRouter:
    """Capture safe semantic extraction without changing the product API.

    The evaluator only needs to know which semantic fields reached the
    PlanningIntent seam.  Keeping this probe at the evaluation composition
    root avoids adding debug-only fields to AgentResponse or Graph state.
    """

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.latest_semantic_trace: dict[str, Any] | None = None

    def interpret_with_runtime(self, user_input: str, context: Any):
        method = getattr(self._delegate, "interpret_with_runtime", None)
        if callable(method):
            interpretation, runtime = method(user_input, context)
        else:
            interpretation = self._delegate.interpret(user_input, context)
            runtime = None
        self.latest_semantic_trace = _router_semantic_trace(interpretation)
        if runtime is None:
            from app.domain.runtime import RuntimeDecision

            runtime = RuntimeDecision(
                stage="turn_interpreter",
                adapter="recording",
                model_invoked=False,
                attempts=0,
                latency_ms=None,
            )
        return interpretation, runtime

    def interpret(self, user_input: str, context: Any):
        interpretation = self._delegate.interpret(user_input, context)
        self.latest_semantic_trace = _router_semantic_trace(interpretation)
        return interpretation


@dataclass
class _EvaluationDependencies:
    """Expensive, stateless dependencies shared by one evaluation run.

    The database and graph application remain isolated per case, while model
    clients, the BGE provider/index and catalog are reused.  This mirrors a
    long-lived service process and prevents every case from paying the model
    and embedding cold-start cost again.
    """

    catalog: Catalog
    router_delegate: Any
    planning_intent_provider: Any
    candidate_retriever: Any
    recommendation_advisor: Any
    retrieval_warmed: bool = False


def _router_semantic_trace(interpretation: Any) -> dict[str, Any]:
    """Serialize only semantic extraction fields needed for attribution."""

    raw = interpretation.raw_constraints
    return {
        "primary_intent": interpretation.primary_intent.value,
        "semantic_fields": {
            "preferences": list(raw.preferences),
            "diet_tags": list(raw.diet_tags),
            "scene_tags": list(raw.scene_tags),
            "avoid": list(raw.avoid),
        },
        "evidence_map": dict(interpretation.evidence_map),
    }


def _planning_intent_semantic_trace(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the final objective/query vocabulary seen by the Planner."""

    decision = (data or {}).get("planning_intent_decision") or {}
    intent = decision.get("intent") or {}
    semantic = intent.get("semantic_request") or {}
    return {
        "source": decision.get("source"),
        "objective_kinds": [
            item.get("kind")
            for item in semantic.get("objectives", [])
            if item.get("kind")
        ],
        "queries": [
            {
                "query_id": item.get("query_id"),
                "text": item.get("text"),
                "evidence_refs": item.get("evidence_refs", []),
            }
            for item in semantic.get("queries", [])
        ],
        "evidence_ids": [
            item.get("evidence_id")
            for item in semantic.get("evidence", [])
            if item.get("evidence_id")
        ],
    }


class EvaluationConfigurationError(ValueError):
    """Raised before execution when an evaluation safety gate is not met."""


class EvaluationVariant(BaseModel):
    """One frozen component configuration used by the evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variant_id: VariantId
    router_mode: Literal["demo", "llm"]
    planning_intent_mode: Literal["rule", "llm"]
    retrieval_mode: Literal["rule", "hybrid"]
    advisor_mode: Literal["rule", "llm"]


class EvalAssertion(BaseModel):
    """A single observable assertion, not just a case-level boolean."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str = Field(min_length=1)
    status: Literal[
        "passed",
        "failed",
        "not_applicable",
        "not_observable",
        "not_evaluable",
    ]
    expected: Any = None
    actual: Any = None
    details: str | None = None
    required: bool = True


class EvalFailureDetail(BaseModel):
    """A bounded, safe explanation for one evaluation failure or fallback.

    Evaluation reports are intended to be useful for debugging a pilot, but
    they must not become a second channel for provider payloads or secrets.
    Details are therefore generated from assertion values and safe runtime
    metadata only; raw model responses and exception messages are excluded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "product_failure",
        "model_failure",
        "fixture_failure",
        "runner_failure",
        "label_review_required",
    ]
    code: str = Field(min_length=1, max_length=160)
    stage: str | None = Field(default=None, max_length=80)
    metric: str | None = Field(default=None, max_length=120)
    details: str = Field(default="", max_length=500)


class EvaluationCaseResult(BaseModel):
    """The complete result and safe transcript for one case/repeat."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    variant_id: VariantId
    repeat: int = Field(ge=1)
    label_status: Literal["draft", "reviewed"]
    category: Literal["planning", "clarification", "conflict", "modification"]
    tags: list[str] = Field(default_factory=list)
    executed: bool = True
    task_status: Literal["normal_success", "degraded_but_completed", "failed"]
    task_passed: bool
    actual_outcome: Literal["plan", "question", "conflict", "unknown", "error"]
    assertions: list[EvalAssertion] = Field(default_factory=list)
    runtime_summary: dict[str, Any] = Field(default_factory=dict)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: int = Field(ge=0)
    failure_kinds: list[
        Literal[
            "product_failure",
            "model_failure",
            "fixture_failure",
            "runner_failure",
            "label_review_required",
        ]
    ] = Field(default_factory=list)
    failure_details: list[EvalFailureDetail] = Field(default_factory=list)
    error: str | None = None


class ResumeReleaseEvalReport(BaseModel):
    """Serializable report produced by ``run_resume_release_evaluation``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["resume-release-eval-report.v1"] = (
        "resume-release-eval-report.v1"
    )
    run_id: str
    generated_at: datetime
    git_commit: str
    git_dirty: bool
    dataset_path: str
    dataset_sha256: str
    label_status_counts: dict[str, int]
    variant: EvaluationVariant
    case_count: int = Field(ge=0)
    repeat_count: int = Field(ge=1)
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)
    aggregate: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


_VARIANT_DEFINITIONS: dict[str, EvaluationVariant] = {
    "offline_sanity": EvaluationVariant(
        variant_id="offline_sanity",
        router_mode="demo",
        planning_intent_mode="rule",
        retrieval_mode="rule",
        advisor_mode="rule",
    ),
    "B0_DOWNSTREAM_RULE": EvaluationVariant(
        variant_id="B0_DOWNSTREAM_RULE",
        router_mode="llm",
        planning_intent_mode="rule",
        retrieval_mode="rule",
        advisor_mode="rule",
    ),
    "B1_LLM_INTENT": EvaluationVariant(
        variant_id="B1_LLM_INTENT",
        router_mode="llm",
        planning_intent_mode="llm",
        retrieval_mode="rule",
        advisor_mode="rule",
    ),
    "B2_HYBRID_RETRIEVAL": EvaluationVariant(
        variant_id="B2_HYBRID_RETRIEVAL",
        router_mode="llm",
        planning_intent_mode="llm",
        retrieval_mode="hybrid",
        advisor_mode="rule",
    ),
    "B3_GROUNDED_ADVICE": EvaluationVariant(
        variant_id="B3_GROUNDED_ADVICE",
        router_mode="llm",
        planning_intent_mode="llm",
        retrieval_mode="hybrid",
        advisor_mode="llm",
    ),
}


def load_evaluation_variant(value: str | EvaluationVariant) -> EvaluationVariant:
    """Resolve a frozen variant id without silently changing its components."""

    if isinstance(value, EvaluationVariant):
        return value
    normalized = value.strip()
    if normalized.casefold() == "offline_sanity":
        return _VARIANT_DEFINITIONS["offline_sanity"]
    for key, variant in _VARIANT_DEFINITIONS.items():
        if key.casefold() == normalized.casefold():
            return variant
    allowed = ", ".join(_VARIANT_DEFINITIONS)
    raise EvaluationConfigurationError(
        f"unknown evaluation variant {value!r}; choose one of: {allowed}"
    )


def run_resume_release_evaluation(
    dataset: ResumeReleaseDataset | Path | None = None,
    *,
    variant: str | EvaluationVariant = "offline_sanity",
    case_ids: Sequence[str] | None = None,
    category: str | None = None,
    label_status: Literal["reviewed", "draft", "all"] = "reviewed",
    allow_draft: bool = False,
    allow_llm: bool = False,
    repeats: int = 1,
    max_model_calls: int | None = None,
    output_dir: Path | None = None,
) -> ResumeReleaseEvalReport:
    """Run selected cases through the public HTTP/SQLite application seam.

    ``offline_sanity`` is safe by default.  Real LLM variants require the
    explicit ``allow_llm`` flag and an ``LLM_API`` environment variable.  Draft
    labels likewise require ``allow_draft``; no result can promote a draft to
    reviewed automatically.
    """

    selected_variant = load_evaluation_variant(variant)
    if repeats < 1:
        raise EvaluationConfigurationError("repeats must be at least 1")
    if max_model_calls is not None and max_model_calls < 1:
        raise EvaluationConfigurationError("max_model_calls must be positive")

    dataset_path: Path | None = dataset if isinstance(dataset, Path) else None
    if dataset is None:
        dataset_path = DEFAULT_DATASET_PATH
        loaded_dataset = load_resume_release_dataset(dataset_path)
    elif isinstance(dataset, Path):
        loaded_dataset = load_resume_release_dataset(dataset)
    else:
        loaded_dataset = dataset

    cases = _select_cases(
        loaded_dataset,
        case_ids=case_ids,
        category=category,
        label_status=label_status,
        allow_draft=allow_draft,
    )
    if not cases:
        raise EvaluationConfigurationError("no evaluation cases selected")

    requires_llm = selected_variant.router_mode == "llm" or any(
        mode == "llm"
        for mode in (
            selected_variant.planning_intent_mode,
            selected_variant.advisor_mode,
        )
    )
    if requires_llm and not allow_llm:
        raise EvaluationConfigurationError(
            "real LLM variants require --allow-llm; offline_sanity is the safe default"
        )
    if requires_llm:
        import os
        from dotenv import load_dotenv

        # Match the server's configuration behavior, but only after the caller
        # explicitly opts into a real-model run.  The report never serializes
        # the loaded environment or credentials.
        load_dotenv()

        if not os.getenv("LLM_API"):
            raise EvaluationConfigurationError(
                "LLM_API is required before starting an LLM evaluation"
            )

    clock = loaded_dataset.evaluation_clock
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    # Keep the HTTP application isolated per case, but reuse expensive
    # stateless clients and the lazy BGE/index objects for this serial run.
    # This makes the first successful dense retrieval the only cold start.
    dependencies = _build_evaluation_dependencies(selected_variant)
    results: list[EvaluationCaseResult] = []
    used_model_calls = 0
    for repeat in range(1, repeats + 1):
        for case in cases:
            estimated_calls = _estimate_case_model_calls(case, selected_variant)
            if max_model_calls is not None and (
                used_model_calls >= max_model_calls
                or used_model_calls + estimated_calls > max_model_calls
            ):
                results.append(
                    _runner_failure_result(
                        case,
                        selected_variant,
                        repeat,
                        (
                            "model call budget exhausted before case started "
                            f"(estimated_max={estimated_calls}, remaining="
                            f"{max_model_calls - used_model_calls})"
                        ),
                    )
                )
                continue
            result = _run_case(
                case,
                selected_variant,
                repeat=repeat,
                evaluation_clock=clock,
                max_model_calls=max_model_calls,
                used_model_calls=used_model_calls,
                dependencies=dependencies,
            )
            # The hard budget is a provider-request budget. A single logical
            # decision can use two requests when its bounded format retry runs.
            used_model_calls += int(
                result.runtime_summary.get("provider_attempt_count", 0)
            )
            results.append(result)

    report = ResumeReleaseEvalReport(
        run_id=_run_id(),
        generated_at=datetime.now(timezone.utc),
        git_commit=_git_commit(),
        git_dirty=_git_dirty(),
        dataset_path=str(dataset_path or "<in-memory>"),
        dataset_sha256=_dataset_hash(loaded_dataset, dataset_path),
        label_status_counts={
            "draft": sum(item.label_status == "draft" for item in cases),
            "reviewed": sum(item.label_status == "reviewed" for item in cases),
        },
        variant=selected_variant,
        case_count=len(results),
        repeat_count=repeats,
        case_results=results,
        aggregate=_aggregate_results(results),
        metadata={
            "evaluation_clock": clock.isoformat(),
            "default_location": DEFAULT_LOCATION.model_dump(mode="json"),
            "catalog": "demo_world.v1",
            "providers": {
                "route": "deterministic_replay_fixture",
                "weather": "deterministic_replay_fixture",
                "availability": "deterministic_replay_fixture",
                "geocoding": "deterministic_replay_fixture",
            },
            "draft_results_are_not_resume_grade": any(
                item.label_status == "draft" for item in cases
            ),
            "model_call_budget": max_model_calls,
            # Keep the old key for report readers, but expose both meanings
            # explicitly for new consumers.
            "actual_model_calls": used_model_calls,
            "actual_model_decisions": sum(
                result.runtime_summary.get("model_decision_count", 0)
                for result in results
            ),
            "actual_provider_attempts": used_model_calls,
            "model_names": sorted(
                {
                    decision.get("model_name")
                    for result in results
                    for row in result.transcript
                    for decision in (row.get("runtime_decisions") or [])
                    if decision.get("model_name")
                }
            ),
            "prompt_versions": sorted(
                {
                    value
                    for result in results
                    for row in result.transcript
                    for value in (
                        (row.get("planning_intent_decision") or {}).get("prompt_version"),
                        ((row.get("recommendation_advice") or {}).get("prompt_version")),
                    )
                    if value
                }
            ),
            "business_logic_modified": False,
            "retrieval_cold_start_policy": (
                "first successful bge_hybrid retrieval in this serial run"
            ),
        },
    )
    if output_dir is not None:
        write_resume_release_report(report, output_dir)
    return report


def write_resume_release_report(
    report: ResumeReleaseEvalReport,
    output_dir: Path,
) -> None:
    """Write inspectable JSON, Markdown and JSONL without storing credentials."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    with (output_dir / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for result in report.case_results:
            handle.write(result.model_dump_json() + "\n")
    (output_dir / "report.md").write_text(
        _render_markdown(report),
        encoding="utf-8",
    )


def _select_cases(
    dataset: ResumeReleaseDataset,
    *,
    case_ids: Sequence[str] | None,
    category: str | None,
    label_status: Literal["reviewed", "draft", "all"],
    allow_draft: bool,
) -> list[ResumeReleaseCase]:
    requested = set(case_ids or ())
    known = {case.case_id for case in dataset.cases}
    unknown = requested - known
    if unknown:
        raise EvaluationConfigurationError(
            f"unknown case id(s): {', '.join(sorted(unknown))}"
        )
    # ``--allow-draft`` is the explicit pilot switch.  With the default
    # ``reviewed`` selector it therefore means “use the available draft set”
    # when no reviewed labels exist, rather than silently returning zero cases.
    effective_label_status = (
        "all"
        if allow_draft and label_status == "reviewed"
        else label_status
    )
    cases = [
        case
        for case in dataset.cases
        if (not requested or case.case_id in requested)
        and (category is None or case.category == category)
        and (
            effective_label_status == "all"
            or case.label_status == effective_label_status
        )
    ]
    if not allow_draft and any(case.label_status == "draft" for case in cases):
        raise EvaluationConfigurationError(
            "draft cases require allow_draft=True; no draft result is promoted automatically"
        )
    if effective_label_status == "reviewed" and not cases:
        raise EvaluationConfigurationError(
            "no reviewed cases are available; use allow_draft=True explicitly for a pilot"
        )
    return cases


def _run_case(
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
    *,
    repeat: int,
    evaluation_clock: datetime,
    max_model_calls: int | None,
    used_model_calls: int,
    dependencies: _EvaluationDependencies,
) -> EvaluationCaseResult:
    started_at = perf_counter()
    transcript: list[dict[str, Any]] = []
    last_payload: dict[str, Any] | None = None
    error: str | None = None
    final_view: dict[str, Any] | None = None

    try:
        with tempfile.TemporaryDirectory(prefix="hft-eval-") as temp_dir:
            app, router_probe = _build_evaluation_app(
                Path(temp_dir) / "case.db",
                case,
                variant,
                evaluation_clock,
                dependencies=dependencies,
            )
            with TestClient(app) as client:
                created = client.post("/api/sessions", headers=_headers())
                if created.status_code != 201:
                    raise RuntimeError(
                        f"session creation failed: {created.status_code} {created.text}"
                    )
                session_id = created.json()["data"]["session_id"]
                for step_index, step in enumerate(case.steps):
                    if (
                        max_model_calls is not None
                        and used_model_calls
                        + _count_provider_attempts(transcript)
                        >= max_model_calls
                    ):
                        error = "model call budget exhausted during case"
                        break
                    if step.action == "message":
                        payload = _message_step(
                            client,
                            session_id,
                            step,
                            case,
                            variant,
                            repeat,
                            step_index,
                        )
                        last_payload = payload.get("data")
                        transcript.append(
                            _annotate_runtime_timing(
                                _transcript_row(
                                    session_id=session_id,
                                    step_index=step_index,
                                    action="message",
                                    request_id=payload["request_id"],
                                    response=payload,
                                    semantic_stage_trace=(
                                        {
                                            "turn_interpreter": router_probe.latest_semantic_trace,
                                            "planning_intent": _planning_intent_semantic_trace(
                                                payload.get("data")
                                            ),
                                        }
                                        if router_probe.latest_semantic_trace is not None
                                        else None
                                    ),
                                ),
                                dependencies,
                            )
                        )
                    elif step.action == "select_plan":
                        selection = _select_step(
                            client,
                            session_id,
                            step,
                            last_payload,
                        )
                        transcript.append(
                            _annotate_runtime_timing(
                                _transcript_row(
                                    session_id=session_id,
                                    step_index=step_index,
                                    action="select_plan",
                                    request_id=None,
                                    response=selection,
                                ),
                                dependencies,
                            )
                        )
                    else:
                        replacement = _replace_step(
                            client,
                            session_id,
                            step,
                            case,
                            variant,
                            repeat,
                            step_index,
                        )
                        last_payload = replacement.get("data")
                        transcript.append(
                            _annotate_runtime_timing(
                                _transcript_row(
                                    session_id=session_id,
                                    step_index=step_index,
                                    action="replace_stop",
                                    request_id=replacement["request_id"],
                                    response=replacement,
                                ),
                                dependencies,
                            )
                        )
                restored = client.get(
                    f"/api/sessions/{session_id}",
                    headers=_headers(),
                )
                if restored.status_code == 200:
                    final_view = restored.json().get("data")
                elif error is None:
                    error = f"session restore failed: {restored.status_code}"
    except Exception as exc:  # one broken case must not stop the remaining set
        # Do not serialize an exception message: provider clients sometimes
        # echo request details.  The case-level failure detail retains the
        # exception class, while HTTP response status/error fields remain
        # available in the safe transcript envelope.
        error = type(exc).__name__

    elapsed_ms = max(0, round((perf_counter() - started_at) * 1000))
    result = _score_case(
        case,
        variant,
        repeat=repeat,
        transcript=transcript,
        final_view=final_view,
        last_payload=last_payload,
        elapsed_ms=elapsed_ms,
        error=error,
    )
    return result


def _build_evaluation_app(
    database_path: Path,
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
    evaluation_clock: datetime,
    *,
    dependencies: _EvaluationDependencies | None = None,
):
    """Build one isolated application without importing the global server app."""

    dependencies = dependencies or _build_evaluation_dependencies(variant)
    catalog: Catalog = dependencies.catalog
    environment = EnvironmentContext(
        now=evaluation_clock,
        default_location=DEFAULT_LOCATION,
    )
    router_probe = _RecordingRouter(
        dependencies.router_delegate
    )
    app = create_app(
        database_path=database_path,
        router=router_probe,
        environment_provider=lambda _actor: environment,
        weather_provider=_EvaluationWeatherProvider(
            rainy=case.fixtures.weather == "rainy",
            clock=evaluation_clock,
        ),
        route_provider=_EvaluationRouteProvider(clock=evaluation_clock),
        geocoding_provider=_EvaluationGeocodingProvider(
            resolution=case.fixtures.geocoding,
            clock=evaluation_clock,
        ),
        availability_provider=_EvaluationAvailabilityProvider(
            all_unavailable=case.fixtures.availability == "all_unavailable",
            clock=evaluation_clock,
        ),
        catalog=catalog,
        planning_intent_provider=dependencies.planning_intent_provider,
        candidate_retriever=dependencies.candidate_retriever,
        recommendation_advisor=dependencies.recommendation_advisor,
    )
    return app, router_probe


def _build_evaluation_dependencies(
    variant: EvaluationVariant,
) -> _EvaluationDependencies:
    """Construct run-scoped providers once; databases remain case-scoped."""

    return _EvaluationDependencies(
        catalog=DemoCatalog(),
        router_delegate=(
            DemoRouter()
            if variant.router_mode == "demo"
            else build_default_turn_interpreter()
        ),
        planning_intent_provider=build_default_planning_intent_provider(
            variant.planning_intent_mode
        ),
        candidate_retriever=build_default_candidate_retriever(
            variant.retrieval_mode
        ),
        recommendation_advisor=build_default_recommendation_advisor(
            variant.advisor_mode
        ),
    )


def _message_step(
    client: TestClient,
    session_id: str,
    step: EvaluationStep,
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
    repeat: int,
    step_index: int,
) -> dict[str, Any]:
    request_id = _request_id(case, repeat, step_index)
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=_headers(),
        json={"request_id": request_id, "content": step.user_input},
    )
    return _response_envelope(response, request_id=request_id)


def _select_step(
    client: TestClient,
    session_id: str,
    step: EvaluationStep,
    last_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    view_response = client.get(
        f"/api/sessions/{session_id}",
        headers=_headers(),
    )
    if view_response.status_code != 200:
        return {
            "status_code": view_response.status_code,
            "error": "session view unavailable before selection",
            "data": None,
        }
    view = view_response.json().get("data", {})
    plans = list(view.get("plans") or [])
    strategy = step.selection_strategy
    plan_id: str | None = None
    if strategy == "recommended":
        advice = (last_payload or {}).get("recommendation_advice")
        plan_id = advice.get("recommended_plan_id") if advice else None
        if plan_id not in {item.get("plan_id") for item in plans}:
            return {
                "status_code": 422,
                "error": "recommended plan id missing or not in current plans",
                "data": None,
            }
    elif strategy == "first" and plans:
        plan_id = plans[0].get("plan_id")
    elif strategy == "second" and len(plans) >= 2:
        plan_id = plans[1].get("plan_id")
    if plan_id is None:
        return {
            "status_code": 422,
            "error": f"selection strategy {strategy!r} has no usable plan",
            "data": None,
        }
    response = client.post(
        f"/api/sessions/{session_id}/plans/{plan_id}/select",
        headers=_headers(),
    )
    result = _response_envelope(response, request_id=None)
    result["requested_plan_id"] = plan_id
    return result


def _replace_step(
    client: TestClient,
    session_id: str,
    step: EvaluationStep,
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
    repeat: int,
    step_index: int,
) -> dict[str, Any]:
    view_response = client.get(
        f"/api/sessions/{session_id}",
        headers=_headers(),
    )
    request_id = _request_id(case, repeat, step_index)
    if view_response.status_code != 200:
        return {
            "request_id": request_id,
            "status_code": view_response.status_code,
            "error": "session view unavailable before replacement",
            "data": None,
        }
    view = view_response.json().get("data", {})
    selected_id = view.get("selected_plan_id")
    selected_plan = next(
        (plan for plan in view.get("plans", []) if plan.get("plan_id") == selected_id),
        None,
    )
    target_index = step.target_stop_index
    if selected_plan is None or target_index is None:
        return {
            "request_id": request_id,
            "status_code": 422,
            "error": "selected plan or target index unavailable before replacement",
            "data": None,
        }
    stops = selected_plan.get("stops") or []
    if target_index >= len(stops):
        return {
            "request_id": request_id,
            "status_code": 422,
            "error": "target index is outside selected plan",
            "data": None,
        }
    target = stops[target_index]
    criteria: list[dict[str, Any]] = []
    if step.preset == "shorter_travel":
        criteria.append(
            {
                "kind": "route_objective",
                "metric": "total_route_distance",
                "direction": "decrease",
                "strength": "required",
            }
        )
    elif step.preset != "similar" and step.user_input:
        criteria.append(
            {"kind": "semantic", "text": step.user_input, "strength": "preferred"}
        )
    command = {
        "operation": "replace",
        "base_plan_version_id": view.get("active_plan_version_id"),
        "base_plan_id": selected_id,
        "target": {
            "stop_index": target_index,
            "resource_id": target.get("resource_id"),
            "role": target.get("role"),
            "raw_text": target.get("name") or f"第 {target_index + 1} 站",
        },
        "replacement_criteria": criteria,
    }
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=_headers(),
        json={
            "request_id": request_id,
            "content": step.user_input or "按当前条件替换这一站",
            "conversation_command": command,
        },
    )
    result = _response_envelope(response, request_id=request_id)
    result.update(
        {
            "base_plan_id": selected_id,
            "base_plan_version_id": view.get("active_plan_version_id"),
            "base_plan_stops": [
                {
                    "resource_id": item.get("resource_id"),
                    "role": item.get("role"),
                    "name": item.get("name"),
                }
                for item in stops
            ],
            "target_stop_index": target_index,
        }
    )
    return result


def _response_envelope(response, *, request_id: str | None) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    data = body.get("data") if isinstance(body, dict) else None
    return {
        "request_id": request_id,
        "status_code": response.status_code,
        "data": data,
        "detail": body.get("detail") if isinstance(body, dict) else None,
        "error": None if response.status_code < 400 else body.get("detail", response.text),
    }


def _transcript_row(
    *,
    session_id: str,
    step_index: int,
    action: str,
    request_id: str | None,
    response: dict[str, Any],
    semantic_stage_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = response.get("data") or {}
    row: dict[str, Any] = {
        "session_id": session_id,
        "step_index": step_index,
        "action": action,
        "request_id": request_id,
        "status_code": response.get("status_code"),
        "elapsed_ms": response.get("elapsed_ms"),
        "error": response.get("error"),
        "plan_version_id": data.get("plan_version_id"),
        "selected_plan_id": data.get("selected_plan_id"),
        "plans": data.get("plans", []),
        "plan_ids": [item.get("plan_id") for item in data.get("plans", [])],
        "constraint_summary": data.get("constraint_summary", []),
        "assumptions": data.get("assumptions", []),
        # Provider and catalog facts are part of the public response contract.
        # Keep them in the evaluator transcript so provider-dependent
        # postconditions can be audited without reaching into Graph state.
        "provider_facts": data.get("provider_facts", []),
        "catalog_violations": data.get("catalog_violations", []),
        "catalog_warnings": data.get("catalog_warnings", []),
        "warnings": data.get("warnings", []),
        "poi_presentations": data.get("poi_presentations", []),
        "plan_diffs": data.get("plan_diffs", []),
        "recommendation_advice": data.get("recommendation_advice"),
        "planning_intent_decision": data.get("planning_intent_decision"),
        "retrieval_evidence": data.get("retrieval_evidence", []),
        "retrieval_mode": data.get("retrieval_mode"),
        "retrieval_index_version": data.get("retrieval_index_version"),
        "conversation_command": data.get("conversation_command"),
        "runtime_decisions": data.get("runtime_decisions", []),
        "question": data.get("question"),
        "conflict": data.get("conflict"),
        "status": data.get("status"),
    }
    if semantic_stage_trace is not None:
        row["semantic_stage_trace"] = semantic_stage_trace
    for key in (
        "requested_plan_id",
        "base_plan_id",
        "base_plan_version_id",
        "base_plan_stops",
        "target_stop_index",
    ):
        if key in response:
            row[key] = response[key]
    return row


def _annotate_runtime_timing(
    row: dict[str, Any],
    dependencies: _EvaluationDependencies,
) -> dict[str, Any]:
    """Add evaluator-only cold/warm labels without changing RuntimeDecision.

    A successful dense retrieval is the observable event that pays the BGE
    model/index load.  Rule retrievals, empty semantic requests and hybrid
    fallbacks are not labelled as dense cold starts.  The serialized response
    contract remains untouched; only the evaluation transcript gets the
    ``evaluation_cold_start`` marker.
    """

    decisions: list[dict[str, Any]] = []
    for decision in row.get("runtime_decisions") or []:
        if not isinstance(decision, dict):
            continue
        safe_decision = dict(decision)
        is_dense_retrieval = (
            decision.get("stage") == "candidate_retrieval"
            and decision.get("adapter") == "bge_hybrid"
            and int(decision.get("query_count") or 0) > 0
            and decision.get("fallback_reason") is None
        )
        if is_dense_retrieval:
            safe_decision["evaluation_cold_start"] = not dependencies.retrieval_warmed
            dependencies.retrieval_warmed = True
        decisions.append(safe_decision)
    row["runtime_decisions"] = decisions
    return row


def _score_case(
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
    *,
    repeat: int,
    transcript: list[dict[str, Any]],
    final_view: dict[str, Any] | None,
    last_payload: dict[str, Any] | None,
    elapsed_ms: int,
    error: str | None,
) -> EvaluationCaseResult:
    final_row = next(
        (
            item
            for item in reversed(transcript)
            if item.get("action") in {"message", "replace_stop"}
        ),
        None,
    )
    payload = (final_row or {}).get("plans") is not None and final_row or None
    actual_outcome = _actual_outcome(final_row)
    assertions: list[EvalAssertion] = []

    def add(
        metric: str,
        status: Literal[
            "passed",
            "failed",
            "not_applicable",
            "not_observable",
            "not_evaluable",
        ],
        *,
        expected: Any = None,
        actual: Any = None,
        details: str | None = None,
        required: bool = True,
    ) -> None:
        assertions.append(
            EvalAssertion(
                metric=metric,
                status=status,
                expected=expected,
                actual=actual,
                details=details,
                required=required,
            )
        )

    expected_outcome = case.expected.outcome
    add(
        "outcome",
        "passed" if actual_outcome == expected_outcome else "failed",
        expected=expected_outcome,
        actual=actual_outcome,
    )

    downstream_evaluable = _downstream_assertions_evaluable(
        case,
        actual_outcome=actual_outcome,
        final_row=final_row,
        transcript=transcript,
    )

    def add_downstream(
        metric: str,
        status: Literal[
            "passed",
            "failed",
            "not_applicable",
            "not_observable",
            "not_evaluable",
        ],
        *,
        expected: Any = None,
        actual: Any = None,
        details: str | None = None,
        required: bool = True,
    ) -> None:
        # A plan-dependent assertion has no truth value when its prerequisite
        # plan is absent.  Convert even an accidentally passing scorer (for
        # example ``all([])``) so an upstream failure cannot become a phantom
        # downstream success.
        if not downstream_evaluable and status != "not_applicable":
            status = "not_evaluable"
            details = (
                "前置结果未满足，无法评价该下游断言；仅保留主结果失败"
                if not details
                else f"前置结果未满足，无法评价该下游断言；{details}"
            )
        add(
            metric,
            status,
            expected=expected,
            actual=actual,
            details=details,
            required=required,
        )

    if expected_outcome == "question":
        actual_field = ((final_row or {}).get("question") or {}).get("field")
        add(
            "question_field",
            "passed"
            if actual_field == case.expected.expected_question_field
            else "failed",
            expected=case.expected.expected_question_field,
            actual=actual_field,
        )
    elif expected_outcome == "conflict":
        conflict = (final_row or {}).get("conflict") or {}
        actual_code = conflict.get("code")
        actual_fields = set(conflict.get("fields") or ())
        expected_fields = set(case.expected.expected_conflict_fields)
        add(
            "conflict_code",
            "passed"
            if actual_code == case.expected.expected_conflict_code
            else "failed",
            expected=case.expected.expected_conflict_code,
            actual=actual_code,
        )
        add(
            "conflict_fields",
            "passed" if expected_fields.issubset(actual_fields) else "failed",
            expected=sorted(expected_fields),
            actual=sorted(actual_fields),
        )

    constraints = _constraint_snapshot(final_row, final_view)
    for field, expected_value in case.expected.expected_constraint_values.items():
        actual_value = _constraint_value(constraints, field)
        if actual_value is None:
            add_downstream(
                f"constraint.{field}",
                "not_observable",
                expected=expected_value,
                actual=None,
                details="field was not exposed by the HTTP/session read model",
            )
        else:
            add_downstream(
                f"constraint.{field}",
                "passed" if _values_equal(actual_value, expected_value) else "failed",
                expected=expected_value,
                actual=actual_value,
            )

    if case.category == "modification":
        _score_modification(case, final_row, transcript, add_downstream)
    else:
        _score_planning_shape(case, final_row, add_downstream)
        _score_semantic_objectives(case, final_row, add_downstream)

    if case.expected.require_grounded_advice:
        _score_grounded_advice(case, final_row, add_downstream)
    elif case.expected.advice_must_address:
        add(
            "grounded_advice",
            "not_applicable",
            required=False,
            details="case does not require advice",
        )
    if case.expected.advice_must_address:
        add_downstream(
            "advice_address_manual_review",
            "not_observable",
            expected=list(case.expected.advice_must_address),
            actual=(final_row or {}).get("recommendation_advice"),
            details="textual helpfulness is a manual review hint, not an automatic pass",
            required=False,
        )

    if "hard_constraint" in case.tags:
        hard_status = _hard_constraint_status(case, final_row, constraints)
        add_downstream(
            "hard_constraint_postconditions",
            hard_status[0],
            expected=hard_status[1],
            actual=hard_status[2],
            details=hard_status[3],
        )

    # These checks deliberately live beside, rather than inside, the product
    # verifier.  The product already enforces the facts; the evaluator must
    # prove that the public result reflects those decisions.  Fixture-specific
    # checks are explicit so a missing provider observation is not silently
    # counted as success.
    _score_plan_fact_postconditions(case, final_row, constraints, add_downstream)

    runtime_summary = _runtime_summary(transcript)
    required_assertions = [item for item in assertions if item.required]
    task_passed = (
        error is None
        and bool(required_assertions)
        and all(item.status == "passed" for item in required_assertions)
    )
    if error is not None:
        failure_kinds = ["runner_failure"]
    elif not task_passed:
        failure_kinds = ["product_failure"]
        if runtime_summary["fallback_count"]:
            failure_kinds.append("model_failure")
    else:
        failure_kinds = []
    if any(item.label_status == "draft" for item in (case,)):
        failure_kinds.append("label_review_required")
    has_fallback = runtime_summary["fallback_count"] > 0
    if task_passed:
        task_status: Literal["normal_success", "degraded_but_completed"] = (
            "degraded_but_completed" if has_fallback else "normal_success"
        )
    else:
        task_status = "failed"
    if error is not None:
        actual_outcome = "error"

    failure_details = _failure_details(
        assertions=assertions,
        transcript=transcript,
        error=error,
        label_status=case.label_status,
    )

    return EvaluationCaseResult(
        case_id=case.case_id,
        variant_id=variant.variant_id,
        repeat=repeat,
        label_status=case.label_status,
        category=case.category,
        tags=list(case.tags),
        executed=True,
        task_status=task_status,
        task_passed=task_passed,
        actual_outcome=actual_outcome,
        assertions=assertions,
        runtime_summary=runtime_summary,
        transcript=transcript,
        elapsed_ms=elapsed_ms,
        failure_kinds=failure_kinds,
        failure_details=failure_details,
        error=error,
    )


def _downstream_assertions_evaluable(
    case: ResumeReleaseCase,
    *,
    actual_outcome: str,
    final_row: dict[str, Any] | None,
    transcript: Sequence[dict[str, Any]],
) -> bool:
    """Whether outcome-dependent assertions have an observable prerequisite.

    A missing plan is itself a meaningful outcome failure.  Shape, semantic,
    advice, fact and modification assertions after that point are not failures
    of their own: there is no object on which they can be evaluated.  Keeping
    this distinction prevents one upstream failure from being counted several
    times in aggregate metrics.
    """

    if case.expected.outcome != "plan":
        return True
    if actual_outcome != "plan" or not (final_row or {}).get("plans"):
        return False
    if case.category != "modification":
        return True
    first_message = next(
        (item for item in transcript if item.get("action") == "message"),
        None,
    )
    return bool((first_message or {}).get("plans"))


def _failure_details(
    *,
    assertions: Sequence[EvalAssertion],
    transcript: Sequence[dict[str, Any]],
    error: str | None,
    label_status: Literal["draft", "reviewed"],
) -> list[EvalFailureDetail]:
    """Build bounded diagnostics from observable evaluator facts only."""

    details: list[EvalFailureDetail] = []
    for assertion in assertions:
        if assertion.status != "failed":
            continue
        details.append(
            EvalFailureDetail(
                kind="product_failure",
                code=f"assertion.{_safe_detail_code(assertion.metric)}",
                metric=assertion.metric,
                details=(
                    (
                        (assertion.details[:500] if assertion.details else None)
                        or "expected="
                        + _safe_eval_value(assertion.expected)
                        + "; actual="
                        + _safe_eval_value(assertion.actual)
                    )[:500]
                ),
            )
        )

    for row in transcript:
        for decision in row.get("runtime_decisions") or []:
            if not isinstance(decision, dict) or not decision.get("fallback_reason"):
                continue
            # ``not_run`` is an intentional short-circuit (for example an
            # expected conflict before PlanningIntent), not a model fallback.
            if decision.get("adapter") == "not_run":
                continue
            stage = str(decision.get("stage") or "unknown")
            reason = _safe_detail_code(decision.get("fallback_reason"))
            diagnostic_code = (
                _safe_detail_code(decision.get("diagnostic_code"))
                if decision.get("diagnostic_code")
                else ""
            )
            kind: Literal["product_failure", "model_failure", "fixture_failure", "runner_failure", "label_review_required"] = (
                "fixture_failure"
                if stage == "candidate_retrieval"
                and reason.startswith(("missing_", "retrieval_", "embedding_", "index_"))
                else "model_failure"
            )
            details.append(
                EvalFailureDetail(
                    kind=kind,
                    code=(
                        f"runtime_diagnostic.{diagnostic_code}"
                        if diagnostic_code
                        else f"runtime_fallback.{reason}"
                    ),
                    stage=stage,
                    details=(
                        f"adapter={_safe_detail_code(decision.get('adapter'))}; "
                        f"attempts={decision.get('attempts', 0)}; "
                        f"latency_ms={decision.get('latency_ms')}"
                        + (
                            f"; paths={_safe_diagnostic_list(decision.get('diagnostic_paths'))}"
                            if diagnostic_code
                            else ""
                        )
                        + (
                            f"; error_types={_safe_diagnostic_list(decision.get('diagnostic_error_types'))}"
                            if diagnostic_code
                            else ""
                        )
                    ),
                )
            )

    if error is not None:
        # Keep the existing top-level error for backwards compatibility, but
        # expose only its exception class in the detailed diagnostic.
        error_type = str(error).split(":", 1)[0].strip() or "UnknownError"
        details.append(
            EvalFailureDetail(
                kind="runner_failure",
                code="runner.exception",
                details=f"case execution raised {_safe_detail_code(error_type)}",
            )
        )
    if label_status == "draft":
        details.append(
            EvalFailureDetail(
                kind="label_review_required",
                code="draft_label",
                details="case label requires independent human review before resume claims",
            )
        )
    return details


def _safe_detail_code(value: Any) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r"[^A-Za-z0-9_.:=/-]+", "_", text)
    return text[:120] or "unknown"


def _safe_diagnostic_list(value: Any) -> str:
    if not isinstance(value, (list, tuple)):
        return "-"
    values = [_safe_detail_code(item) for item in value[:8] if item]
    return ",".join(values) or "-"


def _safe_eval_value(value: Any) -> str:
    """Serialize small expected/actual values without unbounded transcript text."""

    try:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        text = str(type(value).__name__)
    return text[:260]


def _score_planning_shape(case: ResumeReleaseCase, row: dict[str, Any] | None, add) -> None:
    plans = (row or {}).get("plans") or []
    if case.expected.expected_stop_count is not None:
        actual = sorted({len(plan.get("stops", [])) for plan in plans})
        add(
            "stop_count",
            "passed"
            if plans and actual == [case.expected.expected_stop_count]
            else "failed",
            expected=case.expected.expected_stop_count,
            actual=actual,
            details="all returned plans are checked",
        )
    if case.expected.required_roles:
        required = set(case.expected.required_roles)
        actual_roles = [
            sorted({stop.get("role") for stop in plan.get("stops", [])})
            for plan in plans
        ]
        passed = bool(plans) and all(required.issubset(set(roles)) for roles in actual_roles)
        add(
            "required_roles",
            "passed" if passed else "failed",
            expected=sorted(required),
            actual=actual_roles,
            details="all returned plans are checked",
        )


def _score_semantic_objectives(case: ResumeReleaseCase, row: dict[str, Any] | None, add) -> None:
    if not case.expected.expected_objectives:
        return
    decision = (row or {}).get("planning_intent_decision") or {}
    intent = decision.get("intent") or {}
    actual = {
        (item.get("kind") if isinstance(item, dict) else None)
        for item in ((intent.get("semantic_request") or {}).get("objectives") or [])
    }
    for objective in case.expected.expected_objectives:
        add(
            f"semantic_objective.{objective}",
            "passed" if objective in actual else "failed",
            expected=objective,
            actual=sorted(item for item in actual if item is not None),
            details="creation path PlanningIntent objective coverage",
        )


def _score_modification(case: ResumeReleaseCase, row: dict[str, Any] | None, transcript, add) -> None:
    plans = (row or {}).get("plans") or []
    diffs = (row or {}).get("plan_diffs") or []
    replacement_row = next(
        (item for item in reversed(transcript) if item.get("action") == "replace_stop"),
        None,
    )
    target_index = (replacement_row or {}).get("target_stop_index")
    base_stops = (replacement_row or {}).get("base_plan_stops") or []
    expected_max = case.expected.max_replacement_candidates or 2
    add(
        "replacement_candidate_count",
        "passed" if 0 < len(plans) <= expected_max else "failed",
        expected=f"1..{expected_max}",
        actual=len(plans),
    )
    diff_ids = {item.get("new_plan_id") for item in diffs}
    plan_ids = {item.get("plan_id") for item in plans}
    add(
        "plan_diff_valid",
        "passed"
        if plans
        and len(diffs) == len(plans)
        and diff_ids == plan_ids
        and all(len(item.get("replacements") or []) == 1 for item in diffs)
        else "failed",
        expected="one diff per candidate",
        actual={"plans": len(plans), "diffs": len(diffs), "diff_ids": sorted(diff_ids)},
    )
    replacement_indexes = [
        (item.get("replacements") or [{}])[0].get("stop_index")
        for item in diffs
        if item.get("replacements")
    ]
    add(
        "replacement_target_accuracy",
        "passed"
        if replacement_indexes and target_index is not None and all(
            index == target_index for index in replacement_indexes
        )
        else "failed",
        expected=target_index,
        actual=replacement_indexes,
    )
    preserved_values: list[bool] = []
    for plan in plans:
        new_stops = plan.get("stops") or []
        preserved_values.append(
            bool(base_stops)
            and len(new_stops) == len(base_stops)
            and all(
                before.get("resource_id") == after.get("resource_id")
                for index, (before, after) in enumerate(zip(base_stops, new_stops))
                if index != target_index
            )
        )
    add(
        "non_target_identity_preservation_rate",
        "passed" if preserved_values and all(preserved_values) else "failed",
        expected=1.0,
        actual=(sum(preserved_values) / len(preserved_values) if preserved_values else 0.0),
        details="identity is checked independently for every replacement candidate",
    )


def _score_grounded_advice(case: ResumeReleaseCase, row: dict[str, Any] | None, add) -> None:
    advice = (row or {}).get("recommendation_advice")
    plans = (row or {}).get("plans") or []
    if advice is None:
        add("grounded_advice", "failed", expected="advice present", actual=None)
        return
    plan_ids = {item.get("plan_id") for item in plans}
    advice_plan_ids = {item.get("plan_id") for item in advice.get("plans", [])}
    known_need_ids = {item.get("need_id") for item in advice.get("understood_needs", [])}
    known_evidence_ids = {
        item.get("evidence_id")
        for item in ((row or {}).get("retrieval_evidence") or [])
    }
    decision = (row or {}).get("planning_intent_decision") or {}
    known_evidence_ids.update(
        item.get("evidence_id")
        for item in (((decision.get("intent") or {}).get("semantic_request") or {}).get("evidence") or [])
    )
    # Replacement responses intentionally expose the command and its advice,
    # but not the private compiled SemanticRequest.  NeedSummary still carries
    # the user-evidence ids, which are safe to use as the public grounding
    # anchor for this evaluator.
    known_evidence_ids.update(
        evidence_id
        for need in advice.get("understood_needs", [])
        for evidence_id in need.get("user_evidence_ids", [])
    )
    unknown_plan_ids = advice_plan_ids - plan_ids
    unknown_need_ids = {
        need_id
        for plan in advice.get("plans", [])
        for need_id in plan.get("matched_need_ids", [])
        if need_id not in known_need_ids
    }
    unknown_evidence_ids = {
        evidence_id
        for plan in advice.get("plans", [])
        for evidence_id in plan.get("supporting_evidence_ids", [])
        if evidence_id not in known_evidence_ids
    }
    recommended_valid = advice.get("recommended_plan_id") in plan_ids
    grounded = not unknown_plan_ids and not unknown_need_ids and not unknown_evidence_ids
    add(
        "grounded_advice_valid",
        "passed" if recommended_valid and grounded else "failed",
        expected="known plan/need/evidence ids only",
        actual={
            "recommended_plan_valid": recommended_valid,
            "unknown_plan_ids": sorted(unknown_plan_ids),
            "unknown_need_ids": sorted(unknown_need_ids),
            "unknown_evidence_ids": sorted(unknown_evidence_ids),
        },
    )


@lru_cache(maxsize=1)
def _demo_catalog_fact_index() -> dict[str, dict[str, Any]]:
    """Load stable Demo World facts used as the evaluator's local oracle.

    ``AgentResponse`` intentionally exposes plans, not the complete Catalog
    records.  The evaluator therefore reads the same versioned local snapshot
    that builds ``DemoCatalog`` and only keeps the two stable facts needed for
    these postconditions.  Dynamic weather and availability remain response
    facts supplied by their injected fixtures.
    """

    world = DemoWorld()
    return {
        candidate.resource_id: {
            "open_hours": dict(world.record(candidate.resource_id).get("open_hours") or {}),
            "weather_sensitive": bool(
                world.record(candidate.resource_id).get("weather_sensitive", False)
            ),
        }
        for candidate in SnapshotCatalog().load_candidates()
    }


def _score_plan_fact_postconditions(
    case: ResumeReleaseCase,
    row: dict[str, Any] | None,
    constraints: dict[str, Any],
    add,
) -> None:
    """Score provider/catalog facts that were previously absent from S5.

    The checks are intentionally conservative.  ``not_observable`` means the
    public result did not contain enough evidence to prove a claim; it is not
    converted to a pass.  Fixture-specific assertions are only applicable
    when the dataset explicitly asks for that condition.
    """

    plans = (row or {}).get("plans") or []
    actual_outcome = _actual_outcome(row)

    if case.expected.outcome == "plan":
        _score_opening_hours_postcondition(plans, constraints, add)

    if case.fixtures.weather == "rainy":
        _score_rainy_weather_postcondition(
            plans,
            expected_plan=case.expected.outcome == "plan",
            add=add,
        )

    if case.fixtures.availability == "all_unavailable":
        _score_all_unavailable_postcondition(
            row,
            plans,
            actual_outcome=actual_outcome,
            add=add,
        )
    elif case.expected.outcome == "plan" and plans:
        _score_availability_postcondition(row, plans, add)


def _score_opening_hours_postcondition(
    plans: list[dict[str, Any]],
    constraints: dict[str, Any],
    add,
) -> None:
    if not plans:
        add(
            "opening_hours_postconditions",
            "not_observable",
            expected="every returned stop fits its known opening interval",
            actual={"plan_count": 0},
            details="no plan was returned, so opening hours cannot be checked here",
            required=False,
        )
        return

    raw_date = _constraint_value(constraints, "date")
    try:
        plan_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    except ValueError:
        plan_date = None
    if plan_date is None:
        add(
            "opening_hours_postconditions",
            "not_observable",
            expected="every returned stop fits its known opening interval",
            actual={"date": raw_date},
            details="plan date is not exposed in canonical ISO form",
        )
        return

    weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[plan_date.weekday()]
    facts = _demo_catalog_fact_index()
    failures: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for plan in plans:
        for stop in plan.get("stops") or []:
            resource_id = stop.get("resource_id")
            catalog_fact = facts.get(resource_id)
            hours = (catalog_fact or {}).get("open_hours", {}).get(weekday)
            if not hours:
                unknown.append(
                    {
                        "plan_id": plan.get("plan_id"),
                        "resource_id": resource_id,
                        "reason": "opening_hours_missing",
                    }
                )
                continue
            fits = visit_fits_opening_hours(
                hours,
                stop.get("start"),
                stop.get("end"),
            )
            if fits is False:
                failures.append(
                    {
                        "plan_id": plan.get("plan_id"),
                        "resource_id": resource_id,
                        "hours": hours,
                        "start": stop.get("start"),
                        "end": stop.get("end"),
                    }
                )
            elif fits is None:
                unknown.append(
                    {
                        "plan_id": plan.get("plan_id"),
                        "resource_id": resource_id,
                        "reason": "opening_hours_grammar_unsupported",
                    }
                )
    if failures:
        status = "failed"
    elif unknown:
        status = "not_observable"
    else:
        status = "passed"
    add(
        "opening_hours_postconditions",
        status,
        expected="every returned stop fits its known opening interval",
        actual={"violations": failures, "not_observable": unknown},
        details=(
            "all plan stops are checked against the versioned Demo World hours"
        ),
    )


def _score_rainy_weather_postcondition(
    plans: list[dict[str, Any]],
    *,
    expected_plan: bool,
    add,
) -> None:
    if not plans:
        add(
            "weather_safe_plan_postcondition",
            "not_observable" if expected_plan else "not_applicable",
            expected="a plan contains no weather-sensitive activity",
            actual={"plan_count": 0},
            details=(
                "rainy fixture expected a plan, but no plan was returned; outcome "
                "assertion records the task failure"
                if expected_plan
                else "no plan was expected"
            ),
            required=False,
        )
        return

    facts = _demo_catalog_fact_index()
    sensitive: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for plan in plans:
        for stop in plan.get("stops") or []:
            # The product weather gate applies to activities.  Meals are not
            # silently treated as outdoor activities when their catalog fact
            # is absent.
            if stop.get("role") != "activity":
                continue
            resource_id = stop.get("resource_id")
            catalog_fact = facts.get(resource_id)
            if catalog_fact is None:
                unknown.append(
                    {"plan_id": plan.get("plan_id"), "resource_id": resource_id}
                )
            elif catalog_fact["weather_sensitive"]:
                sensitive.append(
                    {"plan_id": plan.get("plan_id"), "resource_id": resource_id}
                )
    if sensitive:
        status = "failed"
    elif unknown:
        status = "not_observable"
    else:
        status = "passed"
    add(
        "weather_safe_plan_postcondition",
        status,
        expected="a plan contains no weather-sensitive activity",
        actual={"weather_sensitive_activities": sensitive, "unknown": unknown},
        details="rainy fixture checks every returned plan, not only the recommendation",
        required=expected_plan,
    )


def _availability_facts(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        item
        for item in ((row or {}).get("provider_facts") or [])
        if item.get("kind") == "availability" and item.get("resource_id")
    ]


def _score_availability_postcondition(
    row: dict[str, Any] | None,
    plans: list[dict[str, Any]],
    add,
) -> None:
    planned_ids = {
        stop.get("resource_id")
        for plan in plans
        for stop in (plan.get("stops") or [])
        if stop.get("resource_id")
    }
    facts = _availability_facts(row)
    verified_unavailable = sorted(
        {
            item["resource_id"]
            for item in facts
            if item.get("resource_id") in planned_ids
            and item.get("status") == AvailabilityStatus.UNAVAILABLE.value
            and item.get("verified") is True
            and not item.get("stale", False)
            and not item.get("degraded", False)
        }
    )
    observed_ids = {item["resource_id"] for item in facts}
    missing_ids = sorted(planned_ids - observed_ids)
    add(
        "availability_no_verified_unavailable",
        "failed" if verified_unavailable else "passed",
        expected="no returned stop has a verified unavailable fact",
        actual={
            "verified_unavailable_resource_ids": verified_unavailable,
            "unobserved_resource_ids": missing_ids,
        },
        details=(
            "unknown or unobserved availability is reported separately and is "
            "not treated as proof of availability"
        ),
    )
    add(
        "availability_observation_coverage",
        "passed" if not missing_ids else "not_observable",
        expected="availability fact observed for every returned stop",
        actual={
            "observed_resource_count": len(planned_ids & observed_ids),
            "planned_resource_count": len(planned_ids),
            "unobserved_resource_ids": missing_ids,
        },
        details="coverage is diagnostic; unknown facts remain visible",
        required=False,
    )


def _score_all_unavailable_postcondition(
    row: dict[str, Any] | None,
    plans: list[dict[str, Any]],
    *,
    actual_outcome: str,
    add,
) -> None:
    conflict_fields = set(((row or {}).get("conflict") or {}).get("fields") or ())
    passed = actual_outcome == "conflict" and not plans and "availability" in conflict_fields
    add(
        "availability_fixture_enforced",
        "passed" if passed else "failed",
        expected={
            "outcome": "conflict",
            "plan_count": 0,
            "conflict_field": "availability",
        },
        actual={
            "outcome": actual_outcome,
            "plan_count": len(plans),
            "conflict_fields": sorted(conflict_fields),
        },
        details=(
            "all_unavailable fixture must not be bypassed by an unverified plan"
        ),
    )


def _hard_constraint_status(
    case: ResumeReleaseCase,
    row: dict[str, Any] | None,
    constraints: dict[str, Any],
) -> tuple[
    Literal["passed", "failed", "not_observable"],
    Any,
    Any,
    str,
]:
    expected = case.expected
    actual_outcome = _actual_outcome(row)
    if expected.outcome == "conflict":
        actual_conflict = (row or {}).get("conflict") or {}
        passed = actual_outcome == "conflict" and (
            actual_conflict.get("code") == expected.expected_conflict_code
        )
        return (
            "passed" if passed else "failed",
            {"outcome": "conflict", "code": expected.expected_conflict_code},
            {"outcome": actual_outcome, "code": actual_conflict.get("code")},
            "conflict is an expected hard-constraint result",
        )
    plans = (row or {}).get("plans") or []
    if actual_outcome != "plan" or not plans:
        return "failed", "all plans feasible", {"outcome": actual_outcome}, "no feasible plans returned"
    failures: list[str] = []
    if expected.expected_stop_count is not None:
        failures.extend(
            f"{plan.get('plan_id')}:stop_count"
            for plan in plans
            if len(plan.get("stops") or []) != expected.expected_stop_count
        )
    if expected.required_roles:
        required = set(expected.required_roles)
        failures.extend(
            f"{plan.get('plan_id')}:required_roles"
            for plan in plans
            if not required.issubset({stop.get("role") for stop in plan.get("stops", [])})
        )
    departure_at = _constraint_value(constraints, "departure_at")
    if departure_at is not None:
        failures.extend(
            f"{plan.get('plan_id')}:departure_at"
            for plan in plans
            if not plan.get("route_legs")
            or plan["route_legs"][0].get("start") != departure_at
        )
    time_window = _constraint_value(constraints, "time_window")
    if isinstance(time_window, dict):
        window_start = _clock_minutes(time_window.get("start"))
        window_end = _clock_minutes(time_window.get("end"))
        if window_start is None or window_end is None:
            return "not_observable", time_window, None, "time window is not canonical"
        for plan in plans:
            for stop in plan.get("stops", []):
                start = _clock_minutes(stop.get("start"))
                end = _clock_minutes(stop.get("end"))
                if start is None or end is None or start < window_start or end > window_end:
                    failures.append(f"{plan.get('plan_id')}:time_window")
                    break
    max_leg_distance = _constraint_value(constraints, "max_distance_km")
    if max_leg_distance is not None:
        failures.extend(
            f"{plan.get('plan_id')}:max_distance_km"
            for plan in plans
            if max(
                (float(leg.get("distance_km", 0)) for leg in plan.get("route_legs", [])),
                default=0.0,
            )
            > float(max_leg_distance) + 1e-6
        )
    budget = _constraint_value(constraints, "budget_per_person")
    strict_budget = bool(constraints.get("strict_budget"))
    party = constraints.get("party") or {}
    if budget is not None and strict_budget:
        people = int(party.get("adults", 0)) + int(party.get("children", 0))
        if people <= 0:
            return "not_observable", budget, None, "party size is unavailable for per-person budget"
        failures.extend(
            f"{plan.get('plan_id')}:strict_budget"
            for plan in plans
            if float(plan.get("total_price", 0)) / people > float(budget) + 1e-6
        )
    total_distance = _constraint_value(constraints, "total_distance_km")
    if total_distance is not None:
        failures.extend(
            f"{plan.get('plan_id')}:total_distance"
            for plan in plans
            if sum(float(leg.get("distance_km", 0)) for leg in plan.get("route_legs", []))
            > float(total_distance) + 1e-6
        )
    return_by = _constraint_value(constraints, "return_by")
    if return_by is not None:
        deadline = _clock_minutes(return_by)
        if deadline is None:
            return "not_observable", return_by, None, "return deadline is not canonical"
        for plan in plans:
            legs = plan.get("route_legs") or []
            if not legs or legs[-1].get("destination_name") != "出发地":
                failures.append(f"{plan.get('plan_id')}:missing_return_leg")
                continue
            arrival = _clock_minutes(legs[-1].get("end"))
            if arrival is None or arrival > deadline:
                failures.append(f"{plan.get('plan_id')}:return_by")
    return (
        "passed" if not failures else "failed",
        "all returned plans satisfy applicable hard postconditions",
        failures or "all checked plans passed",
        "all candidates are checked, not only the recommended plan",
    )


def _constraint_snapshot(
    row: dict[str, Any] | None,
    final_view: dict[str, Any] | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in ((row or {}).get("constraint_summary") or []):
        if isinstance(item, dict) and item.get("field"):
            values[item["field"]] = item.get("value")
    active = (final_view or {}).get("active_constraints") or {}
    for field, value in active.items():
        if field in values:
            continue
        if isinstance(value, dict) and "value" in value:
            values[field] = value["value"]
        else:
            values[field] = value
    return values


def _constraint_value(values: dict[str, Any], field: str) -> Any:
    if field in values:
        return values[field]
    # Backward-compatible evaluator aliases for the old draft label.  New
    # labels should use time_window.start/end directly.
    if field in {"time_start", "time_end"} and isinstance(values.get("time_window"), dict):
        return values["time_window"].get("start" if field == "time_start" else "end")
    return None


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _values_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-6)
    return str(actual) == str(expected)


def _actual_outcome(row: dict[str, Any] | None) -> Literal["plan", "question", "conflict", "unknown"]:
    if not row:
        return "unknown"
    if row.get("plans"):
        return "plan"
    if row.get("question") or row.get("status") == "needs_input":
        return "question"
    if row.get("conflict"):
        return "conflict"
    return "unknown"


def _runtime_summary(transcript: Sequence[dict[str, Any]]) -> dict[str, Any]:
    decisions = [
        item
        for row in transcript
        for item in (row.get("runtime_decisions") or [])
        if isinstance(item, dict)
    ]
    model_decisions = [item for item in decisions if item.get("model_invoked")]
    observed = [
        item
        for item in model_decisions
        if item.get("input_tokens") is not None and item.get("output_tokens") is not None
    ]
    stage_summary: dict[str, dict[str, Any]] = {}
    for stage in {item.get("stage") for item in decisions}:
        if stage is None:
            continue
        stage_items = [item for item in decisions if item.get("stage") == stage]
        latencies = [item.get("latency_ms") for item in stage_items if item.get("latency_ms") is not None]
        cold_latencies = [
            item.get("latency_ms")
            for item in stage_items
            if item.get("evaluation_cold_start") is True
            and item.get("latency_ms") is not None
        ]
        warm_latencies = [
            item.get("latency_ms")
            for item in stage_items
            if item.get("evaluation_cold_start") is False
            and item.get("latency_ms") is not None
        ]
        stage_summary[stage] = {
            "invocation_count": len(stage_items),
            "model_invocation_count": sum(item.get("model_invoked", False) for item in stage_items),
            "model_decision_count": sum(
                bool(item.get("model_invoked")) for item in stage_items
            ),
            "provider_attempt_count": sum(
                _decision_attempts(item)
                for item in stage_items
                if item.get("model_invoked")
            ),
            "fallback_count": sum(
                bool(item.get("fallback_reason"))
                and bool(item.get("model_invoked"))
                for item in stage_items
            ),
            "diagnostic_counts": _count_diagnostics(stage_items),
            "p50_ms": _percentile(latencies, 0.50),
            "p95_ms": _percentile(latencies, 0.95),
            "cold_start_count": len(cold_latencies),
            "cold_start_p50_ms": _percentile(cold_latencies, 0.50),
            "cold_start_p95_ms": _percentile(cold_latencies, 0.95),
            "warm_count": len(warm_latencies),
            "warm_p50_ms": _percentile(warm_latencies, 0.50),
            "warm_p95_ms": _percentile(warm_latencies, 0.95),
        }
    model_count = len(model_decisions)
    provider_attempt_count = sum(_decision_attempts(item) for item in model_decisions)
    fallback_count = sum(bool(item.get("fallback_reason")) for item in model_decisions)
    diagnostic_counts: dict[str, int] = {}
    for item in model_decisions:
        code = item.get("diagnostic_code")
        if not code:
            continue
        safe_code = _safe_detail_code(code)
        diagnostic_counts[safe_code] = diagnostic_counts.get(safe_code, 0) + 1
    return {
        "stage": stage_summary,
        "invocation_count": len(decisions),
        # Compatibility alias retained for existing consumers.  This value is
        # a logical decision count; use the explicit names for new reports.
        "model_invocation_count": model_count,
        "model_decision_count": model_count,
        "provider_attempt_count": provider_attempt_count,
        "fallback_count": fallback_count,
        "diagnostic_counts": dict(sorted(diagnostic_counts.items())),
        "fallback_rate": (fallback_count / model_count if model_count else 0.0),
        "known_input_tokens": sum(int(item["input_tokens"]) for item in observed),
        "known_output_tokens": sum(int(item["output_tokens"]) for item in observed),
        "token_observed_call_count": len(observed),
        "token_unobserved_call_count": model_count - len(observed),
        "token_coverage_rate": (len(observed) / model_count if model_count else 1.0),
        "degraded_completion": bool(fallback_count),
        "cold_start_count": sum(
            item.get("evaluation_cold_start") is True for item in decisions
        ),
        "warm_count": sum(
            item.get("evaluation_cold_start") is False for item in decisions
        ),
    }


def _count_diagnostics(decisions: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        code = decision.get("diagnostic_code")
        if not code:
            continue
        safe_code = _safe_detail_code(code)
        counts[safe_code] = counts.get(safe_code, 0) + 1
    return dict(sorted(counts.items()))


def _decision_attempts(decision: dict[str, Any]) -> int:
    """Return the provider request attempts represented by one decision."""

    if "attempts" not in decision:
        return 1 if decision.get("model_invoked") else 0
    try:
        attempts = int(decision.get("attempts") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, attempts)


def _aggregate_results(results: Sequence[EvaluationCaseResult]) -> dict[str, Any]:
    total = len(results)
    executed_results = [item for item in results if item.executed]
    successful = sum(item.task_passed for item in executed_results)
    hard_results = [
        item
        for item in executed_results
        if "hard_constraint" in item.tags
    ]
    hard_assertions = [
        assertion
        for item in hard_results
        for assertion in item.assertions
        if assertion.metric == "hard_constraint_postconditions"
    ]
    evaluable_hard_assertions = [
        assertion
        for assertion in hard_assertions
        if assertion.status in {"passed", "failed"}
    ]
    hard_passed = sum(
        assertion.status == "passed" for assertion in evaluable_hard_assertions
    )
    all_assertions = [
        assertion
        for item in executed_results
        for assertion in item.assertions
        if assertion.required and assertion.status != "not_evaluable"
    ]
    not_evaluable_assertions = sum(
        assertion.required and assertion.status == "not_evaluable"
        for item in executed_results
        for assertion in item.assertions
    )
    passed_assertions = sum(assertion.status == "passed" for assertion in all_assertions)
    model_decisions = sum(
        item.runtime_summary.get(
            "model_decision_count",
            item.runtime_summary.get("model_invocation_count", 0),
        )
        for item in executed_results
    )
    provider_attempts = sum(
        item.runtime_summary.get(
            "provider_attempt_count",
            item.runtime_summary.get("model_invocation_count", 0),
        )
        for item in executed_results
    )
    observed_token_calls = sum(
        item.runtime_summary.get("token_observed_call_count", 0)
        for item in executed_results
    )
    fallback_calls = sum(
        item.runtime_summary.get("fallback_count", 0) for item in executed_results
    )
    postcondition_metrics: dict[str, Any] = {}
    for metric in (
        "opening_hours_postconditions",
        "weather_safe_plan_postcondition",
        "availability_no_verified_unavailable",
        "availability_observation_coverage",
        "availability_fixture_enforced",
    ):
        observations = [
            assertion
            for item in executed_results
            for assertion in item.assertions
            if assertion.metric == metric
        ]
        passed = sum(assertion.status == "passed" for assertion in observations)
        failed = sum(assertion.status == "failed" for assertion in observations)
        not_observable = sum(
            assertion.status == "not_observable" for assertion in observations
        )
        not_applicable = sum(
            assertion.status == "not_applicable" for assertion in observations
        )
        denominator = (
            passed + failed + not_observable
            if metric == "availability_observation_coverage"
            else passed + failed
        )
        postcondition_metrics[metric] = {
            "pass_rate": _metric(passed, denominator),
            "passed": passed,
            "failed": failed,
            "not_observable": not_observable,
            "not_applicable": not_applicable,
            "applicable_count": passed + failed + not_observable,
        }
    stage_latency = _aggregate_stage_latency(executed_results)
    return {
        "unique_case_count": len({item.case_id for item in results}),
        "execution_count": len(results),
        "reviewed_unique_case_count": len(
            {
                item.case_id
                for item in results
                if item.label_status == "reviewed"
            }
        ),
        "task_success_rate": _metric(successful, len(executed_results)),
        "hard_constraint_pass_rate": _metric(
            hard_passed,
            len(evaluable_hard_assertions),
        ),
        "required_assertion_pass_rate": _metric(passed_assertions, len(all_assertions)),
        "not_evaluable_assertion_count": not_evaluable_assertions,
        "normal_success_count": sum(item.task_status == "normal_success" for item in results),
        "degraded_but_completed_count": sum(
            item.task_status == "degraded_but_completed" for item in results
        ),
        "failed_count": sum(item.task_status == "failed" for item in results),
        "executed_case_count": len(executed_results),
        "skipped_case_count": total - len(executed_results),
        "fallback_rate": _metric(fallback_calls, model_decisions),
        "model_decision_count": model_decisions,
        "provider_attempt_count": provider_attempts,
        "postcondition_metrics": postcondition_metrics,
        "known_input_tokens": sum(
            item.runtime_summary.get("known_input_tokens", 0)
            for item in executed_results
        ),
        "known_output_tokens": sum(
            item.runtime_summary.get("known_output_tokens", 0)
            for item in executed_results
        ),
        "token_observed_call_count": sum(
            item.runtime_summary.get("token_observed_call_count", 0)
            for item in executed_results
        ),
        "token_unobserved_call_count": sum(
            item.runtime_summary.get("token_unobserved_call_count", 0)
            for item in executed_results
        ),
        "token_coverage_rate": _metric(observed_token_calls, model_decisions),
        "elapsed_ms": {
            "p50": _percentile([item.elapsed_ms for item in executed_results], 0.50),
            "p95": _percentile([item.elapsed_ms for item in executed_results], 0.95),
        },
        "stage_latency": stage_latency,
        "failure_kinds": {
            kind: sum(kind in item.failure_kinds for item in results)
            for kind in (
                "product_failure",
                "model_failure",
                "fixture_failure",
                "runner_failure",
                "label_review_required",
            )
        },
        "failure_codes": _aggregate_failure_codes(results),
        "structured_output_diagnostic_codes": _aggregate_runtime_diagnostic_codes(
            results
        ),
        "denominators": {
            "task_success_rate": "passed cases / all executed cases",
            "hard_constraint_pass_rate": "passed / evaluable hard-constraint postconditions (not_evaluable excluded)",
            "required_assertion_pass_rate": "passed / evaluable required assertions (not_evaluable excluded)",
            "fallback_rate": "fallback model decisions / model decisions",
            "model_decision_count": "logical runtime decisions that invoked a model",
            "provider_attempt_count": "sum of provider attempts across model decisions",
        },
    }


def _aggregate_failure_codes(
    results: Sequence[EvaluationCaseResult],
) -> dict[str, int]:
    """Count safe diagnostic codes so Pilot failures are easy to triage."""

    counts: dict[str, int] = {}
    for result in results:
        for detail in result.failure_details:
            counts[detail.code] = counts.get(detail.code, 0) + 1
    return dict(sorted(counts.items()))


def _aggregate_runtime_diagnostic_codes(
    results: Sequence[EvaluationCaseResult],
) -> dict[str, int]:
    """Count safe structured-output diagnostics across runtime traces."""

    counts: dict[str, int] = {}
    for result in results:
        for row in result.transcript:
            for decision in row.get("runtime_decisions") or []:
                if not isinstance(decision, dict):
                    continue
                code = decision.get("diagnostic_code")
                if not code:
                    continue
                safe_code = _safe_detail_code(code)
                counts[safe_code] = counts.get(safe_code, 0) + 1
    return dict(sorted(counts.items()))


def _aggregate_stage_latency(
    results: Sequence[EvaluationCaseResult],
) -> dict[str, Any]:
    """Aggregate stage timings and dense cold/warm buckets for the report."""

    by_stage: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        for row in result.transcript:
            for decision in row.get("runtime_decisions") or []:
                if not isinstance(decision, dict) or not decision.get("stage"):
                    continue
                by_stage.setdefault(str(decision["stage"]), []).append(decision)
    aggregate: dict[str, Any] = {}
    for stage, decisions in by_stage.items():
        all_latencies = [
            item.get("latency_ms") for item in decisions if item.get("latency_ms") is not None
        ]
        cold = [
            item.get("latency_ms")
            for item in decisions
            if item.get("evaluation_cold_start") is True
            and item.get("latency_ms") is not None
        ]
        warm = [
            item.get("latency_ms")
            for item in decisions
            if item.get("evaluation_cold_start") is False
            and item.get("latency_ms") is not None
        ]
        aggregate[stage] = {
            "count": len(decisions),
            "p50_ms": _percentile(all_latencies, 0.50),
            "p95_ms": _percentile(all_latencies, 0.95),
            "cold_start": {
                "count": len(cold),
                "p50_ms": _percentile(cold, 0.50),
                "p95_ms": _percentile(cold, 0.95),
            },
            "warm": {
                "count": len(warm),
                "p50_ms": _percentile(warm, 0.50),
                "p95_ms": _percentile(warm, 0.95),
            },
        }
    return aggregate


def _metric(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": (numerator / denominator if denominator else None),
    }


def _runner_failure_result(
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
    repeat: int,
    error: str,
) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        case_id=case.case_id,
        variant_id=variant.variant_id,
        repeat=repeat,
        label_status=case.label_status,
        category=case.category,
        tags=list(case.tags),
        executed=False,
        task_status="failed",
        task_passed=False,
        actual_outcome="error",
        assertions=[],
        runtime_summary={
            "invocation_count": 0,
            "model_invocation_count": 0,
            "model_decision_count": 0,
            "provider_attempt_count": 0,
            "fallback_count": 0,
            "diagnostic_counts": {},
        },
        transcript=[],
        elapsed_ms=0,
        failure_kinds=["runner_failure"],
        failure_details=[
            EvalFailureDetail(
                kind="runner_failure",
                code="runner.preflight_or_budget",
                details="case was not executed because the evaluation runner stopped it before HTTP execution",
            )
        ],
        error=error,
    )


def _headers() -> dict[str, str]:
    return {"X-User-Id": "eval-user"}


def _request_id(case: ResumeReleaseCase, repeat: int, step_index: int) -> str:
    # Each case has an isolated database; keeping the id deterministic makes a
    # rerun exercise the same idempotency boundary without colliding across cases.
    return f"eval-{case.case_id[:42]}-{repeat}-{step_index}"[:64]


def _count_provider_attempts(transcript: Sequence[dict[str, Any]]) -> int:
    return sum(
        _decision_attempts(item)
        for row in transcript
        for item in (row.get("runtime_decisions") or [])
        if isinstance(item, dict) and item.get("model_invoked")
    )


# Backward-compatible helper name for callers that imported the evaluator
# internals before the explicit decision/attempt distinction was added.
_count_model_calls = _count_provider_attempts


def _estimate_case_model_calls(
    case: ResumeReleaseCase,
    variant: EvaluationVariant,
) -> int:
    """Conservative preflight bound used only by the optional cost guard."""

    if variant.router_mode == "demo":
        return 0
    per_message = 2  # TurnInterpreter: one call plus one bounded format retry.
    if variant.planning_intent_mode == "llm":
        per_message += 2
    if variant.advisor_mode == "llm":
        per_message += 2
    replacement = 2 if variant.advisor_mode == "llm" else 0
    return sum(
        per_message if step.action == "message" else replacement if step.action == "replace_stop" else 0
        for step in case.steps
    )


def _percentile(values: Sequence[int | float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 2)


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        output = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return bool(output.strip())
    except Exception:
        return True


def _dataset_hash(dataset: ResumeReleaseDataset, path: Path | None) -> str:
    if path is not None and path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(dataset.model_dump_json().encode("utf-8")).hexdigest()


def _render_markdown(report: ResumeReleaseEvalReport) -> str:
    aggregate = report.aggregate
    lines = [
        "# Resume Release Evaluation",
        "",
        f"- Run: `{report.run_id}`",
        f"- Variant: `{report.variant.variant_id}`",
        f"- Commit: `{report.git_commit}` (dirty={report.git_dirty})",
        f"- Cases: {report.case_count}; draft={report.label_status_counts.get('draft', 0)}, reviewed={report.label_status_counts.get('reviewed', 0)}",
        f"- Unique cases: {aggregate.get('unique_case_count', 0)}; executions: {aggregate.get('execution_count', 0)}; reviewed unique cases: {aggregate.get('reviewed_unique_case_count', 0)}",
        "- Draft labels are exploratory and are not resume-grade ground truth.",
        "",
        "## Aggregate",
        "",
    ]
    for key in (
        "task_success_rate",
        "hard_constraint_pass_rate",
        "required_assertion_pass_rate",
        "fallback_rate",
    ):
        metric = aggregate.get(key)
        if isinstance(metric, dict):
            lines.append(
                f"- {key}: {metric.get('numerator')}/{metric.get('denominator')} = {metric.get('value')}"
            )
    postconditions = aggregate.get("postcondition_metrics") or {}
    if postconditions:
        lines.extend(["", "### Provider/catalog postconditions", ""])
        for name, metric in postconditions.items():
            pass_rate = metric.get("pass_rate") or {}
            lines.append(
                f"- {name}: {pass_rate.get('numerator')}/{pass_rate.get('denominator')} = "
                f"{pass_rate.get('value')}; not_observable={metric.get('not_observable', 0)}"
            )
    stage_latency = aggregate.get("stage_latency") or {}
    if stage_latency:
        lines.extend(["", "### Stage latency", ""])
        for stage, metric in stage_latency.items():
            cold = metric.get("cold_start") or {}
            warm = metric.get("warm") or {}
            lines.append(
                f"- {stage}: all P50/P95={metric.get('p50_ms')}/{metric.get('p95_ms')} ms; "
                f"cold n={cold.get('count')} P50/P95={cold.get('p50_ms')}/{cold.get('p95_ms')} ms; "
                f"warm n={warm.get('count')} P50/P95={warm.get('p50_ms')}/{warm.get('p95_ms')} ms"
            )
    failure_codes = aggregate.get("failure_codes") or {}
    if failure_codes:
        lines.extend(["", "### Failure code counts", ""])
        for code, count in failure_codes.items():
            lines.append(f"- `{code}`: {count}")
    diagnostic_codes = aggregate.get("structured_output_diagnostic_codes") or {}
    if diagnostic_codes:
        lines.extend(["", "### Structured-output diagnostics", ""])
        for code, count in diagnostic_codes.items():
            lines.append(f"- `{code}`: {count}")
    lines.extend(
        [
            f"- End-to-end latency P50/P95: {aggregate.get('elapsed_ms', {}).get('p50')} / {aggregate.get('elapsed_ms', {}).get('p95')} ms",
            f"- Not evaluable assertions (upstream prerequisite missing): {aggregate.get('not_evaluable_assertion_count', 0)}",
            f"- Model decisions: {aggregate.get('model_decision_count', 0)}",
            f"- Provider attempts: {aggregate.get('provider_attempt_count', report.metadata.get('actual_provider_attempts', 0))}",
            f"- Known tokens: input={aggregate.get('known_input_tokens', 0)}, output={aggregate.get('known_output_tokens', 0)}",
            f"- Token coverage: {aggregate.get('token_coverage_rate', {}).get('numerator', 0)}/"
            f"{aggregate.get('token_coverage_rate', {}).get('denominator', 0)} = "
            f"{aggregate.get('token_coverage_rate', {}).get('value')}",
            "",
            "## Cases",
            "",
            "| Case | Label | Outcome | Task | Failures |",
            "|---|---|---|---|---|",
        ]
    )
    for result in report.case_results:
        lines.append(
            f"| {result.case_id} | {result.label_status} | {result.actual_outcome} | {result.task_status} | {', '.join(result.failure_kinds) or '-'} |"
        )
    lines.extend(["", "## Failure details", ""])
    for result in report.case_results:
        if not result.failure_details:
            continue
        lines.append(f"### {result.case_id}")
        for detail in result.failure_details:
            stage = f" [{detail.stage}]" if detail.stage else ""
            metric = f" metric={detail.metric}" if detail.metric else ""
            lines.append(
                f"- `{detail.kind}` `{detail.code}`{stage}{metric}: {detail.details}"
            )
    return "\n".join(lines) + "\n"


class _EvaluationWeatherProvider:
    def __init__(self, *, rainy: bool, clock: datetime) -> None:
        self._rainy = rainy
        self._clock = clock

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        return WeatherFact(
            city=request.city,
            district=request.district,
            date=request.date,
            condition="中雨" if self._rainy else "晴",
            temperature_c=23.0,
            precipitation_mm=8.0 if self._rainy else 0.0,
            is_adverse=self._rainy,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=self._clock,
            verified_at=self._clock,
        )


class _EvaluationRouteProvider:
    def __init__(self, *, clock: datetime) -> None:
        self._clock = clock

    def route(self, request: RouteRequest) -> RouteFact:
        distance = _haversine_km(request.origin, request.destination)
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            distance_km=round(distance, 3),
            duration_minutes=max(5, math.ceil(distance * 5)),
            geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY,
            provider_mode=ProviderMode.REPLAY,
            verified_at=self._clock,
        )


class _EvaluationGeocodingProvider:
    def __init__(
        self,
        *,
        resolution: Literal["default", "resolved", "not_found", "ambiguous"],
        clock: datetime,
    ) -> None:
        self._resolution = (
            GeocodeResolution.NOT_FOUND
            if resolution == "default"
            else GeocodeResolution(resolution)
        )
        self._clock = clock

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        resolved = self._resolution == GeocodeResolution.RESOLVED
        return GeocodingFact(
            request=request,
            resolution=self._resolution,
            point=(
                GeoPoint(latitude=39.9219, longitude=116.4436)
                if resolved
                else None
            ),
            city=(request.city or "北京市") if resolved else None,
            district="朝阳区" if resolved else None,
            adcode="110105" if resolved else None,
            address=request.location_text if resolved else None,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=self._clock,
            verified_at=self._clock,
            verified=False,
        )


class _EvaluationAvailabilityProvider:
    def __init__(self, *, all_unavailable: bool, clock: datetime) -> None:
        self._all_unavailable = all_unavailable
        self._clock = clock

    def check(self, request) -> list[AvailabilityFact]:
        result: list[AvailabilityFact] = []
        for check in request.checks:
            status = (
                AvailabilityStatus.UNAVAILABLE
                if self._all_unavailable
                else AvailabilityStatus.AVAILABLE
            )
            result.append(
                AvailabilityFact(
                    resource_id=check.resource_id,
                    status=status,
                    source=ProviderSource.REPLAY,
                    mode=ProviderMode.REPLAY,
                    observed_at=self._clock,
                    verified_at=self._clock,
                    verified=True,
                    reason=(
                        "evaluation_fixture_unavailable"
                        if status == AvailabilityStatus.UNAVAILABLE
                        else "evaluation_fixture_available"
                    ),
                )
            )
        return result


def _haversine_km(left: GeoPoint, right: GeoPoint) -> float:
    from math import asin, cos, radians, sin, sqrt

    radius = 6371.0
    lat1, lat2 = radians(left.latitude), radians(right.latitude)
    dlat = radians(right.latitude - left.latitude)
    dlon = radians(right.longitude - left.longitude)
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return radius * 2 * asin(sqrt(max(0.0, min(1.0, value))))


def _clock_minutes(value: Any) -> int | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except ValueError:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute

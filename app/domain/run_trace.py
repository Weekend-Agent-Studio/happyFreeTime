"""Safe, public execution trace contracts.

The trace in this module describes what a planning run did at a coarse,
user-safe level.  It is deliberately separate from model diagnostics,
retrieval evidence, provider facts, and the planner's search trace.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RunStage(StrEnum):
    """Coarse execution stages shared by create, patch, clarify, and modify."""

    UNDERSTAND = "understand"
    COMPILE_REQUEST = "compile_request"
    CLARIFY = "clarify"
    RETRIEVE = "retrieve"
    CONSTRUCT = "construct"
    VERIFY = "verify"
    MODIFY = "modify"
    ADVISE = "advise"
    PERSIST = "persist"


class RunEventStatus(StrEnum):
    """Lifecycle state of one coarse execution stage."""

    STARTED = "started"
    COMPLETED = "completed"
    FALLBACK = "fallback"
    FAILED = "failed"
    WAITING_INPUT = "waiting_input"


# These are intentionally boring scalar/summary fields.  A public event must
# not become an escape hatch for prompts, provider payloads, model responses,
# exception text, or high-cardinality search diagnostics.
_PUBLIC_DETAIL_KEYS = frozenset(
    {
        "operation",
        "field",
        "mode",
        "source",
        "index_version",
        "fallback_code",
        "reason_code",
        "count",
        "candidate_count",
        "query_count",
        "verified_count",
        "selected_count",
        "plan_count",
    }
)
_FORBIDDEN_DETAIL_TERMS = frozenset(
    {
        "prompt",
        "response",
        "output",
        "input",
        "token",
        "secret",
        "credential",
        "header",
        "stack",
        "exception",
        "traceback",
        "thinking",
        "reasoning",
    }
)


def _validate_public_details(value: dict[str, Any]) -> dict[str, Any]:
    unknown = set(value) - _PUBLIC_DETAIL_KEYS
    if unknown:
        raise ValueError(
            "public trace detail keys are not allowed: "
            + ", ".join(sorted(unknown))
        )
    for key in value:
        normalized = key.casefold()
        if any(term in normalized for term in _FORBIDDEN_DETAIL_TERMS):
            raise ValueError(f"public trace detail key is sensitive: {key}")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("public trace details must be JSON serializable") from error
    return value


class PlanningRunEvent(BaseModel):
    """One safe, ordered event in a planning run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["planning-run-event.v1"] = "planning-run-event.v1"
    run_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    stage: RunStage
    status: RunEventStatus
    message_key: str = Field(min_length=1, max_length=80)
    public_message: str = Field(min_length=1, max_length=240)
    occurred_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    public_details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    _safe_details = field_validator("public_details")(_validate_public_details)


class PlanningRunTrace(BaseModel):
    """Immutable public trace returned with a completed planning run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["planning-run-trace.v1"] = "planning-run-trace.v1"
    run_id: str = Field(min_length=1, max_length=128)
    events: tuple[PlanningRunEvent, ...] = Field(default_factory=tuple, max_length=64)

    @model_validator(mode="after")
    def validate_event_order(self) -> "PlanningRunTrace":
        expected = 1
        for event in self.events:
            if event.run_id != self.run_id:
                raise ValueError("all trace events must belong to the trace run")
            if event.sequence != expected:
                raise ValueError("trace event sequences must be contiguous and ordered")
            expected += 1
        return self


class PlanningRunEventDraft(BaseModel):
    """Input accepted by a request-scoped observer before sequencing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: RunStage
    status: RunEventStatus
    message_key: str = Field(min_length=1, max_length=80)
    public_message: str = Field(min_length=1, max_length=240)
    duration_ms: int | None = Field(default=None, ge=0)
    public_details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    _safe_details = field_validator("public_details")(_validate_public_details)


class RunObserver(Protocol):
    """Small seam for request-scoped execution instrumentation."""

    def record(self, event: PlanningRunEventDraft) -> PlanningRunEvent:
        ...

    def snapshot(self) -> PlanningRunTrace:
        ...


class InMemoryRunObserver:
    """Collect a bounded trace without putting the observer in Graph State."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._events: list[PlanningRunEvent] = []

    def record(self, event: PlanningRunEventDraft) -> PlanningRunEvent:
        if len(self._events) >= 64:
            raise ValueError("planning run trace event limit exceeded")
        event_value = PlanningRunEvent(
            run_id=self._run_id,
            sequence=len(self._events) + 1,
            occurred_at=datetime.now().astimezone(),
            **event.model_dump(),
        )
        self._events.append(event_value)
        return event_value

    def snapshot(self) -> PlanningRunTrace:
        return PlanningRunTrace(run_id=self._run_id, events=tuple(self._events))

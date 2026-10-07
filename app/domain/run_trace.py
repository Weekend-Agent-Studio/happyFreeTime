"""Safe, public execution trace contracts.

The trace in this module describes what a planning run did at a coarse,
user-safe level.  It is deliberately separate from model diagnostics,
retrieval evidence, provider facts, and the planner's search trace.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RunStage(StrEnum):
    """Coarse execution stages shared by create, patch, clarify, and modify."""

    UNDERSTAND = "understand"
    COMPILE_REQUEST = "compile_request"
    CLARIFY = "clarify"
    STRUCTURE = "structure"
    RETRIEVE = "retrieve"
    CONSTRUCT = "construct"
    VERIFY = "verify"
    MODIFY = "modify"
    ADVISE = "advise"


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
_MAX_PUBLIC_DETAIL_STRING_LENGTH = 160


def _validate_public_detail_string(key: str, value: str) -> None:
    if len(value) > _MAX_PUBLIC_DETAIL_STRING_LENGTH:
        raise ValueError(
            f"public trace detail value is too long for {key}: "
            f"maximum {_MAX_PUBLIC_DETAIL_STRING_LENGTH} characters"
        )
    if any(ord(character) < 32 and character not in "\t" for character in value):
        raise ValueError(f"public trace detail value contains unsafe control characters: {key}")


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
        detail_value = value[key]
        if isinstance(detail_value, str):
            _validate_public_detail_string(key, detail_value)
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

    def __init__(self, run_id: str, *, on_event: Callable[[PlanningRunEvent], None] | None = None) -> None:
        self._run_id = run_id
        self._on_event = on_event
        self._events: list[PlanningRunEvent] = []
        self._stage_states: dict[RunStage, RunEventStatus] = {}
        self._stage_started_at: dict[RunStage, datetime] = {}

    def record(self, event: PlanningRunEventDraft) -> PlanningRunEvent:
        if len(self._events) >= 64:
            raise ValueError("planning run trace event limit exceeded")
        previous = self._stage_states.get(event.stage)
        terminal_statuses = {
            RunEventStatus.COMPLETED,
            RunEventStatus.FALLBACK,
            RunEventStatus.FAILED,
            RunEventStatus.WAITING_INPUT,
        }
        if event.status == RunEventStatus.STARTED:
            if previous is not None:
                raise ValueError(
                    f"trace stage {event.stage.value} cannot start twice"
                )
        elif event.status in terminal_statuses:
            if previous != RunEventStatus.STARTED:
                raise ValueError(
                    f"trace stage {event.stage.value} must start before it terminates"
                )
        else:  # pragma: no cover - guarded by the enum, kept for future statuses.
            raise ValueError(f"unsupported trace event status: {event.status}")

        occurred_at = datetime.now().astimezone()
        duration_ms = event.duration_ms
        if event.status == RunEventStatus.STARTED:
            self._stage_started_at[event.stage] = occurred_at
        else:
            started_at = self._stage_started_at[event.stage]
            duration_ms = max(
                0,
                round((occurred_at - started_at).total_seconds() * 1000),
            )
        event_value = PlanningRunEvent(
            run_id=self._run_id,
            sequence=len(self._events) + 1,
            occurred_at=occurred_at,
            **event.model_dump(exclude={"duration_ms"}),
            duration_ms=duration_ms,
        )
        self._events.append(event_value)
        self._stage_states[event.stage] = event.status
        if event.status in terminal_statuses:
            self._stage_started_at.pop(event.stage, None)
        if self._on_event is not None:
            self._on_event(event_value)
        return event_value

    def fail_open_stages(self) -> tuple[PlanningRunEvent, ...]:
        """Close stages left open by an unexpected execution error safely."""

        failed: list[PlanningRunEvent] = []
        for stage in tuple(self._stage_started_at):
            failed.append(
                self.record(
                    PlanningRunEventDraft(
                        stage=stage,
                        status=RunEventStatus.FAILED,
                        message_key=f"{stage.value}.failed",
                        public_message="本阶段未能完成，规划已安全停止",
                        public_details={"reason_code": "internal_error"},
                    )
                )
            )
        return tuple(failed)

    def snapshot(self) -> PlanningRunTrace:
        return PlanningRunTrace(run_id=self._run_id, events=tuple(self._events))

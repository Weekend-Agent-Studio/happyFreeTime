"""Runtime decisions exposed as safe, user-facing execution evidence.

The model payload, prompts, credentials and provider headers never belong in a
response trace.  This small contract records only which adapter actually ran,
whether a model was invoked, and how the bounded attempt ended.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RuntimeDecision(BaseModel):
    """One bounded decision made during a request.

    ``None`` token fields mean the underlying provider did not expose usage;
    the application deliberately does not estimate token counts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal[
        "turn_interpreter",
        "planning_intent",
        "candidate_retrieval",
        "recommendation_advisor",
    ]
    adapter: str = Field(min_length=1)
    model_invoked: bool
    model_name: str | None = None
    attempts: int = Field(ge=0, le=2)
    fallback_reason: str | None = None
    # Safe diagnostics for a received structured response.  These fields are
    # deliberately codes/paths/types only; provider payloads and exception
    # messages never cross the runtime boundary.
    diagnostic_code: str | None = Field(default=None, max_length=80)
    diagnostic_paths: tuple[str, ...] = ()
    diagnostic_error_types: tuple[str, ...] = ()
    # Version the model-facing contract independently from the persisted
    # Interpretation domain object.  These are safe trace metadata only.
    wire_schema_version: str | None = None
    prompt_version: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    requested_mode: str | None = None
    index_version: str | None = None
    query_count: int | None = Field(default=None, ge=0)
    candidate_count: int | None = Field(default=None, ge=0)

"""Reviewed, hash-pinned Interpretation fixtures for controlled evaluations.

This module is intentionally evaluation-only.  It never falls back to the
DemoRouter or a live model: a missing, draft, or hash-mismatched fixture is a
configuration/runner error.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.constraints import Interpretation
from app.domain.runtime import RuntimeDecision


INTERPRETATION_SCHEMA_VERSION = "interpretation.v1"
FROZEN_FIXTURE_SET_SCHEMA_VERSION = "frozen-interpretations.v1"


class FrozenFixtureError(ValueError):
    """A safe, deterministic fixture validation or lookup failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FrozenInterpretationFixture(BaseModel):
    """One reviewed Interpretation tied to one case step and input hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    step_index: int = Field(ge=0)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interpretation_schema_version: Literal["interpretation.v1"] = (
        INTERPRETATION_SCHEMA_VERSION
    )
    interpretation: Interpretation
    generated_by_model: str = Field(min_length=1, max_length=120)
    generated_at: datetime
    review_status: Literal["draft", "reviewed"]
    review_note: str = Field(default="", max_length=500)


class FrozenInterpretationSet(BaseModel):
    """Versioned fixture file; entries must be unique by case and step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["frozen-interpretations.v1"] = (
        FROZEN_FIXTURE_SET_SCHEMA_VERSION
    )
    fixture_set_id: str = Field(min_length=1, max_length=120)
    fixtures: tuple[FrozenInterpretationFixture, ...] = ()

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "FrozenInterpretationSet":
        keys = [(item.case_id, item.step_index) for item in self.fixtures]
        if len(keys) != len(set(keys)):
            raise ValueError("frozen Interpretation fixture keys must be unique")
        return self


def input_sha256(user_input: str) -> str:
    """Hash one exact UTF-8 user input without retaining the input itself."""

    return hashlib.sha256(user_input.encode("utf-8")).hexdigest()


def frozen_fixture_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_interpretations(
    path: Path,
    *,
    require_reviewed: bool = True,
) -> FrozenInterpretationSet:
    """Load and validate a fixture file without accepting unreviewed entries."""

    if not path.exists() or not path.is_file():
        raise FrozenFixtureError("fixture_set_missing", "frozen fixture set is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fixture_set = FrozenInterpretationSet.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        # Do not expose parser text or fixture contents to the evaluator.
        raise FrozenFixtureError(
            "fixture_set_invalid", "frozen fixture set is invalid"
        ) from error
    if require_reviewed and any(
        item.review_status != "reviewed" for item in fixture_set.fixtures
    ):
        raise FrozenFixtureError(
            "fixture_draft_rejected", "frozen fixture set contains draft entries"
        )
    return fixture_set


class FrozenTurnInterpreter:
    """Evaluation adapter that serves only the case's hash-pinned fixtures."""

    def __init__(self, fixture_set: FrozenInterpretationSet, *, case_id: str) -> None:
        self._case_id = case_id
        self._fixtures = tuple(
            item for item in fixture_set.fixtures if item.case_id == case_id
        )
        self._used_steps: set[int] = set()

    def interpret_with_runtime(
        self,
        user_input: str,
        context: object,
    ) -> tuple[Interpretation, RuntimeDecision]:
        fixture = self._lookup(user_input)
        return fixture.interpretation, RuntimeDecision(
            stage="turn_interpreter",
            adapter="frozen_fixture",
            model_invoked=False,
            model_name=fixture.generated_by_model,
            attempts=0,
            latency_ms=0,
        )

    def interpret(self, user_input: str, context: object) -> Interpretation:
        interpretation, _ = self.interpret_with_runtime(user_input, context)
        return interpretation

    def _lookup(self, user_input: str) -> FrozenInterpretationFixture:
        actual_hash = input_sha256(user_input)
        candidates = [
            item
            for item in self._fixtures
            if item.step_index not in self._used_steps
            and item.input_sha256 == actual_hash
        ]
        if candidates:
            fixture = min(candidates, key=lambda item: item.step_index)
            self._used_steps.add(fixture.step_index)
            return fixture
        same_case_unused = [
            item for item in self._fixtures if item.step_index not in self._used_steps
        ]
        if same_case_unused:
            raise FrozenFixtureError(
                "fixture_hash_mismatch", "frozen fixture input hash does not match"
            )
        raise FrozenFixtureError(
            "fixture_missing", "no unused frozen fixture matches this case input"
        )


def fixture_coverage(
    fixture_set: FrozenInterpretationSet,
    case_steps: dict[str, tuple[tuple[int, str], ...]],
) -> tuple[int, int, int]:
    """Return ``(missing, hash_mismatch, covered)`` for message steps."""

    by_key = {
        (item.case_id, item.step_index): item for item in fixture_set.fixtures
    }
    missing = 0
    mismatch = 0
    covered = 0
    for case_id, steps in case_steps.items():
        for step_index, user_input in steps:
            item = by_key.get((case_id, step_index))
            if item is None:
                missing += 1
            elif item.input_sha256 != input_sha256(user_input):
                mismatch += 1
            else:
                covered += 1
    return missing, mismatch, covered


def safe_fixture_set_id(value: str) -> str:
    """Keep fixture identifiers bounded when copied into report metadata."""

    return re.sub(r"[^A-Za-z0-9_.:-]", "_", value)[:120] or "unknown"

"""Provider-reported token usage for bounded model calls.

Usage is deliberately best-effort: when any invoked attempt omits a token
field, the aggregate remains unknown instead of presenting an undercount as a
complete request cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ModelTokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class TokenUsageAccumulator:
    _attempts: list[ModelTokenUsage] = field(default_factory=list)

    def record(self, result: object) -> None:
        self._attempts.append(extract_token_usage(result))

    def record_unknown(self) -> None:
        self._attempts.append(ModelTokenUsage())

    @property
    def attempt_count(self) -> int:
        return len(self._attempts)

    @property
    def total(self) -> ModelTokenUsage:
        return ModelTokenUsage(
            input_tokens=_complete_sum(self._attempts, "input_tokens"),
            output_tokens=_complete_sum(self._attempts, "output_tokens"),
        )


def extract_token_usage(result: object) -> ModelTokenUsage:
    """Read LangChain/OpenAI-compatible usage without exposing raw metadata."""

    raw = result.get("raw") if isinstance(result, Mapping) and "raw" in result else result
    usage = _mapping_value(raw, "usage_metadata")
    if usage is not None:
        return ModelTokenUsage(
            input_tokens=_token_value(usage, "input_tokens", "prompt_tokens"),
            output_tokens=_token_value(usage, "output_tokens", "completion_tokens"),
        )

    response_metadata = _mapping_value(raw, "response_metadata")
    token_usage = _mapping_value(response_metadata, "token_usage")
    return ModelTokenUsage(
        input_tokens=_token_value(token_usage, "input_tokens", "prompt_tokens"),
        output_tokens=_token_value(token_usage, "output_tokens", "completion_tokens"),
    )


def _mapping_value(value: object, key: str) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        candidate = value.get(key)
    else:
        candidate = getattr(value, key, None)
    return candidate if isinstance(candidate, Mapping) else None


def _token_value(
    usage: Mapping[str, object] | None,
    *keys: str,
) -> int | None:
    if usage is None:
        return None
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _complete_sum(attempts: list[ModelTokenUsage], field_name: str) -> int | None:
    if not attempts:
        return None
    values = [getattr(attempt, field_name) for attempt in attempts]
    if any(value is None for value in values):
        return None
    return sum(values)

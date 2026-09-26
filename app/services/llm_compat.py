"""Small compatibility helpers for OpenAI-compatible model providers."""

from __future__ import annotations

from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool


_QWEN_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "propertyNames",
        "patternProperties",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
    }
)


def is_qwen_model(model_name: str) -> bool:
    """Return whether a model name uses Qwen's compatible API extensions."""

    return model_name.strip().casefold().startswith("qwen")

def thinking_extra_body(model_name: str) -> dict[str, Any]:
    """Return the provider-specific body used to disable reasoning.

    Qwen's OpenAI-compatible API exposes this switch as ``enable_thinking``.
    The ``thinking`` object used by the existing DeepSeek path is not a valid
    Qwen request field, so sending it to Qwen can fail before generation.
    Keep the existing payload for non-Qwen models to avoid changing their
    established compatibility contract.
    """

    if is_qwen_model(model_name):
        return {"enable_thinking": False}
    return {"thinking": {"type": "disabled"}}


def structured_output_schema(model_name: str, schema: Any) -> Any:
    """Build the wire schema accepted by the configured provider.

    Qwen's structured-output validator rejects a few valid JSON Schema
    keywords, notably ``propertyNames`` and ``uniqueItems``.  Keep the domain
    Pydantic schema as the final validator, but remove only those wire-level
    restrictions for Qwen.  Non-Qwen providers receive the original schema
    object and therefore retain the existing Function Calling behavior.
    """

    if not is_qwen_model(model_name):
        return schema
    tool_schema = convert_to_openai_tool(schema)
    return _strip_qwen_unsupported_schema_keys(tool_schema)


def _strip_qwen_unsupported_schema_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_qwen_unsupported_schema_keys(item)
            for key, item in value.items()
            if key not in _QWEN_UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_strip_qwen_unsupported_schema_keys(item) for item in value]
    return value

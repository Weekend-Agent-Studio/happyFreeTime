"""Safe, bounded classification for failures around structured LLM calls.

The product only needs a small operational taxonomy.  Exception messages can
contain provider payloads (and, in badly behaved clients, sensitive request
details), so callers should use :func:`model_failure_reason` instead of
persisting ``str(error)``.
"""

from __future__ import annotations

import re
from typing import Any


_STATUS_RE = re.compile(r"(?:status[_ ]?code|http)[ :=]+(\d{3})", re.IGNORECASE)


def model_failure_reason(error: BaseException) -> str:
    """Return a stable category and, when safe, the provider HTTP status.

    The classifier intentionally relies on exception type names and bounded
    status-code attributes rather than recording the exception message.  It
    works with OpenAI/httpx clients without importing either optional package.
    """

    status = _safe_status_code(error)
    error_name = type(error).__name__.casefold()
    error_text = _safe_error_text(error)

    if (
        isinstance(error, TimeoutError)
        or "timeout" in error_name
        or "timedout" in error_name
        or "deadline exceeded" in error_text
        or status in {408, 504}
    ):
        return _with_status("timeout", status)

    if (
        status in {401, 403}
        or any(
            marker in error_name or marker in error_text
            for marker in (
                "authentication",
                "unauthorized",
                "permissiondenied",
                "permission denied",
                "invalid api key",
                "forbidden",
            )
        )
    ):
        return _with_status("auth_error", status)

    if (
        status == 429
        or any(
            marker in error_name or marker in error_text
            for marker in (
                "ratelimit",
                "rate limit",
                "too many requests",
                "quota exceeded",
            )
        )
    ):
        return _with_status("rate_limited", status)

    if status is not None and 500 <= status <= 599:
        return _with_status("provider_error", status)

    if any(
        marker in error_name or marker in error_text
        for marker in (
            "connection",
            "connecterror",
            "network",
            "dns",
            "transport",
            "remoteprotocol",
            "readerror",
            "sslerror",
        )
    ):
        return "network_error"

    if is_structured_output_error(error):
        return "invalid_output"

    # Unknown client/provider failures are still bounded and observable, but
    # never expose their potentially sensitive message.
    return _with_status("provider_error", status)


def is_structured_output_error(error: BaseException) -> bool:
    """Best-effort detection for parser/validation exceptions.

    This is useful only for classifying an exception that happened while
    decoding a successful model response.  Transport/auth failures must never
    be retried based on a substring in their provider message.
    """

    error_name = type(error).__name__.casefold()
    error_text = _safe_error_text(error)
    return any(
        marker in error_name or marker in error_text
        for marker in (
            "outputparser",
            "parseerror",
            "parsing",
            "jsondecode",
            "invalid json",
            "invalid output",
            "schema validation",
            "validation error",
            "pydantic",
        )
    )


def _safe_status_code(error: BaseException) -> int | None:
    """Read only a bounded numeric status code from common client errors."""

    candidates: list[Any] = [getattr(error, "status_code", None)]
    response = getattr(error, "response", None)
    if response is not None:
        candidates.append(getattr(response, "status_code", None))
    # Some lightweight fakes only expose the code in a short exception type
    # string.  Parse digits, never the whole message.
    candidates.append(_STATUS_RE.search(type(error).__name__))
    for candidate in candidates:
        if hasattr(candidate, "group"):
            candidate = candidate.group(1)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and 100 <= candidate <= 599:
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            value = int(candidate)
            if 100 <= value <= 599:
                return value
    return None


def _safe_error_text(error: BaseException) -> str:
    """Use text only for in-process classification; never return it to callers."""

    try:
        return str(error).casefold()[:512]
    except Exception:
        return ""


def _with_status(category: str, status: int | None) -> str:
    if status is None:
        return category
    return f"{category}:status={status}"

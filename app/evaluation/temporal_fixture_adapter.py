"""Evaluation-only adapter for immutable fixtures created before TimeProposal.

The production wire/domain contract deliberately rejects the retired temporal
fields. Reviewed fixtures remain byte-for-byte hash-pinned; this adapter
translates their old payload only after loading them at the evaluation boundary.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.domain.constraints import TimeScope
from app.services.enrichment import TemporalCompiler


_LEGACY_TIME_FIELDS = (
    "time_text",
    "time_scope",
    "explicit_time_window",
    "departure_at_text",
    "departure_at",
    "departure_period",
    "return_by_text",
    "return_by",
)


def adapt_legacy_interpretation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an old serialized Interpretation without changing its source file."""
    adapted = deepcopy(payload)
    raw, proposals = adapt_legacy_temporal_payload(adapted.get("raw_constraints", {}))
    # Frozen v1 fixtures used one ambiguous location field.  Keep the source
    # files hash-pinned, but translate that retired evaluation shape at the
    # loader seam so the production Router contract can stay scope-explicit.
    if "location_text" in raw:
        raw["origin_text"] = raw.pop("location_text")
    adapted["raw_constraints"] = raw
    if not adapted.get("time_proposals"):
        adapted["time_proposals"] = proposals
    return adapted


def adapt_legacy_temporal_payload(
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return non-temporal raw constraints and bounded temporal proposals.

    Conversion follows the old fixture's explicit fields first, then its former
    trip scope. It does not run in application code and does not guess from a
    free-form user message.
    """
    raw = deepcopy(raw_payload)
    proposals: list[dict[str, Any]] = []
    time_text = raw.get("time_text")

    explicit_window = raw.get("explicit_time_window")
    if isinstance(explicit_window, dict):
        start = explicit_window.get("start")
        end = explicit_window.get("end")
        if start and end:
            proposals.append(
                {
                    "target": "trip",
                    "precision": "exact",
                    "clock": start,
                    "end_clock": end,
                    "evidence": time_text or f"{start}-{end}",
                }
            )

    departure_text = raw.get("departure_at_text")
    departure_value = raw.get("departure_at")
    departure_clock = (
        _normalize_clock(departure_value)
        or _normalize_clock(departure_text)
    )
    if departure_clock:
        proposals.append(
            {
                "target": "departure",
                "precision": "exact",
                "clock": departure_clock,
                "evidence": departure_text or str(departure_value),
            }
        )
    elif departure_text:
        departure_period = TemporalCompiler.extract_departure_period(departure_text)
        if departure_period is not None:
            proposals.append(
                {
                    "target": "departure",
                    "precision": "period",
                    "period": departure_period.value,
                    "evidence": departure_text,
                }
            )

    return_text = raw.get("return_by_text")
    return_value = raw.get("return_by")
    return_clock = _normalize_clock(return_value) or _normalize_clock(return_text)
    if return_clock:
        proposals.append(
            {
                "target": "return",
                "precision": "exact",
                "clock": return_clock,
                "evidence": return_text or str(return_value),
            }
        )
    elif return_text:
        period = _return_period(return_text)
        if period is not None:
            proposals.append(
                {
                    "target": "return",
                    "precision": "period",
                    "period": period.value,
                    "evidence": return_text,
                }
            )

    if not any(item["target"] == "trip" for item in proposals):
        scope_value = raw.get("time_scope")
        try:
            scope = TimeScope(scope_value) if scope_value else None
        except ValueError:
            scope = None
        if scope == TimeScope.EXPLICIT_RANGE and time_text:
            _, _, parsed_window = TemporalCompiler.extract_time(time_text)
            if parsed_window is not None:
                proposals.append(
                    {
                        "target": "trip",
                        "precision": "exact",
                        "clock": parsed_window.start,
                        "end_clock": parsed_window.end,
                        "evidence": time_text,
                    }
                )
        elif scope is not None and scope != TimeScope.EXPLICIT_RANGE:
            proposals.append(
                {
                    "target": "trip",
                    "precision": "period",
                    "period": scope.value,
                    "evidence": time_text or scope.value,
                }
            )

    for field in _LEGACY_TIME_FIELDS:
        raw.pop(field, None)
    if "location_text" in raw:
        raw["origin_text"] = raw.pop("location_text")
    return raw, proposals


def _normalize_clock(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"\s*\d{1,2}[:：]\d{2}\s*", value):
        return TemporalCompiler.normalize_clock_text(value)
    # TemporalCompiler accepts a bare Chinese numeral for field-directed
    # clarification answers. Fixture prose needs an explicit clock marker so
    # words such as “一定” cannot be misread as 01:00.
    if not re.search(
        r"(?:上午|早上|中午|下午|晚上|夜里|夜间)?\s*"
        r"(?:\d{1,2}[:：]\d{2}|[零〇一二两三四五六七八九十]+点"
        r"(?:半|[零〇一二两三四五六七八九十]+分?)?)",
        value,
    ):
        return None
    return TemporalCompiler.normalize_clock_text(value)


def _return_period(value: str) -> TimeScope | None:
    if "午饭" in value or "午餐" in value:
        return TimeScope.AFTERNOON
    if any(term in value for term in ("晚饭", "晚餐", "天黑", "晚上")):
        return TimeScope.EVENING
    return None

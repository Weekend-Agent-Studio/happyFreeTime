"""Conservative helpers for the curated Catalog's basic opening-hours grammar."""

from __future__ import annotations


def visit_fits_opening_hours(
    hours: str,
    visit_start: str,
    visit_end: str,
) -> bool | None:
    """Return whether a visit fits known hours, or ``None`` for unsupported data."""
    intervals = parse_basic_intervals(hours)
    if intervals is None:
        return None
    visit = _parse_interval(f"{visit_start}-{visit_end}")
    if visit is None:
        return None
    start_minutes, end_minutes = visit
    return any(
        open_minutes <= start_minutes and end_minutes <= close_minutes
        for open_minutes, close_minutes in intervals
    )


def opening_hours_overlap(
    hours: str,
    window_start: str,
    window_end: str,
) -> bool | None:
    """Return basic-window overlap, or ``None`` when the grammar is unsupported."""
    intervals = parse_basic_intervals(hours)
    window = _parse_interval(f"{window_start}-{window_end}")
    if intervals is None or window is None:
        return None
    window_start_minutes, window_end_minutes = window
    return any(
        max(open_minutes, window_start_minutes)
        < min(close_minutes, window_end_minutes)
        for open_minutes, close_minutes in intervals
    )


def parse_basic_intervals(value: str) -> tuple[tuple[int, int], ...] | None:
    """Parse comma-separated same-day intervals without guessing OSM extensions."""
    try:
        raw_intervals = [part.strip() for part in value.split(",")]
    except (AttributeError, TypeError):
        return None
    if not raw_intervals or any(not part for part in raw_intervals):
        return None
    parsed: list[tuple[int, int]] = []
    for raw_interval in raw_intervals:
        interval = _parse_interval(raw_interval)
        if interval is None:
            return None
        parsed.append(interval)
    return tuple(parsed)


def _parse_interval(value: str) -> tuple[int, int] | None:
    try:
        start, end = (part.strip() for part in value.split("-", 1))
        start_minutes = _minutes(start)
        end_minutes = _minutes(end)
    except (AttributeError, TypeError, ValueError):
        return None
    if start_minutes >= end_minutes:
        return None
    return start_minutes, end_minutes


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("invalid clock time")
    return hour * 60 + minute

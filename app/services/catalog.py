"""Catalog seam: normalize resources and prune single-resource violations."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Protocol

from app.domain.catalog import (
    CatalogSource,
    CatalogResult,
    ConstraintViolation,
    ResourceType,
    StopCandidate,
    ViolationCode,
)
from app.domain.providers import GeoPoint
from app.domain.constraints import NormalizedConstraints


class Catalog(Protocol):
    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        ...


class InMemoryCatalog:
    def __init__(self, candidates: list[StopCandidate]) -> None:
        self._candidates = [candidate.model_copy(deep=True) for candidate in candidates]

    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        kept: list[StopCandidate] = []
        violations: list[ConstraintViolation] = []
        for candidate in self._candidates:
            candidate_violations = _violations_for(candidate, constraints)
            if candidate_violations:
                violations.extend(candidate_violations)
            else:
                kept.append(candidate.model_copy(deep=True))
        return CatalogResult(candidates=kept, violations=violations)


class LocalFixtureCatalog:
    """Normalize the repository's explicitly unverified local fixture data."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or Path(__file__).resolve().parents[2] / "data"

    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        source_payload = _read_json(self._data_dir / "catalog_sources.json")
        candidates: list[StopCandidate] = []
        for filename, resource_type in (
            ("activities.json", ResourceType.ACTIVITY),
            ("restaurants.json", ResourceType.RESTAURANT),
        ):
            source = CatalogSource.model_validate(source_payload[filename])
            records = _read_json(self._data_dir / filename)
            candidates.extend(
                _normalize_record(record, resource_type, source)
                for record in records
            )
        return InMemoryCatalog(candidates).recall(constraints)


def _violations_for(
    candidate: StopCandidate,
    constraints: NormalizedConstraints,
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    party = constraints.party.value if constraints.party else None
    people = party.adults + party.children if party else 1

    if (
        constraints.strict_budget
        and constraints.budget_per_person
        and candidate.avg_price > constraints.budget_per_person.value
    ):
        violations.append(
            _violation(
                candidate,
                ViolationCode.SINGLE_RESOURCE_BUDGET_EXCEEDED,
                "budget_per_person",
                "单资源人均价格超过严格预算。",
            )
        )

    if candidate.max_party_size is not None and people > candidate.max_party_size:
        violations.append(
            _violation(
                candidate,
                ViolationCode.PARTY_TOO_LARGE,
                "party",
                "同行人数超过该资源可容纳人数。",
            )
        )

    if party and party.children > 0:
        if candidate.children_allowed is False:
            violations.append(
                _violation(
                    candidate,
                    ViolationCode.CHILD_NOT_SUPPORTED,
                    "party.child_age",
                    "该资源明确不接待儿童。",
                )
            )
        elif party.child_age is not None and not _supports_child_age(
            candidate,
            party.child_age,
        ):
            violations.append(
                _violation(
                    candidate,
                    ViolationCode.CHILD_AGE_NOT_SUPPORTED,
                    "party.child_age",
                    "儿童年龄不在该资源支持范围内。",
                )
            )

    if constraints.date and constraints.time_window:
        weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[
            constraints.date.value.weekday()
        ]
        hours = candidate.open_hours.get(weekday)
        window = constraints.time_window.value
        if not hours or not _overlaps(hours, window.start, window.end):
            violations.append(
                _violation(
                    candidate,
                    ViolationCode.OUTSIDE_BASIC_OPENING_HOURS,
                    "date",
                    "该资源在请求日期和基础时间窗内不可用。",
                )
            )

    if constraints.location and constraints.max_distance_km:
        origin = constraints.location.value
        straight_line_km = _haversine_km(
            origin.latitude,
            origin.longitude,
            candidate.location.latitude,
            candidate.location.longitude,
        )
        if straight_line_km > constraints.max_distance_km.value:
            violations.append(
                _violation(
                    candidate,
                    ViolationCode.OUTSIDE_RECALL_RADIUS,
                    "max_distance_km",
                    "候选与出发地的直线距离已超过最大范围。",
                )
            )

    return violations


def _supports_child_age(candidate: StopCandidate, child_age: int) -> bool:
    if candidate.child_age_min is not None and child_age < candidate.child_age_min:
        return False
    if candidate.child_age_max is not None and child_age > candidate.child_age_max:
        return False
    return True


def _overlaps(hours: str, window_start: str, window_end: str) -> bool:
    try:
        open_time, close_time = hours.split("-", 1)
        return max(_minutes(open_time), _minutes(window_start)) < min(
            _minutes(close_time),
            _minutes(window_end),
        )
    except (TypeError, ValueError):
        return False


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _haversine_km(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> float:
    earth_radius_km = 6371.0088
    start_latitude_radians = math.radians(start_latitude)
    end_latitude_radians = math.radians(end_latitude)
    latitude_delta = math.radians(end_latitude - start_latitude)
    longitude_delta = math.radians(end_longitude - start_longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(start_latitude_radians)
        * math.cos(end_latitude_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(haversine))


def _violation(
    candidate: StopCandidate,
    code: ViolationCode,
    field: str,
    message: str,
) -> ConstraintViolation:
    return ConstraintViolation(
        resource_id=candidate.resource_id,
        resource_name=candidate.name,
        code=code,
        field=field,
        message=message,
    )


def _normalize_record(
    record: dict,
    resource_type: ResourceType,
    source: CatalogSource,
) -> StopCandidate:
    child_age_min, child_age_max = _child_age_range(record)
    children_allowed = _children_allowed(record)
    supported_party_sizes = record.get("party_size_supported", [])
    return StopCandidate(
        resource_id=record["id"],
        resource_type=resource_type,
        name=record["name"],
        district=record.get("district", ""),
        address=record.get("address", ""),
        location=GeoPoint(
            latitude=record["lat"],
            longitude=record["lng"],
        ),
        avg_price=record.get("avg_price", 0),
        duration_minutes=record.get("duration_minutes", 60),
        open_hours=record.get("open_hours", {}),
        max_party_size=(
            max(supported_party_sizes) if supported_party_sizes else None
        ),
        children_allowed=children_allowed,
        child_age_min=child_age_min,
        child_age_max=child_age_max,
        weather_sensitive=record.get("weather_sensitive", False),
        packages=record.get("packages", []),
        booking_required=record.get("booking_required", False),
        reservation_required=record.get("reservation_required", False),
        source=source,
    )


def _children_allowed(record: dict) -> bool | None:
    suitable = set(record.get("suitable_for", []))
    not_suitable = set(record.get("not_suitable_for", []))
    if "family" in not_suitable or any(
        value.startswith("children_") for value in not_suitable
    ):
        return False
    if "family" in suitable or any(
        value.startswith("children_") for value in suitable
    ):
        return True
    return None


def _child_age_range(record: dict) -> tuple[int | None, int | None]:
    for value in record.get("suitable_for", []):
        if not value.startswith("children_"):
            continue
        try:
            minimum, maximum = value.removeprefix("children_").split("_", 1)
            return int(minimum), int(maximum)
        except ValueError:
            continue
    return None, None


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

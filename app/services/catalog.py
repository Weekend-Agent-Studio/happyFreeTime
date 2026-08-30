"""Catalog seam: normalize resources and prune single-resource violations."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.domain.catalog import (
    CatalogSource,
    CatalogResult,
    CatalogWarning,
    CatalogWarningCode,
    CoordinateSystem,
    ConstraintViolation,
    ImageRef,
    PriceKind,
    ResourceType,
    StopCandidate,
    ViolationCode,
)
from app.domain.providers import GeoPoint
from app.domain.constraints import NormalizedConstraints
from app.services.opening_hours import opening_hours_overlap, parse_basic_intervals


class Catalog(Protocol):
    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        ...


class CatalogDataError(ValueError):
    """The curated catalog cannot be trusted or normalized safely."""


class InMemoryCatalog:
    def __init__(self, candidates: list[StopCandidate]) -> None:
        self._candidates = [candidate.model_copy(deep=True) for candidate in candidates]

    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        kept: list[StopCandidate] = []
        violations: list[ConstraintViolation] = []
        warnings: list[CatalogWarning] = []
        for candidate in self._candidates:
            candidate_violations = _violations_for(candidate, constraints)
            if candidate_violations:
                violations.extend(candidate_violations)
            else:
                kept.append(candidate.model_copy(deep=True))
                warnings.extend(_warnings_for(candidate, constraints))
        return CatalogResult(candidates=kept, violations=violations, warnings=warnings)


class LocalFixtureCatalog:
    """Normalize the repository's explicitly unverified local fixture data."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = (
            data_dir
            or Path(__file__).resolve().parents[2] / "data" / "fixtures" / "v1"
        )

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


class SnapshotCatalog:
    """Load the generated OSM snapshot behind the stable Catalog seam."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(__file__).resolve().parents[2] / "data" / "catalog" / "pois.json"
        self._candidates = self._load()
        self._delegate = InMemoryCatalog(self._candidates)

    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        return self._delegate.recall(constraints)

    def load_candidates(self) -> list[StopCandidate]:
        """Return a defensive anchor copy for adapters such as DemoCatalog."""
        return [candidate.model_copy(deep=True) for candidate in self._candidates]

    def _load(self) -> list[StopCandidate]:
        try:
            payload = _read_json(self._path)
            manifest = payload["manifest"]
            records = payload["records"]
            source_manifest = manifest["source"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CatalogDataError(f"cannot read catalog snapshot {self._path}: {exc}") from exc

        if manifest.get("record_count") != len(records):
            raise CatalogDataError(f"{self._path}: manifest record_count does not match records")
        try:
            coordinate_system = CoordinateSystem(source_manifest["coordinate_system"])
            collected_at = manifest["collected_at"]
            source_name = source_manifest["name"]
            source_license = source_manifest["license"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogDataError(f"{self._path}: invalid manifest: {exc}") from exc

        candidates: list[StopCandidate] = []
        seen: set[str] = set()
        for index, record in enumerate(records, start=1):
            resource_id = record.get("resource_id", "")
            if not resource_id or resource_id in seen:
                raise CatalogDataError(f"{self._path}: invalid or duplicate resource_id {resource_id!r}")
            seen.add(resource_id)
            try:
                latitude = float(record["latitude"])
                longitude = float(record["longitude"])
                if coordinate_system == CoordinateSystem.WGS84:
                    latitude, longitude = _wgs84_to_gcj02(latitude, longitude)
                source = CatalogSource(
                    source_name=source_name,
                    source_uri=record["source_uri"],
                    source_license=source_license,
                    collected_at=collected_at,
                    last_verified_at=record.get("last_verified_at"),
                    verification_status=record.get("verification_status", "unverified"),
                )
                candidates.append(
                    StopCandidate(
                        resource_id=resource_id,
                        resource_type=record["resource_type"],
                        name=record["name"],
                        district=record.get("district", ""),
                        address=record.get("address", ""),
                        location=GeoPoint(latitude=latitude, longitude=longitude),
                        coordinate_system=CoordinateSystem.GCJ02,
                        category_tags=record.get("category_tags", []),
                        preference_tags=record.get("preference_tags", []),
                        diet_tags=record.get("diet_tags", []),
                        scene_tags=record.get("scene_tags", []),
                        avg_price=record.get("avg_price_yuan"),
                        price_kind=record.get("price_kind", "unknown"),
                        duration_minutes=record["duration_minutes"],
                        open_hours=record.get("open_hours", {}),
                        max_party_size=record.get("max_party_size"),
                        children_allowed=record.get("children_allowed"),
                        child_age_min=record.get("child_age_min"),
                        child_age_max=record.get("child_age_max"),
                        weather_sensitive=record.get("weather_sensitive", False),
                        booking_required=record.get("booking_required", False),
                        reservation_required=record.get("reservation_required", False),
                        image=(ImageRef.model_validate(record["image"]) if record.get("image") else None),
                        source=source,
                    )
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise CatalogDataError(f"{self._path}: record {index}: {exc}") from exc
        return candidates


def _violations_for(
    candidate: StopCandidate,
    constraints: NormalizedConstraints,
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    party = constraints.party.value if constraints.party else None
    people = party.adults + party.children if party else 1

    if constraints.strict_budget and constraints.budget_per_person:
        if candidate.price_kind not in {PriceKind.KNOWN, PriceKind.FREE}:
            violations.append(
                _violation(
                    candidate,
                    ViolationCode.SINGLE_RESOURCE_PRICE_UNVERIFIED,
                    "budget_per_person",
                    "单资源价格未经可靠核验，不能用于证明满足严格预算。",
                )
            )
        elif (
            candidate.avg_price is not None
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
        if hours and not _overlaps(hours, window.start, window.end):
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


def _warnings_for(
    candidate: StopCandidate,
    constraints: NormalizedConstraints,
) -> list[CatalogWarning]:
    warnings: list[CatalogWarning] = []
    party = constraints.party.value if constraints.party else None
    if party and party.children > 0 and candidate.children_allowed is None:
        warnings.append(
            _warning(
                candidate,
                CatalogWarningCode.CHILD_SUITABILITY_UNVERIFIED,
                "party.child_age",
                "该地点没有可靠的儿童适用性信息，请出发前确认。",
            )
        )
    if constraints.date and constraints.time_window:
        weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[
            constraints.date.value.weekday()
        ]
        hours = candidate.open_hours.get(weekday)
        if not hours or parse_basic_intervals(hours) is None:
            warnings.append(
                _warning(
                    candidate,
                    CatalogWarningCode.OPENING_HOURS_UNVERIFIED,
                    "date",
                    "该地点在请求日期的营业时间未知，请出发前确认。",
                )
            )
    return warnings


def _supports_child_age(candidate: StopCandidate, child_age: int) -> bool:
    if candidate.child_age_min is not None and child_age < candidate.child_age_min:
        return False
    if candidate.child_age_max is not None and child_age > candidate.child_age_max:
        return False
    return True


def _overlaps(hours: str, window_start: str, window_end: str) -> bool:
    # Unsupported expressions are unknown facts, not proof that a place is closed.
    return opening_hours_overlap(hours, window_start, window_end) is not False


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


def _warning(
    candidate: StopCandidate,
    code: CatalogWarningCode,
    field: str,
    message: str,
) -> CatalogWarning:
    return CatalogWarning(
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
        preference_tags=record.get("tags", []),
        diet_tags=record.get("diet_tags", []),
        scene_tags=record.get("scene_tags", []),
        price_kind=PriceKind.ESTIMATED,
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


def _wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    """Normalize mainland-China OSM coordinates for Amap route requests."""
    if not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271):
        return latitude, longitude
    latitude_delta = _transform_latitude(longitude - 105.0, latitude - 35.0)
    longitude_delta = _transform_longitude(longitude - 105.0, latitude - 35.0)
    rad_latitude = math.radians(latitude)
    magic = math.sin(rad_latitude)
    magic = 1 - 0.00669342162296594323 * magic * magic
    sqrt_magic = math.sqrt(magic)
    latitude_delta = (
        latitude_delta * 180.0
        / ((6378245.0 * (1 - 0.00669342162296594323)) / (magic * sqrt_magic) * math.pi)
    )
    longitude_delta = (
        longitude_delta * 180.0
        / (6378245.0 / sqrt_magic * math.cos(rad_latitude) * math.pi)
    )
    return latitude + latitude_delta, longitude + longitude_delta


def _transform_latitude(x: float, y: float) -> float:
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    value += 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    return value + (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0


def _transform_longitude(x: float, y: float) -> float:
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    value += 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    return value + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0

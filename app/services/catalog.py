"""Catalog seam: normalize resources and prune single-resource violations."""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.domain.catalog import (
    CatalogSource,
    CatalogResult,
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


class CsvCatalog:
    """Load and validate a small, versioned POI catalog fully offline."""

    REQUIRED_COLUMNS = {
        "resource_id",
        "resource_type",
        "name",
        "category_tags",
        "district",
        "address",
        "latitude",
        "longitude",
        "coordinate_system",
        "duration_minutes",
        "weather_sensitive",
        "avg_price_yuan",
        "price_kind",
        "max_party_size",
        "children_allowed",
        "child_age_min",
        "child_age_max",
        "booking_required",
        "reservation_required",
        *(f"open_{day}" for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")),
        "source_name",
        "source_uri",
        "source_license",
        "collected_at",
        "last_verified_at",
        "verification_status",
        "image_url",
        "image_source_uri",
        "image_author",
        "image_license",
        "image_license_uri",
        "image_attribution",
        "notes",
    }

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(__file__).resolve().parents[2] / "data" / "catalog" / "pois.csv"
        self._delegate = InMemoryCatalog(self._load())

    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        return self._delegate.recall(constraints)

    def _load(self) -> list[StopCandidate]:
        try:
            with self._path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                missing = self.REQUIRED_COLUMNS - set(reader.fieldnames or [])
                if missing:
                    raise CatalogDataError(
                        f"{self._path}: missing columns: {', '.join(sorted(missing))}"
                    )
                rows = list(reader)
        except OSError as exc:
            raise CatalogDataError(f"cannot read catalog {self._path}: {exc}") from exc

        seen: set[str] = set()
        candidates: list[StopCandidate] = []
        for line_number, row in enumerate(rows, start=2):
            resource_id = row["resource_id"].strip()
            if resource_id in seen:
                raise CatalogDataError(
                    f"{self._path}:{line_number}: duplicate resource_id {resource_id!r}"
                )
            seen.add(resource_id)
            try:
                candidates.append(_candidate_from_csv(row))
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise CatalogDataError(
                    f"{self._path}:{line_number}: {exc}"
                ) from exc
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


_HOURS_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d$")


def _candidate_from_csv(row: dict[str, str]) -> StopCandidate:
    source_name = _required(row, "source_name")
    source_uri = _required(row, "source_uri")
    source_license = _required(row, "source_license")
    source_coordinate_system = CoordinateSystem(_required(row, "coordinate_system"))
    latitude = _float(row, "latitude")
    longitude = _float(row, "longitude")
    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if source_coordinate_system == CoordinateSystem.WGS84:
        latitude, longitude = _wgs84_to_gcj02(latitude, longitude)

    price_kind = PriceKind(_required(row, "price_kind"))
    price = _price(row.get("avg_price_yuan", ""), price_kind)
    estimated_fields = ["duration_minutes"]
    if price_kind == PriceKind.ESTIMATED:
        estimated_fields.append("avg_price")
    open_hours = {
        day: value
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        if (value := _hours(row, f"open_{day}", required=day in {"sat", "sun"}))
    }

    image = None
    if row.get("image_url", "").strip():
        image = ImageRef(
            url=_required(row, "image_url"),
            source_uri=_required(row, "image_source_uri"),
            author=_optional(row, "image_author"),
            license=_required(row, "image_license"),
            license_uri=_required(row, "image_license_uri"),
            attribution=_optional(row, "image_attribution"),
        )

    source = CatalogSource(
        source_name=source_name,
        source_uri=source_uri,
        source_license=source_license,
        coordinate_system=source_coordinate_system,
        collected_at=_required(row, "collected_at"),
        last_verified_at=_optional(row, "last_verified_at"),
        verification_status=_required(row, "verification_status"),
        estimated_fields=estimated_fields,
        derived_fields=[
            "resource_type",
            "category_tags",
            "weather_sensitive",
            "booking_required",
            "reservation_required",
        ],
    )
    return StopCandidate(
        resource_id=_required(row, "resource_id"),
        resource_type=ResourceType(_required(row, "resource_type")),
        name=_required(row, "name"),
        category_tags=[
            value.strip()
            for value in row.get("category_tags", "").split("|")
            if value.strip()
        ],
        district=row.get("district", "").strip(),
        address=row.get("address", "").strip(),
        location=GeoPoint(latitude=latitude, longitude=longitude),
        coordinate_system=CoordinateSystem.GCJ02,
        avg_price=price,
        price_kind=price_kind,
        duration_minutes=_int(row, "duration_minutes", required=True),
        open_hours=open_hours,
        max_party_size=_int(row, "max_party_size"),
        children_allowed=_bool(row, "children_allowed"),
        child_age_min=_int(row, "child_age_min"),
        child_age_max=_int(row, "child_age_max"),
        weather_sensitive=_bool(row, "weather_sensitive", required=True),
        booking_required=_bool(row, "booking_required", required=True),
        reservation_required=_bool(row, "reservation_required", required=True),
        image=image,
        source=source,
    )


def _required(row: dict[str, str], field: str) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _optional(row: dict[str, str], field: str) -> str | None:
    return row.get(field, "").strip() or None


def _int(row: dict[str, str], field: str, *, required: bool = False) -> int | None:
    raw = row.get(field, "").strip()
    if not raw:
        if required:
            raise ValueError(f"{field} is required")
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer") from exc


def _float(row: dict[str, str], field: str) -> float:
    try:
        return float(_required(row, field))
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc


def _bool(row: dict[str, str], field: str, *, required: bool = False) -> bool | None:
    raw = row.get(field, "").strip().lower()
    if not raw and not required:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(f"{field} must be true or false")


def _hours(row: dict[str, str], field: str, *, required: bool) -> str | None:
    raw = row.get(field, "").strip()
    if not raw:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not _HOURS_PATTERN.fullmatch(raw):
        raise ValueError(f"{field} must use HH:MM-HH:MM")
    if _minutes(raw.split("-", 1)[0]) >= _minutes(raw.split("-", 1)[1]):
        raise ValueError(f"{field} must close after it opens")
    return raw


def _price(raw: str, kind: PriceKind) -> int | None:
    value = raw.strip()
    if kind == PriceKind.UNKNOWN:
        if value:
            raise ValueError("avg_price_yuan must be blank when price_kind is unknown")
        return None
    if kind == PriceKind.FREE:
        if value not in {"", "0"}:
            raise ValueError("avg_price_yuan must be blank or 0 when price_kind is free")
        return 0
    if not value:
        raise ValueError(f"avg_price_yuan is required when price_kind is {kind.value}")
    try:
        price = int(value)
    except ValueError as exc:
        raise ValueError("avg_price_yuan must be an integer") from exc
    if price < 0:
        raise ValueError("avg_price_yuan must be non-negative")
    return price


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

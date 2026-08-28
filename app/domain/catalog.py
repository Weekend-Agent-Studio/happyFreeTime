"""Stable catalog facts and candidate-level pruning results."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.providers import GeoPoint


class ResourceType(str, Enum):
    ACTIVITY = "activity"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    DESSERT = "dessert"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    STALE = "stale"


class CoordinateSystem(str, Enum):
    WGS84 = "WGS84"
    GCJ02 = "GCJ02"


class PriceKind(str, Enum):
    KNOWN = "known"
    ESTIMATED = "estimated"
    FREE = "free"
    UNKNOWN = "unknown"


class ViolationCode(str, Enum):
    SINGLE_RESOURCE_PRICE_UNVERIFIED = "single_resource_price_unverified"
    SINGLE_RESOURCE_BUDGET_EXCEEDED = "single_resource_budget_exceeded"
    PARTY_TOO_LARGE = "party_too_large"
    CHILD_NOT_SUPPORTED = "child_not_supported"
    CHILD_AGE_NOT_SUPPORTED = "child_age_not_supported"
    OUTSIDE_BASIC_OPENING_HOURS = "outside_basic_opening_hours"
    OUTSIDE_RECALL_RADIUS = "outside_recall_radius"


class CatalogWarningCode(str, Enum):
    OPENING_HOURS_UNVERIFIED = "opening_hours_unverified"
    CHILD_SUITABILITY_UNVERIFIED = "child_suitability_unverified"


class CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_name: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_license: str = Field(min_length=1)
    collected_at: datetime
    last_verified_at: datetime | None = None
    verification_status: VerificationStatus


class ImageRef(BaseModel):
    """Optional remote image metadata; images never affect feasibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    source_uri: str
    author: str | None = None
    license: str = Field(min_length=1)
    license_uri: str
    attribution: str | None = None

    @field_validator("url", "source_uri", "license_uri")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        if urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("must be an HTTP(S) URL")
        return value


class StopCandidate(BaseModel):
    """One place that may enter combination after Catalog pruning."""

    model_config = ConfigDict(extra="forbid")

    resource_id: str
    resource_type: ResourceType
    name: str
    district: str = ""
    address: str = ""
    location: GeoPoint
    coordinate_system: CoordinateSystem = CoordinateSystem.GCJ02
    category_tags: list[str] = Field(default_factory=list)
    preference_tags: list[str] = Field(default_factory=list)
    diet_tags: list[str] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list)
    avg_price: int | None = Field(default=None, ge=0)
    price_kind: PriceKind = PriceKind.UNKNOWN
    duration_minutes: int = Field(gt=0)
    open_hours: dict[str, str] = Field(default_factory=dict)
    max_party_size: int | None = Field(default=None, gt=0)
    children_allowed: bool | None = None
    child_age_min: int | None = Field(default=None, ge=0)
    child_age_max: int | None = Field(default=None, ge=0)
    weather_sensitive: bool = False
    packages: list[dict] = Field(default_factory=list)
    booking_required: bool = False
    reservation_required: bool = False
    image: ImageRef | None = None
    source: CatalogSource

    def to_legacy_record(self) -> dict:
        return {
            "id": self.resource_id,
            "type": self.resource_type.value,
            "name": self.name,
            "category": self.category_tags,
            "tags": self.preference_tags,
            "diet_tags": self.diet_tags,
            "scene_tags": self.scene_tags,
            "district": self.district,
            "address": self.address,
            "lat": self.location.latitude,
            "lng": self.location.longitude,
            # V1 组合器仍要求数值；price_kind 保留“未知”语义，Presenter 不会
            # 把这个内部算术占位显示成免费。
            "avg_price": self.avg_price if self.avg_price is not None else 0,
            "price_kind": self.price_kind.value,
            "duration_minutes": self.duration_minutes,
            "open_hours": self.open_hours,
            "weather_sensitive": self.weather_sensitive,
            "packages": self.packages,
            "booking_required": self.booking_required,
            "reservation_required": self.reservation_required,
        }


class ConstraintViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    resource_name: str
    code: ViolationCode
    field: str
    message: str


class CatalogWarning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    resource_name: str
    code: CatalogWarningCode
    field: str
    message: str


class CatalogResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[StopCandidate] = Field(default_factory=list)
    violations: list[ConstraintViolation] = Field(default_factory=list)
    warnings: list[CatalogWarning] = Field(default_factory=list)

"""Stable catalog facts and candidate-level pruning results."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

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


class ViolationCode(str, Enum):
    SINGLE_RESOURCE_BUDGET_EXCEEDED = "single_resource_budget_exceeded"
    PARTY_TOO_LARGE = "party_too_large"
    CHILD_NOT_SUPPORTED = "child_not_supported"
    CHILD_AGE_NOT_SUPPORTED = "child_age_not_supported"
    OUTSIDE_BASIC_OPENING_HOURS = "outside_basic_opening_hours"
    OUTSIDE_RECALL_RADIUS = "outside_recall_radius"


class CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_name: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    collected_at: datetime
    last_verified_at: datetime | None = None
    verification_status: VerificationStatus
    dynamic_fields_mock: list[str] = Field(default_factory=list)


class StopCandidate(BaseModel):
    """One place that may enter combination after Catalog pruning."""

    model_config = ConfigDict(extra="forbid")

    resource_id: str
    resource_type: ResourceType
    name: str
    district: str = ""
    address: str = ""
    location: GeoPoint
    avg_price: int = Field(default=0, ge=0)
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
    source: CatalogSource

    def to_legacy_record(self) -> dict:
        return {
            "id": self.resource_id,
            "type": self.resource_type.value,
            "name": self.name,
            "district": self.district,
            "address": self.address,
            "lat": self.location.latitude,
            "lng": self.location.longitude,
            "avg_price": self.avg_price,
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


class CatalogResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[StopCandidate] = Field(default_factory=list)
    violations: list[ConstraintViolation] = Field(default_factory=list)

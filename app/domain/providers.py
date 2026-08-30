"""Provider 事实的稳定领域契约。

运行模式描述“如何取得事实”，来源描述“这条事实实际来自哪里”。两者分开，
这样 record 模式录到的仍可能是高德事实，而 live 失败后的结果可以明确来自缓存
或 Mock，不能把降级伪装成实时结果。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderMode(str, Enum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"
    MOCK = "mock"


class ProviderSource(str, Enum):
    AMAP_LIVE = "amap_live"
    CACHE = "cache"
    REPLAY = "replay"
    MOCK = "mock"
    LOCAL_ESTIMATE = "local_estimate"


class RouteMode(str, Enum):
    WALK = "walk"
    BIKE = "bike"
    TAXI = "taxi"
    TRANSIT = "transit"


class RouteSource(str, Enum):
    LOCAL_ESTIMATE = "local_estimate"
    REAL_PROVIDER = "real_provider"
    CACHE = "cache"
    REPLAY = "replay"


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GeocodeResolution(str, Enum):
    """A location answer must be explicit about whether it is usable."""

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class GeocodeRequest(BaseModel):
    """One explicit user location, scoped to a city when available."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    location_text: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=80)

    @property
    def cache_key(self) -> str:
        return f"{(self.city or '').strip()}|{self.location_text.strip()}"


class GeocodingFact(BaseModel):
    """Normalized geocoding fact; no provider response payload crosses this seam."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["geocoding"] = "geocoding"
    request: GeocodeRequest
    resolution: GeocodeResolution
    point: GeoPoint | None = None
    city: str | None = None
    district: str | None = None
    adcode: str | None = Field(default=None, pattern=r"^\d{6}$")
    address: str | None = None
    source: ProviderSource
    mode: ProviderMode
    observed_at: datetime
    verified_at: datetime
    verified: bool = False
    degraded: bool = False
    degraded_reason: str | None = None
    cache_age_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_resolution(self) -> "GeocodingFact":
        if self.resolution == GeocodeResolution.RESOLVED:
            if self.point is None or not self.city or not self.address:
                raise ValueError("resolved geocoding facts require point, city, and address")
        elif self.point is not None:
            raise ValueError("unresolved geocoding facts cannot contain a point")
        if self.degraded and not self.degraded_reason:
            raise ValueError("degraded geocoding facts require degraded_reason")
        if not self.degraded and self.degraded_reason is not None:
            raise ValueError("non-degraded geocoding facts cannot have degraded_reason")
        return self


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AvailabilityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(min_length=1)
    start: str = Field(pattern=r"^\d{2,}:\d{2}$")
    end: str = Field(pattern=r"^\d{2,}:\d{2}$")


class AvailabilityRequest(BaseModel):
    """Bounded batch lookup for finalist visits, never an unbounded POI crawl."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    checks: tuple[AvailabilityCheck, ...] = Field(min_length=1, max_length=8)

    @property
    def cache_key(self) -> str:
        checks = ";".join(
            f"{check.resource_id}:{check.start}-{check.end}" for check in self.checks
        )
        return f"{self.date.isoformat()}|{checks}"


class AvailabilityFact(BaseModel):
    """A dynamic availability observation for one planned stop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["availability"] = "availability"
    resource_id: str = Field(min_length=1)
    status: AvailabilityStatus
    source: ProviderSource
    mode: ProviderMode
    observed_at: datetime
    verified_at: datetime
    verified: bool = False
    expires_at: datetime | None = None
    stale: bool = False
    degraded: bool = False
    reason: str | None = None
    degraded_reason: str | None = None

    @model_validator(mode="after")
    def validate_availability_semantics(self) -> "AvailabilityFact":
        if self.status == AvailabilityStatus.UNAVAILABLE and not self.reason:
            raise ValueError("unavailable availability facts require reason")
        if self.degraded and not self.degraded_reason:
            raise ValueError("degraded availability facts require degraded_reason")
        if not self.degraded and self.degraded_reason is not None:
            raise ValueError("non-degraded availability facts cannot have degraded_reason")
        return self


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: GeoPoint
    destination: GeoPoint
    mode: RouteMode = RouteMode.TAXI
    departure_at: datetime

    @property
    def cache_key(self) -> str:
        departure_bucket = self.departure_at.replace(
            minute=(self.departure_at.minute // 15) * 15,
            second=0,
            microsecond=0,
        )
        return (
            f"{self.origin.latitude:.5f},{self.origin.longitude:.5f}|"
            f"{self.destination.latitude:.5f},{self.destination.longitude:.5f}|"
            f"{self.mode.value}|{departure_bucket.isoformat()}"
        )


class RouteFact(BaseModel):
    """finalist 时间线消费的相邻站点路线事实。"""

    model_config = ConfigDict(extra="forbid")

    origin: GeoPoint
    destination: GeoPoint
    mode: RouteMode
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(gt=0)
    geometry: list[GeoPoint] = Field(default_factory=list)
    source: ProviderSource
    provider_mode: ProviderMode
    verified_at: datetime
    degraded: bool = False
    degraded_reason: str | None = None
    cache_age_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_degradation(self) -> "RouteFact":
        if self.degraded and not self.degraded_reason:
            raise ValueError("degraded route facts require degraded_reason")
        if not self.degraded and self.degraded_reason is not None:
            raise ValueError("non-degraded route facts cannot have degraded_reason")
        return self


class WeatherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    city: str
    district: str = ""
    adcode: str = Field(pattern=r"^\d{6}$")
    date: date

    @property
    def cache_key(self) -> str:
        return f"{self.adcode}|{self.date.isoformat()}"


class WeatherFact(BaseModel):
    """Planner 可消费且 API 可公开的天气事实。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["weather"] = "weather"
    city: str
    district: str = ""
    date: date
    condition: str
    temperature_c: float | None = None
    precipitation_mm: float = Field(default=0, ge=0)
    is_adverse: bool = False
    source: ProviderSource
    mode: ProviderMode
    observed_at: datetime
    verified_at: datetime
    degraded: bool = False
    degraded_reason: str | None = None
    cache_age_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_degradation(self) -> "WeatherFact":
        if self.degraded and not self.degraded_reason:
            raise ValueError("degraded weather facts require degraded_reason")
        if not self.degraded and self.degraded_reason is not None:
            raise ValueError("non-degraded weather facts cannot have degraded_reason")
        return self

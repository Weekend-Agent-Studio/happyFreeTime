"""Planner 对外输出的数据契约。

前端和持久化层只依赖这里的结构，不依赖当前 V1 Mock Planner 的内部字典。
以后替换成 Beam Search、真实 POI 或高德路线时，只要继续产出这些模型，
API 和 UI 就不需要跟着重写。
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.catalog import CatalogSource, ConstraintViolation
from app.domain.providers import (
    GeoPoint,
    ProviderMode,
    RouteMode,
    RouteSource,
    WeatherFact,
)


class StopType(str, Enum):
    """行程停靠点类型。M1 主要使用活动和餐厅，其他类型为近期扩展保留。"""
    ACTIVITY = "activity"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    DESSERT = "dessert"


class Stop(BaseModel):
    """一个有明确起止时间和价格的行程停靠点。"""
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    type: StopType
    name: str
    start: str
    end: str
    duration_minutes: int = Field(gt=0)
    price: int = Field(default=0, ge=0)
    source: CatalogSource


class RouteLeg(BaseModel):
    """两个停靠点之间的一段路线及其降级信息。"""
    model_config = ConfigDict(extra="forbid")

    origin_name: str
    destination_name: str
    start: str
    end: str
    mode: RouteMode
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    source: RouteSource
    provider_mode: ProviderMode = ProviderMode.MOCK
    degraded: bool = True
    degraded_reason: str | None = None
    verified_at: datetime | None = None
    cache_age_seconds: int | None = Field(default=None, ge=0)
    geometry: list[GeoPoint] = Field(default_factory=list)


class Plan(BaseModel):
    """可被比较和展示的完整候选方案。"""
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    title: str
    strategy: str
    total_score: float = Field(ge=0)
    total_price: int = Field(ge=0)
    total_duration_minutes: int = Field(gt=0)
    stops: list[Stop]
    route_legs: list[RouteLeg]
    highlights: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)


class ConstraintConflict(BaseModel):
    """没有可行方案时返回的结构化冲突，而不是让模型编造一个结果。"""
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    fields: list[str] = Field(default_factory=list)
    relaxation_options: list[str] = Field(default_factory=list)


class CandidateSet(BaseModel):
    """Planner 的互斥结果：包含候选方案，或包含一个不可行冲突。"""
    model_config = ConfigDict(extra="forbid")

    plans: list[Plan] = Field(default_factory=list)
    conflict: ConstraintConflict | None = None
    provider_facts: list[WeatherFact] = Field(default_factory=list)
    catalog_violations: list[ConstraintViolation] = Field(default_factory=list)

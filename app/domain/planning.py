"""Planner 对外输出的数据契约。

前端和持久化层只依赖这里的结构，不依赖双站组合、评分或路线复核的内部实现。
以后替换成 Beam Search 或多站规划时，只要继续产出这些模型，API 和 UI 就不需要跟着重写。
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.catalog import (
    CatalogSource,
    CatalogWarning,
    ConstraintViolation,
    ImageRef,
    PriceKind,
)
from app.domain.constraints import QuestionDecision, StopRole
from app.domain.providers import (
    AvailabilityFact,
    GeocodingFact,
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


class PlanSkeleton(BaseModel):
    """A bounded ordered role pattern without concrete resources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skeleton_id: str = Field(min_length=1)
    roles: tuple[StopRole, ...] = Field(min_length=1, max_length=4)


class PlanPace(str, Enum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    FULL = "full"


class PlanStrategy(str, Enum):
    """Named deterministic optimization emphasis for a feasible plan."""

    BALANCED = "balanced"
    LOW_COST = "low_cost"
    LOW_TRAVEL = "low_travel"
    EXPERIENCE = "experience"
    FAMILY_SAFE = "family_safe"
    WEATHER_SAFE = "weather_safe"


class PlanningIntent(BaseModel):
    """Evidence-linked structural guidance; it is not feasibility proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_roles: tuple[StopRole, ...] = ()
    optional_roles: tuple[StopRole, ...] = ()
    precedence: tuple[tuple[StopRole, StopRole], ...] = ()
    minimum_stops: int = Field(default=2, ge=1, le=4)
    maximum_stops: int = Field(default=4, ge=1, le=4)
    pace: PlanPace = PlanPace.BALANCED
    evidence: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stop_range(self) -> "PlanningIntent":
        if self.minimum_stops > self.maximum_stops:
            raise ValueError("minimum_stops cannot exceed maximum_stops")
        return self


class PlanningIntentProposal(BaseModel):
    """模型对规划结构的原始提议；不承载任何外部事实或硬约束值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_roles: tuple[StopRole, ...] = ()
    optional_roles: tuple[StopRole, ...] = ()
    precedence: tuple[tuple[StopRole, StopRole], ...] = ()
    minimum_stops: int = Field(default=2, ge=1, le=4)
    maximum_stops: int = Field(default=4, ge=1, le=4)
    pace: PlanPace = PlanPace.BALANCED
    evidence: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_stop_range(self) -> "PlanningIntentProposal":
        if self.minimum_stops > self.maximum_stops:
            raise ValueError("minimum_stops cannot exceed maximum_stops")
        return self


class PlanningIntentDecision(BaseModel):
    """Validated PlanningIntent plus bounded model-runtime trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: PlanningIntent
    source: Literal["rule_based", "llm", "fallback"]
    confidence: float = Field(ge=0, le=1)
    attempts: int = Field(ge=0, le=2)
    fallback_reason: str | None = None
    prompt_version: str = Field(min_length=1)
    model_name: str | None = None


class PlanPriceStatus(str, Enum):
    KNOWN = "known"
    ESTIMATED = "estimated"
    INCOMPLETE = "incomplete"


class ScoreContribution(BaseModel):
    """One auditable rule contribution to a plan's deterministic score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    points: float
    message: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class Stop(BaseModel):
    """一个有明确起止时间和价格的行程停靠点。"""
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    type: StopType
    role: StopRole | None = None
    name: str
    start: str
    end: str
    duration_minutes: int = Field(gt=0)
    price: int = Field(default=0, ge=0)
    price_kind: PriceKind = PriceKind.UNKNOWN
    category_tags: list[str] = Field(default_factory=list)
    image: ImageRef | None = None
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
    composition_fingerprint: str
    skeleton_id: str | None = None
    title: str
    strategy: PlanStrategy
    total_score: float = Field(ge=0)
    total_price: int = Field(ge=0)
    price_status: PlanPriceStatus = PlanPriceStatus.KNOWN
    total_duration_minutes: int = Field(gt=0)
    stops: list[Stop]
    route_legs: list[RouteLeg]
    score_breakdown: list[ScoreContribution] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)


class LockedStop(BaseModel):
    """A resolved Stop identity that a modification is not allowed to change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_plan_id: str
    stop_index: int = Field(ge=0)
    resource_id: str
    role: StopRole | None = None


class StopReplacement(BaseModel):
    """One identity change at a stable itinerary position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_index: int = Field(ge=0)
    role: StopRole | None = None
    before_resource_id: str
    before_name: str
    after_resource_id: str
    after_name: str


class PlanDiff(BaseModel):
    """An evidence-bearing difference between an immutable base and new Plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_plan_version_id: str | None = None
    to_plan_version_id: str | None = None
    base_plan_id: str
    new_plan_id: str
    locked_stops: tuple[LockedStop, ...] = ()
    replacements: tuple[StopReplacement, ...] = ()
    route_distance_delta_km: float
    duration_delta_minutes: int
    price_delta: int


class ConstraintConflict(BaseModel):
    """没有可行方案时返回的结构化冲突，而不是让模型编造一个结果。"""
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    fields: list[str] = Field(default_factory=list)
    relaxation_options: list[str] = Field(default_factory=list)


class PlanWarning(BaseModel):
    """Non-blocking incompleteness scoped to a returned plan and its actual facts."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    plan_id: str | None = None
    resource_id: str | None = None
    route_leg_index: int | None = Field(default=None, ge=0)
    source: str | None = None
    degraded: bool = False
    stale: bool = False


class CandidateSet(BaseModel):
    """Planner 的互斥结果：包含候选方案，或包含一个不可行冲突。"""
    model_config = ConfigDict(extra="forbid")

    plans: list[Plan] = Field(default_factory=list)
    conflict: ConstraintConflict | None = None
    provider_facts: list[WeatherFact | GeocodingFact | AvailabilityFact] = Field(default_factory=list)
    catalog_violations: list[ConstraintViolation] = Field(default_factory=list)
    catalog_warnings: list[CatalogWarning] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)
    planning_intent_decision: PlanningIntentDecision | None = None


class PlanModificationResult(BaseModel):
    """The bounded outcome of applying one supported command to a selected Plan."""

    model_config = ConfigDict(extra="forbid")

    candidate_set: CandidateSet | None = None
    plan_diff: PlanDiff | None = None
    question: QuestionDecision | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "PlanModificationResult":
        if self.question is not None:
            if self.candidate_set is not None or self.plan_diff is not None:
                raise ValueError("a modification question cannot also contain a result")
            return self
        if self.candidate_set is None:
            raise ValueError("a modification outcome requires a candidate set or question")
        if self.candidate_set.plans and self.plan_diff is None:
            raise ValueError("a successful modification requires a plan diff")
        if not self.candidate_set.plans and self.plan_diff is not None:
            raise ValueError("a failed modification cannot contain a plan diff")
        return self

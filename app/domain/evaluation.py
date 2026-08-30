"""离线 smoke eval 的用例、期望结果和报告契约。"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import Intent, RawConstraints
from app.domain.providers import AvailabilityStatus, GeocodeResolution


class ExpectedOutcome(str, Enum):
    """M1 主链对用户可观察的三种规划结果。"""
    PLAN = "plan"
    QUESTION = "question"
    CONFLICT = "conflict"


class EvalWeatherScenario(BaseModel):
    """Deterministic weather input for an offline planning behaviour case."""
    model_config = ConfigDict(extra="forbid")

    condition: str
    is_adverse: bool


class EvalGeocodingScenario(BaseModel):
    """Replayable outcome for an explicit location in an offline smoke case."""
    model_config = ConfigDict(extra="forbid")

    resolution: GeocodeResolution
    city: str = "北京市"
    district: str = "朝阳区"
    address: str = "北京市朝阳区国贸"
    latitude: float = 39.9087
    longitude: float = 116.4615
    adcode: str = "110105"


class EvalAvailabilityScenario(BaseModel):
    """Batch availability fixtures keyed by stable catalog resource id."""
    model_config = ConfigDict(extra="forbid")

    statuses: dict[str, AvailabilityStatus] = Field(default_factory=dict)
    default_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN


class EvalRouteScenario(BaseModel):
    """Deterministic finalized route facts keyed by route destination resource id."""
    model_config = ConfigDict(extra="forbid")

    distance_by_resource_id: dict[str, float] = Field(default_factory=dict)
    default_distance_km: float = Field(default=1.0, gt=0)
    duration_minutes: int = Field(default=5, gt=0)


class ExpectedPlanFacts(BaseModel):
    """A Plan-case oracle expressed as externally observable postconditions."""
    model_config = ConfigDict(extra="forbid")

    return_to_origin: bool | None = None
    latest_return_time: str | None = None
    max_total_distance_km: float | None = Field(default=None, ge=0)
    max_route_leg_distance_km: float | None = Field(default=None, ge=0)
    route_sources: list[str] = Field(default_factory=list)
    weather_sources: list[str] = Field(default_factory=list)
    children_allowed: bool | None = None
    no_weather_sensitive_activities: bool | None = None
    opening_hours_valid: bool | None = None
    provider_fact_kinds: list[str] = Field(default_factory=list)
    required_warning_codes: list[str] = Field(default_factory=list)
    excluded_resource_ids: list[str] = Field(default_factory=list)
    required_resource_ids: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    """一条评测输入及其最小期望，不绑定内部节点实现。"""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    intent: Intent
    raw_constraints: RawConstraints
    expected_outcome: ExpectedOutcome
    expected_question_field: str | None = None
    expected_conflict_code: str | None = None
    expected_conflict_fields: list[str] = Field(default_factory=list)
    expected_plan_facts: ExpectedPlanFacts | None = None
    weather: EvalWeatherScenario | None = None
    geocoding: EvalGeocodingScenario | None = None
    availability: EvalAvailabilityScenario | None = None
    route: EvalRouteScenario | None = None
    tags: list[str] = Field(default_factory=list)


class EvalOutcome(BaseModel):
    """单条用例的实际结果与便于排错的摘要。"""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    actual_outcome: ExpectedOutcome
    details: str


class EvalReport(BaseModel):
    """一轮评测的聚合报告。"""
    model_config = ConfigDict(extra="forbid")

    total: int
    passed: int
    hard_constraint_total: int = 0
    hard_constraint_passed: int = 0
    hard_constraint_rate: float = 1.0
    outcomes: list[EvalOutcome]

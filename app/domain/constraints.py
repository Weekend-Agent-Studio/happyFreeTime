"""规划入口阶段使用的领域数据契约。

这个文件只定义“数据长什么样”，不包含业务流程。Router、Enrichment、Gate
和 Graph 都通过这些 Pydantic 模型交换数据，因而某个节点不能悄悄添加一个
下游不知道的字段。所有模型设置 ``extra="forbid"``，也是为了尽早暴露
LLM 结构化输出或节点拼装中的字段错误。
"""

from __future__ import annotations

from datetime import date as Date
from enum import Enum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.domain.catalog import ResourceType
from app.domain.providers import GeocodingFact


# Stable, non-sensitive contract codes used by the structured Router
# diagnostic path.  Keep this vocabulary finite: these values are safe to put
# in RuntimeDecision and in the one bounded repair prompt.
STRUCTURED_OUTPUT_RULE_CODES = (
    "inferred_value_missing",
    "inferred_evidence_missing",
    "inferred_confidence_missing",
    "temporal_evidence_missing",
    "temporal_confidence_missing",
    "weekday_reference_mismatch",
    "weekday_missing",
    "absolute_date_reference_mismatch",
    "absolute_date_missing",
    "explicit_time_window_order_invalid",
    "replace_target_missing",
    "target_reference_missing",
)


class Intent(str, Enum):
    """当前支持的顶层用户意图，也是 Graph 分流的主要依据。"""
    PLAN_OUTING = "plan_outing"
    FIND_ACTIVITY = "find_activity"
    CHECK_WEATHER = "check_weather"
    REFINE_PLAN = "refine_plan"
    EXECUTE_PLAN = "execute_plan"
    CANCEL_EXECUTION = "cancel_execution"
    CHITCHAT = "chitchat"
    CLARIFY = "clarify"


class IdentityType(str, Enum):
    """用户身份阶段；M1 使用 DEMO，但数据模型不假设永远只有一个用户。"""
    DEMO = "demo"
    ANONYMOUS = "anonymous"
    REGISTERED = "registered"


class ConstraintSource(str, Enum):
    """约束值的来源，用于解释、审计以及将来计算不同来源的可信度。"""
    USER_EXPLICIT = "user_explicit"
    USER_INFERRED = "user_inferred"
    SESSION_CONFIRMED = "session_confirmed"
    MEMORY = "memory"
    SYSTEM_CONTEXT = "system_context"
    REAL_TOOL = "real_tool"
    DEFAULT_RULE = "default_rule"


class StopRole(str, Enum):
    """A semantic purpose fulfilled by one concrete itinerary stop."""

    ACTIVITY = "activity"
    MEAL = "meal"
    LUNCH = "lunch"
    DINNER = "dinner"
    BREAK = "break"


class TimeScope(str, Enum):
    """有限的时间语义；具体边界仍由 Enrichment 的产品规则决定。"""

    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    ALL_DAY = "all_day"
    EXPLICIT_RANGE = "explicit_range"


class DateReference(str, Enum):
    """有限的相对/绝对日期语义；实际日期由 Enrichment 计算。"""

    TODAY = "today"
    TOMORROW = "tomorrow"
    DAY_AFTER_TOMORROW = "day_after_tomorrow"
    WEEKDAY = "weekday"
    ABSOLUTE = "absolute"


class Weekday(str, Enum):
    """星期语义；不携带具体日期或时区信息。"""

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class CommandOperation(str, Enum):
    """The bounded action requested by one interpreted conversation turn."""

    CREATE = "create"
    SELECT = "select"
    KEEP = "keep"
    REPLACE = "replace"
    UNSUPPORTED = "unsupported"


class TargetReference(BaseModel):
    """A user-language reference that has not yet been authorized or resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: StopRole | None = None
    resource_type: ResourceType | None = None
    stop_index: int | None = Field(default=None, ge=0)
    resource_id: str | None = None
    raw_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_reference_dimension(self) -> "TargetReference":
        if (
            self.role is None
            and self.resource_type is None
            and self.stop_index is None
            and self.resource_id is None
        ):
            raise PydanticCustomError(
                "target_reference_missing",
                "target reference requires a role, stop index, or resource id",
            )
        return self


class ConstraintPatch(BaseModel):
    """The small, explicit modification vocabulary supported by Resume V1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prefer_shorter_travel: bool = False


class CriterionStrength(str, Enum):
    """How strongly one replacement criterion constrains candidate selection."""

    PREFERRED = "preferred"
    REQUIRED = "required"


class SemanticCriterion(BaseModel):
    """A soft, evidence-bearing preference for the replacement resource."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["semantic"] = "semantic"
    text: str = Field(min_length=1)
    # Demo World facts cannot prove semantic absolutes; required semantic
    # criteria remain deliberately out of this first bounded slice.
    strength: Literal["preferred"] = "preferred"


class RouteObjective(BaseModel):
    """A deterministic objective whose postcondition can be verified."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["route_objective"] = "route_objective"
    metric: Literal["total_route_distance"] = "total_route_distance"
    direction: Literal["decrease"] = "decrease"
    strength: Literal["required"] = "required"


ReplacementCriterion = Annotated[
    SemanticCriterion | RouteObjective,
    Field(discriminator="kind"),
]


class ConversationCommand(BaseModel):
    """A validated proposal to operate on session state; it grants no mutation rights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: CommandOperation
    # Optional client anchors are checked by the API against the session
    # snapshot.  They are additive so legacy natural-language checkpoints can
    # still deserialize without these fields.
    base_plan_version_id: str | None = None
    base_plan_id: str | None = None
    target: TargetReference | None = None
    locked_targets: tuple[TargetReference, ...] = ()
    constraint_patch: ConstraintPatch = Field(default_factory=ConstraintPatch)
    replacement_criteria: tuple[ReplacementCriterion, ...] = ()
    evidence: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_replace_contract(self) -> "ConversationCommand":
        if self.operation == CommandOperation.REPLACE and self.target is None:
            raise PydanticCustomError(
                "replace_target_missing",
                "replace command requires a target",
            )
        return self


def effective_replacement_criteria(
    command: ConversationCommand,
) -> tuple[ReplacementCriterion, ...]:
    """Return one canonical criterion tuple while supporting old checkpoints.

    S3 stored ``ConstraintPatch.prefer_shorter_travel`` before the typed
    replacement vocabulary existed. Keeping this conversion in one place means
    callers never need to interpret both representations independently.
    """
    criteria = list(command.replacement_criteria)
    if command.constraint_patch.prefer_shorter_travel and not any(
        item.kind == "route_objective" for item in criteria
    ):
        criteria.append(RouteObjective())
    return tuple(criteria)


class ActorContext(BaseModel):
    """一次 Graph 调用的调用者上下文，不承载用户本轮的自然语言需求。"""
    model_config = ConfigDict(extra="forbid")

    user_id: str
    session_id: str
    identity_type: IdentityType
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"


class GeoLocation(BaseModel):
    """已经解析完成、可供路线服务使用的地理位置。"""
    model_config = ConfigDict(extra="forbid")

    city: str
    district: str = ""
    address: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # 行政区编码来自地理编码事实。默认出发地也可以由系统上下文给出；Planner
    # 不再维护地点文本或行政区到天气编码的私有映射。
    adcode: str | None = Field(default=None, pattern=r"^\d{6}$")


class PartyProfile(BaseModel):
    """同行人结构；儿童年龄保留为可空，以便 Gate 按活动限制决定是否追问。"""
    model_config = ConfigDict(extra="forbid")

    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    child_age: int | None = Field(default=None, ge=0, le=17)
    members: list[str] = Field(default_factory=list)


class TimeWindow(BaseModel):
    """24 小时制时间窗；统一格式减少 Planner 中的重复解析。"""
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_clock_time(cls, value: str) -> str:
        return _canonical_clock(value, field_name="time") or value



class RawConstraints(BaseModel):
    """Router 从用户原话中抽出的“原始约束”。

    ``*_text`` 字段保留原始表达，例如“今天下午”“别太远”；Router 不在这里
    猜具体日期或公里数。已经明确出现的数字可以同时写入结构化字段，例如
    ``budget_text='人均150'`` 与 ``budget_per_person=150``。
    """
    model_config = ConfigDict(extra="forbid")

    date_text: str | None = None
    # Structured temporal contract.  The legacy *_text fields remain for
    # checkpoint compatibility and as user-language evidence.
    date_reference: DateReference | None = None
    weekday: Weekday | None = None
    week_offset: Literal[0, 1] | None = None
    absolute_date: Date | None = None
    time_text: str | None = None
    time_scope: TimeScope | None = None
    explicit_time_window: TimeWindow | None = None
    departure_at_text: str | None = None
    departure_at: str | None = None
    exact_stop_count: int | None = Field(default=None, ge=1, le=4)
    required_stop_roles: tuple[StopRole, ...] = ()
    duration_minutes: int | None = Field(default=None, gt=0)
    location_text: str | None = None
    adults: int | None = Field(default=None, ge=0)
    children: int | None = Field(default=None, ge=0)
    child_age: int | None = Field(default=None, ge=0, le=17)
    members: list[str] = Field(default_factory=list)
    budget_text: str | None = None
    budget_per_person: int | None = Field(default=None, gt=0)
    strict_budget: bool = False
    # 仅在用户明确要求“确认有位/必须可预约”时置为 True。普通探索性
    # 规划允许 UNKNOWN，但必须由 Verifier 生成显式提醒。
    require_availability_confirmation: bool = False
    max_distance_text: str | None = None
    max_distance_km: float | None = Field(default=None, gt=0)
    preferences: list[str] = Field(default_factory=list)
    diet_tags: list[str] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    return_by_text: str | None = None
    return_by: str | None = None
    total_distance_text: str | None = None
    total_distance_km: float | None = Field(default=None, gt=0)

    @field_validator("departure_at")
    @classmethod
    def validate_departure_at(cls, value: str | None) -> str | None:
        return _canonical_clock(value, field_name="departure_at")

    @model_validator(mode="after")
    def validate_temporal_contract(self) -> "RawConstraints":
        reference = self.date_reference
        has_weekday_fields = self.weekday is not None or self.week_offset is not None
        if has_weekday_fields and reference != DateReference.WEEKDAY:
            raise PydanticCustomError(
                "weekday_reference_mismatch",
                "weekday fields require date_reference=weekday",
            )
        if reference == DateReference.WEEKDAY and self.weekday is None:
            raise PydanticCustomError(
                "weekday_missing",
                "date_reference=weekday requires weekday",
            )
        if self.absolute_date is not None and reference != DateReference.ABSOLUTE:
            raise PydanticCustomError(
                "absolute_date_reference_mismatch",
                "absolute_date requires date_reference=absolute",
            )
        if reference == DateReference.ABSOLUTE and self.absolute_date is None:
            raise PydanticCustomError(
                "absolute_date_missing",
                "date_reference=absolute requires absolute_date",
            )
        if reference in {
            DateReference.TODAY,
            DateReference.TOMORROW,
            DateReference.DAY_AFTER_TOMORROW,
        } and has_weekday_fields:
            raise PydanticCustomError(
                "weekday_reference_mismatch",
                "relative date reference cannot carry weekday fields",
            )
        if self.explicit_time_window is not None and (
            self.explicit_time_window.start >= self.explicit_time_window.end
        ):
            raise PydanticCustomError(
                "explicit_time_window_order_invalid",
                "explicit time window start must be before end",
            )
        return self


class Interpretation(BaseModel):
    """Router 的内部领域对象；不再直接作为实时 LLM Wire Schema。"""
    model_config = ConfigDict(extra="forbid")

    primary_intent: Intent
    intent_scores: dict[Intent, float]
    raw_constraints: RawConstraints = Field(default_factory=RawConstraints)
    selected_plan_index: int | None = Field(default=None, ge=0)
    target_reference: str | None = None
    conversation_command: ConversationCommand | None = None
    extraction_confidence: dict[str, float] = Field(default_factory=dict)
    evidence_map: dict[str, str] = Field(default_factory=dict)
    inferred_fields: set[str] = Field(default_factory=set)
    reply: str = ""
    requires_clarification: bool = False

    @model_validator(mode="after")
    def validate_inferred_fields(self) -> "Interpretation":
        """推断标记必须同时拥有实际值、证据和置信度。"""
        # A stable iteration order makes the first surfaced rule code
        # deterministic when a provider omits several related fields.
        for field in sorted(self.inferred_fields):
            if field == "party":
                raw_value_exists = any(
                    value is not None
                    for value in (
                        self.raw_constraints.adults,
                        self.raw_constraints.children,
                        self.raw_constraints.child_age,
                    )
                ) or bool(self.raw_constraints.members)
            elif hasattr(self.raw_constraints, field):
                value = getattr(self.raw_constraints, field)
                raw_value_exists = value not in (None, "", [], {})
            else:
                raw_value_exists = False
            if not raw_value_exists:
                raise PydanticCustomError(
                    "inferred_value_missing",
                    "inferred field has no extracted value",
                    {"field": field},
                )
            if not self.evidence_map.get(field):
                raise PydanticCustomError(
                    "inferred_evidence_missing",
                    "inferred field has no evidence",
                    {"field": field},
                )
            if field not in self.extraction_confidence:
                raise PydanticCustomError(
                    "inferred_confidence_missing",
                    "inferred field has no confidence",
                    {"field": field},
                )
        return self

    @model_validator(mode="after")
    def validate_temporal_evidence(self) -> "Interpretation":
        """Structured temporal values must remain traceable to user text.

        Legacy checkpoints only contain ``*_text`` fields and therefore keep
        their historical compatibility.  Once a new structured temporal
        field is present, the adapter must provide evidence.  Confidence is
        retained as optional observability data; it is not a legality gate
        because the live model wire contract no longer asks the model to
        manufacture a calibrated probability.
        """

        raw = self.raw_constraints

        def require_trace(field: str, evidence_keys: tuple[str, ...]) -> None:
            if getattr(raw, field) is None:
                return
            if not any(self.evidence_map.get(key) for key in evidence_keys):
                raise PydanticCustomError(
                    "temporal_evidence_missing",
                    "structured temporal field requires evidence",
                )

        require_trace("date_reference", ("date_reference", "date_text"))
        require_trace("weekday", ("weekday", "date_reference", "date_text"))
        require_trace("week_offset", ("week_offset", "date_reference", "date_text"))
        require_trace("absolute_date", ("absolute_date", "date_reference", "date_text"))
        require_trace("time_scope", ("time_scope", "time_text"))
        require_trace("explicit_time_window", ("explicit_time_window", "time_text"))
        return self


T = TypeVar("T")


class ConstraintValue(BaseModel, Generic[T]):
    """规范化后的约束值，并携带来源、原文、置信度和命中的规则。"""
    model_config = ConfigDict(extra="forbid")

    value: T
    source: ConstraintSource
    raw_text: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    rule_id: str | None = None


class NormalizedConstraints(BaseModel):
    """Enrichment 输出给 Gate 和 Planner 的统一规划约束。包含来自各个来源的约束"""
    model_config = ConfigDict(extra="forbid")

    date: ConstraintValue[Date] | None = None
    time_scope: ConstraintValue[TimeScope] | None = None
    time_window: ConstraintValue[TimeWindow] | None = None
    duration_minutes: ConstraintValue[int] | None = None
    location: ConstraintValue[GeoLocation] | None = None
    party: ConstraintValue[PartyProfile] | None = None
    budget_per_person: ConstraintValue[int] | None = None
    max_distance_km: ConstraintValue[float] | None = None
    preferences: list[str] = Field(default_factory=list)
    diet_tags: list[str] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    strict_budget: bool = False
    require_availability_confirmation: bool = False
    departure_at: ConstraintValue[str] | None = None
    exact_stop_count: ConstraintValue[int] | None = None
    required_stop_roles: ConstraintValue[tuple[StopRole, ...]] | None = None
    return_by: ConstraintValue[str] | None = None
    total_distance_km: ConstraintValue[float] | None = None

    @field_validator("departure_at", "return_by")
    @classmethod
    def validate_clock_constraint(cls, value: ConstraintValue[str] | None) -> ConstraintValue[str] | None:
        """Planner only receives canonical same-day clock constraints."""
        if value is None:
            return None
        return value.model_copy(
            update={"value": _canonical_clock(value.value, field_name="clock")}
        )


def _canonical_clock(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{field_name} must use HH:MM")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as error:
        raise ValueError(f"{field_name} must use HH:MM") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{field_name} must be a valid 24-hour clock value")
    return f"{hour:02d}:{minute:02d}"


class Assumption(BaseModel):
    """系统代用户采用的可见默认值；前端应展示，后续可支持用户直接修改。"""
    model_config = ConfigDict(extra="forbid")

    field: str
    value: object
    reason: str
    rule_id: str
    user_editable: bool = True


class EnrichmentResult(BaseModel):
    """规范化约束与本轮新增假设的组合结果。"""
    model_config = ConfigDict(extra="forbid")

    constraints: NormalizedConstraints
    assumptions: list[Assumption] = Field(default_factory=list)
    geocoding_fact: GeocodingFact | None = None


class QuestionDecision(BaseModel):
    """Gate 的决策结果；``need_question=True`` 会触发 Graph interrupt。"""
    model_config = ConfigDict(extra="forbid")

    need_question: bool
    field: str | None = None
    question: str = ""
    severity: str = "none"
    # Stable policy identity for evaluation and clients.  The natural-language
    # question is presentation; callers should classify the decision by this
    # code instead of matching Chinese wording.
    rule_id: str = "question.none.v1"

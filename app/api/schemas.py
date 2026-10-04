"""HTTP 层的数据契约；负责稳定前后端接口，不承载规划业务规则。"""

from datetime import date as DateValue, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.constraints import (
    ClarificationReply,
    ConstraintSource,
    ConversationCommand,
    PartyProfile,
)
from app.domain.runtime import RuntimeDecision


T = TypeVar("T")


class FieldEdit(BaseModel, Generic[T]):
    """Typed set/clear operation used by the five-column planning editor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["set", "clear"]
    value: T | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "FieldEdit":
        if self.operation == "set" and self.value is None:
            raise ValueError("set operations require a value")
        if self.operation == "clear" and self.value is not None:
            raise ValueError("clear operations must not include a value")
        return self


class WhenEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: FieldEdit[DateValue] | None = None
    start_at: FieldEdit[str] | None = None
    end_at: FieldEdit[str] | None = None

    @model_validator(mode="after")
    def require_an_edit(self) -> "WhenEdit":
        if self.date is None and self.start_at is None and self.end_at is None:
            raise ValueError("when edit must change at least one field")
        return self


class WhoEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adults: int | None = Field(default=None, ge=0)
    children: int | None = Field(default=None, ge=0)
    child_age: FieldEdit[int] | None = None
    members: FieldEdit[list[str]] | None = None

    @model_validator(mode="after")
    def require_an_edit(self) -> "WhoEdit":
        if all(value is None for value in (self.adults, self.children, self.child_age, self.members)):
            raise ValueError("who edit must change at least one field")
        return self


class BudgetEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["unlimited", "per_person"]
    amount: int | None = Field(default=None, gt=0)
    strict: bool = False

    @model_validator(mode="after")
    def validate_mode(self) -> "BudgetEdit":
        if self.mode == "per_person" and self.amount is None:
            raise ValueError("per_person budget requires an amount")
        if self.mode == "unlimited" and (self.amount is not None or self.strict):
            raise ValueError("unlimited budget cannot include amount or strict mode")
        return self


class PreferenceTagEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal["preferences", "diet_tags", "scene_tags", "avoid"]
    value: str = Field(min_length=1, max_length=120)


class PreferencesEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    add: list[PreferenceTagEdit] = Field(default_factory=list, max_length=20)
    remove: list[PreferenceTagEdit] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_changes(self) -> "PreferencesEdit":
        normalized_add = {
            (item.category, item.value.strip()) for item in self.add if item.value.strip()
        }
        normalized_remove = {
            (item.category, item.value.strip()) for item in self.remove if item.value.strip()
        }
        if not normalized_add and not normalized_remove:
            raise ValueError("preferences edit must add or remove a value")
        if normalized_add.intersection(normalized_remove):
            raise ValueError("a preference cannot be added and removed together")
        return self


class PlanningContextPatch(BaseModel):
    """Public typed editor contract; internal RequestPatch stays server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_revision: int = Field(ge=0)
    where: FieldEdit[str] | None = None
    when: WhenEdit | None = None
    who: WhoEdit | None = None
    budget: BudgetEdit | None = None
    preferences: PreferencesEdit | None = None

    @model_validator(mode="after")
    def require_an_edit(self) -> "PlanningContextPatch":
        if all(value is None for value in (self.where, self.when, self.who, self.budget, self.preferences)):
            raise ValueError("planning context patch must edit at least one section")
        return self


class PlanningContextUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64)
    patch: PlanningContextPatch


class PlanningContextField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    display_value: str = "未设置"
    source: Literal["user", "derived", "default"] = "default"
    editable: bool = True
    status: Literal["resolved", "assumed", "pending"] = "assumed"


class PlanningContextWhen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: PlanningContextField
    start_at: PlanningContextField
    end_at: PlanningContextField
    start_kind: Literal["trip_start", "departure"] = "trip_start"
    end_kind: Literal["trip_end", "return_deadline"] = "trip_end"


class PlanningContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_revision: int = Field(ge=0)
    where: PlanningContextField
    when: PlanningContextWhen
    who: PlanningContextField
    budget: PlanningContextField
    preferences: PlanningContextField
    pending_field: str | None = None
    planned_request_revision: int | None = Field(default=None, ge=0)
    has_active_plan: bool = False
    plan_stale: bool = False
    ready_for_planning: bool = False


class ResponseEnvelope(BaseModel, Generic[T]):
    """所有成功响应统一包在 data 字段中，方便前端集中处理。"""
    data: T


class MessageRequest(BaseModel):
    """用户发送的一条消息；长度限制用于尽早拒绝异常请求。"""
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64)
    content: str = Field(min_length=1, max_length=4000)
    clarification_reply: ClarificationReply | None = None
    # Structured UI actions bypass semantic interpretation but still enter the
    # same Graph authorization and planning service.  It is additive so old
    # natural-language clients remain valid.
    conversation_command: ConversationCommand | None = None
    planning_context_patch: PlanningContextPatch | None = None
    # Internal UI workflow flags. A context save persists the new PlanRequest
    # without planning; a separate action can replan that saved request.
    defer_planning: bool = False
    replan_current_request: bool = False

    @model_validator(mode="after")
    def validate_action_shape(self) -> "MessageRequest":
        actions = sum(
            value is not None
            for value in (
                self.clarification_reply,
                self.conversation_command,
                self.planning_context_patch,
            )
        )
        if actions > 1:
            raise ValueError("only one structured action may be submitted per request")
        if self.defer_planning and self.planning_context_patch is None:
            raise ValueError("defer_planning requires a planning context patch")
        if self.replan_current_request and actions:
            raise ValueError("replan_current_request cannot be combined with another action")
        return self


class CurrentRequestReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64)


class SessionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str


class SessionTitleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)


class SessionMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConstraintSummaryItem(BaseModel):
    """前端约束摘要；由规范化约束派生，不是独立事实来源。"""
    model_config = ConfigDict(extra="forbid")

    field: str
    value: Any
    source: ConstraintSource
    evidence: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    rule_id: str | None = None
    user_editable: bool = True


class AgentResponse(BaseModel):
    """一次 Agent 调用的统一响应。

    正常情况下会落入三种产品状态之一：``plans`` 有值、``question`` 有值，
    或 ``conflict`` 有值。闲聊等非规划意图则主要使用 ``reply``。
    """
    model_config = ConfigDict(extra="forbid")

    status: str
    reply: str = ""
    question: dict[str, Any] | None = None
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    constraint_summary: list[ConstraintSummaryItem] = Field(default_factory=list)
    plans: list[dict[str, Any]] = Field(default_factory=list)
    conflict: dict[str, Any] | None = None
    provider_facts: list[dict[str, Any]] = Field(default_factory=list)
    catalog_violations: list[dict[str, Any]] = Field(default_factory=list)
    catalog_warnings: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    poi_presentations: list[dict[str, Any]] = Field(default_factory=list)
    plan_version_id: str | None = None
    planning_intent_decision: dict[str, Any] | None = None
    retrieval_evidence: list[dict[str, Any]] = Field(default_factory=list)
    runtime_decisions: list[RuntimeDecision] = Field(default_factory=list)
    retrieval_mode: str | None = None
    retrieval_index_version: str | None = None
    search_mode: str | None = None
    search_beam_width: int | None = Field(default=None, ge=0)
    search_max_expansions: int | None = Field(default=None, ge=0)
    search_theoretical_combinations: int | None = Field(default=None, ge=0)
    search_expansions: int | None = Field(default=None, ge=0)
    search_finalist_count: int | None = Field(default=None, ge=0)
    search_pruned_by: dict[str, int] = Field(default_factory=dict)
    search_traces: list[dict[str, Any]] = Field(default_factory=list)
    primary_search_mode: str | None = None
    legacy_fallback_used: bool = False
    legacy_fallback_reason: str | None = None
    beam_expansions: int | None = Field(default=None, ge=0)
    beam_finalist_count: int | None = Field(default=None, ge=0)
    legacy_expansions: int | None = Field(default=None, ge=0)
    accepted_plan_spec_ids: list[str] = Field(default_factory=list)
    planning_intent_proposal_schema_version: str | None = None
    planning_intent_proposal_slots: list[dict[str, str]] = Field(default_factory=list)
    planning_intent_proposal_rejected: bool = False
    planning_intent_proposal_compiled: bool = False
    planning_intent_preferred_spec_ids: list[str] = Field(default_factory=list)
    planning_intent_fallback_spec_ids: list[str] = Field(default_factory=list)
    planning_intent_structure_fallback_attempted: bool = False
    planning_intent_structure_fallback_used: bool = False
    planning_intent_structure_fallback_reason: str | None = None
    planning_intent_structure_fallback_stage: str | None = None
    planning_intent_preferred_failure_fields: list[str] = Field(default_factory=list)
    planning_intent_fallback_failure_fields: list[str] = Field(default_factory=list)
    conversation_command: dict[str, Any] | None = None
    plan_diff: dict[str, Any] | None = None
    plan_diffs: list[dict[str, Any]] = Field(default_factory=list)
    recommendation_advice: dict[str, Any] | None = None
    planning_context: PlanningContextSummary | None = None


class PlanVersionSummary(BaseModel):
    """会话中一个 Plan Version 的最小元数据，用于按时间顺序恢复多轮方案组。"""
    model_config = ConfigDict(extra="forbid")

    plan_version_id: str
    planning_run_id: str
    supersedes_version_id: str | None = None
    created_at: datetime

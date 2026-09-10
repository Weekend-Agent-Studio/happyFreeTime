"""HTTP 层的数据契约；负责稳定前后端接口，不承载规划业务规则。"""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import ConstraintSource, ConversationCommand
from app.domain.runtime import RuntimeDecision


T = TypeVar("T")


class ResponseEnvelope(BaseModel, Generic[T]):
    """所有成功响应统一包在 data 字段中，方便前端集中处理。"""
    data: T


class MessageRequest(BaseModel):
    """用户发送的一条消息；长度限制用于尽早拒绝异常请求。"""
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64)
    content: str = Field(min_length=1, max_length=4000)
    # Structured UI actions bypass semantic interpretation but still enter the
    # same Graph authorization and planning service.  It is additive so old
    # natural-language clients remain valid.
    conversation_command: ConversationCommand | None = None


class SessionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str


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
    runtime_decisions: list[RuntimeDecision] = Field(default_factory=list)
    conversation_command: dict[str, Any] | None = None
    plan_diff: dict[str, Any] | None = None
    plan_diffs: list[dict[str, Any]] = Field(default_factory=list)


class PlanVersionSummary(BaseModel):
    """会话中一个 Plan Version 的最小元数据，用于按时间顺序恢复多轮方案组。"""
    model_config = ConfigDict(extra="forbid")

    plan_version_id: str
    planning_run_id: str
    supersedes_version_id: str | None = None
    created_at: datetime

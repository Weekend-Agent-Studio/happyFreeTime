"""HTTP 层的数据契约；负责稳定前后端接口，不承载规划业务规则。"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import ConstraintSource


T = TypeVar("T")


class ResponseEnvelope(BaseModel, Generic[T]):
    """所有成功响应统一包在 data 字段中，方便前端集中处理。"""
    data: T


class MessageRequest(BaseModel):
    """用户发送的一条消息；长度限制用于尽早拒绝异常请求。"""
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


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

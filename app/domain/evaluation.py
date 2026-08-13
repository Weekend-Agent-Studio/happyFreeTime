"""离线 smoke eval 的用例、期望结果和报告契约。"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import Intent, RawConstraints


class ExpectedOutcome(str, Enum):
    """M1 主链对用户可观察的三种规划结果。"""
    PLAN = "plan"
    QUESTION = "question"
    CONFLICT = "conflict"


class EvalCase(BaseModel):
    """一条评测输入及其最小期望，不绑定内部节点实现。"""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    intent: Intent
    raw_constraints: RawConstraints
    expected_outcome: ExpectedOutcome
    expected_question_field: str | None = None
    expected_conflict_code: str | None = None
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
    outcomes: list[EvalOutcome]

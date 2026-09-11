"""Grounded recommendation contracts for already verified plans.

The advisor is deliberately downstream of planning.  It may select and explain
among values that the Planner has already verified, but it cannot create a POI,
route fact, price, or new planning constraint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.constraints import NormalizedConstraints
from app.domain.planning import Plan, PlanDiff
from app.domain.semantics import EvidenceRef, SemanticRequest


class RecommendationAdviceRequest(BaseModel):
    """The bounded context visible to a recommendation adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    constraints: NormalizedConstraints
    semantic_request: SemanticRequest = SemanticRequest()
    verified_plans: tuple[Plan, ...] = Field(min_length=1, max_length=3)
    retrieval_evidence: tuple[EvidenceRef, ...] = ()
    plan_diffs: tuple[PlanDiff, ...] = ()

    @model_validator(mode="after")
    def validate_plan_and_evidence_identity(self) -> "RecommendationAdviceRequest":
        plan_ids = [plan.plan_id for plan in self.verified_plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("verified plan ids must be unique")
        diff_ids = [diff.new_plan_id for diff in self.plan_diffs]
        if len(diff_ids) != len(set(diff_ids)):
            raise ValueError("recommendation plan diff ids must be unique")
        if not set(diff_ids).issubset(set(plan_ids)):
            raise ValueError("recommendation plan diffs must target verified plans")
        evidence_ids = [item.evidence_id for item in self.retrieval_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("retrieval evidence ids must be unique")
        return self


class NeedSummary(BaseModel):
    """One user need, always tied to one or more user evidence records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    need_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    user_evidence_ids: tuple[str, ...] = ()


class PlanAdvice(BaseModel):
    """Grounded explanation for one verified plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    matched_need_ids: tuple[str, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    tradeoffs: tuple[str, ...] = ()


class RecommendationAdviceProposal(BaseModel):
    """Untrusted model payload; runtime metadata is added by the adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommended_plan_id: str = Field(min_length=1)
    understood_needs: tuple[NeedSummary, ...] = ()
    overall_reason: str = Field(min_length=1)
    plans: tuple[PlanAdvice, ...] = Field(min_length=1, max_length=3)


class RecommendationAdvice(BaseModel):
    """Persistable advice plus safe model-runtime provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommended_plan_id: str = Field(min_length=1)
    understood_needs: tuple[NeedSummary, ...] = ()
    overall_reason: str = Field(min_length=1)
    plans: tuple[PlanAdvice, ...] = Field(min_length=1, max_length=3)
    adapter: Literal["rule_based", "llm", "fallback"]
    fallback_reason: str | None = None
    prompt_version: str = Field(min_length=1)
    model_name: str | None = None
    model_invoked: bool = False
    attempts: int = Field(ge=0, le=2)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

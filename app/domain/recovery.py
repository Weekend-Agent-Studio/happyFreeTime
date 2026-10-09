"""Typed, user-safe recovery facts and actions for planning turns."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.constraints import ConstraintSource, RequestPatch


class RecoveryKind(StrEnum):
    HARD_CONFLICT = "hard_conflict"
    NO_FEASIBLE_PLAN = "no_feasible_plan"
    MODIFICATION_FAILED = "modification_failed"


class RecoveryStage(StrEnum):
    CONSTRAINT_COMPILATION = "constraint_compilation"
    PLAN_STRUCTURE = "plan_structure"
    RETRIEVAL = "retrieval"
    SCHEDULING = "scheduling"
    ROUTE = "route"
    AVAILABILITY = "availability"
    VERIFICATION = "verification"
    MODIFICATION = "modification"


RecoveryField: TypeAlias = Literal[
    "date",
    "time_window",
    "departure_at",
    "return_by",
    "budget_per_person",
    "strict_budget",
    "max_distance_km",
    "total_distance_km",
    "location",
    "planning_area",
    "exact_stop_count",
    "required_stop_roles",
    "availability",
    "target_reference",
    "replacement_criteria",
]


class RecoveryDiagnostics(BaseModel):
    """Small numeric and provenance summary; never carries raw tool/model data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_max_distance_km: float | None = Field(default=None, gt=0)
    nearest_candidate_distance_km: float | None = Field(default=None, ge=0)
    requested_stop_count: int | None = Field(default=None, ge=1, le=4)
    required_role_count: int | None = Field(default=None, ge=0, le=4)
    available_minutes: int | None = Field(default=None, ge=0)
    optimistic_duration_minutes: int | None = Field(default=None, ge=0)
    rejected_by_time_window: int = Field(default=0, ge=0)
    rejected_by_route: int = Field(default=0, ge=0)
    rejected_by_availability: int = Field(default=0, ge=0)
    constraint_sources: dict[str, ConstraintSource] = Field(default_factory=dict)

    @field_validator("constraint_sources")
    @classmethod
    def validate_constraint_sources(
        cls,
        value: dict[str, ConstraintSource],
    ) -> dict[str, ConstraintSource]:
        allowed = {
            "date",
            "time_window",
            "departure_at",
            "return_by",
            "budget_per_person",
            "strict_budget",
            "max_distance_km",
            "total_distance_km",
            "location",
            "planning_area",
            "exact_stop_count",
            "required_stop_roles",
        }
        if not set(value).issubset(allowed):
            raise ValueError("constraint source keys must be known planning fields")
        return value


class RecoveryReason(BaseModel):
    """A safe projection of the actual conflict/failure that blocks progress."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=80)
    kind: RecoveryKind
    stage: RecoveryStage
    fields: tuple[RecoveryField, ...] = ()
    request_revision: int = Field(ge=0)
    plan_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    public_summary: str = Field(min_length=1, max_length=240)
    diagnostics: RecoveryDiagnostics = Field(default_factory=RecoveryDiagnostics)

    @field_validator("fields")
    @classmethod
    def validate_unique_fields(cls, value: tuple[RecoveryField, ...]) -> tuple[RecoveryField, ...]:
        if len(value) != len(set(value)):
            raise ValueError("recovery fields must be unique")
        return value


RecoveryContinuation: TypeAlias = Literal[
    "compile_patch", "plan", "modify", "clarify_field", "finish"
]


class _RecoveryAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=240)
    request_revision: int = Field(ge=0)
    plan_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    continuation: RecoveryContinuation


class ApplyRequestPatchAction(_RecoveryAction):
    kind: Literal["apply_request_patch"] = "apply_request_patch"
    patch: RequestPatch
    auto_eligible: bool = False


class RequestFieldAction(_RecoveryAction):
    kind: Literal["request_field"] = "request_field"
    field: RecoveryField
    input_type: Literal["text", "number", "clock", "choice"]


class ReplanCurrentRequestAction(_RecoveryAction):
    kind: Literal["replan_current_request"] = "replan_current_request"


class CancelTurnAction(_RecoveryAction):
    kind: Literal["cancel_turn"] = "cancel_turn"


class StartNewRequestAction(_RecoveryAction):
    kind: Literal["start_new_request"] = "start_new_request"


class KeepCurrentPlanAction(_RecoveryAction):
    kind: Literal["keep_current_plan"] = "keep_current_plan"


RecoveryAction: TypeAlias = Annotated[
    ApplyRequestPatchAction
    | RequestFieldAction
    | ReplanCurrentRequestAction
    | CancelTurnAction
    | StartNewRequestAction
    | KeepCurrentPlanAction,
    Field(discriminator="kind"),
]


class RecoveryDecision(BaseModel):
    """Deterministic policy output; application and orchestration remain callers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: RecoveryReason
    actions: tuple[RecoveryAction, ...] = ()
    auto_action: ApplyRequestPatchAction | None = None
    stale: bool = False


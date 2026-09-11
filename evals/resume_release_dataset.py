"""Versioned task labels for the Resume Release evaluation slice.

The file is intentionally a data contract, not an evaluator.  Draft labels
must receive a manual review before they can be used as resume-grade ground
truth or quoted as product-quality metrics.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["message", "select_plan", "replace_stop"]
    user_input: str | None = None
    selection_strategy: Literal["recommended", "first", "second"] | None = None
    target_stop_index: int | None = Field(default=None, ge=0, le=3)
    preset: Literal["similar", "shorter_travel"] | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "EvaluationStep":
        if self.action == "message" and not self.user_input:
            raise ValueError("message step requires user_input")
        if self.action == "select_plan" and self.selection_strategy is None:
            raise ValueError("select_plan step requires selection_strategy")
        if self.action == "replace_stop":
            if self.target_stop_index is None:
                raise ValueError("replace_stop step requires target_stop_index")
            if not self.user_input and self.preset is None:
                raise ValueError("replace_stop step requires user_input or preset")
        return self


class EvaluationExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["plan", "question", "conflict"]
    expected_stop_count: int | None = Field(default=None, ge=1, le=4)
    required_roles: tuple[Literal["activity", "meal", "lunch", "dinner", "break"], ...] = ()
    expected_constraint_values: dict[str, Any] = Field(default_factory=dict)
    expected_objectives: tuple[
        Literal[
            "low_fatigue",
            "shorter_travel",
            "fewer_stops",
            "novelty",
            "quiet",
            "conversation_friendly",
            "romantic",
            "family_friendly",
            "low_spice",
        ],
        ...,
    ] = ()
    expected_question_field: str | None = None
    expected_conflict_code: str | None = None
    expected_conflict_fields: tuple[str, ...] = ()
    require_grounded_advice: bool = False
    advice_must_address: tuple[str, ...] = ()
    require_plan_diff: bool = False
    preserve_non_target_stops: bool = False
    max_replacement_candidates: int | None = Field(default=None, ge=1, le=2)

    @model_validator(mode="after")
    def validate_outcome_fields(self) -> "EvaluationExpectation":
        if self.outcome == "question" and self.expected_question_field is None:
            raise ValueError("question outcome requires expected_question_field")
        if self.outcome == "conflict" and self.expected_conflict_code is None:
            raise ValueError("conflict outcome requires expected_conflict_code")
        if self.outcome != "plan" and (
            self.require_grounded_advice or self.require_plan_diff
        ):
            raise ValueError("only a plan outcome can require advice or plan diff")
        return self


class EvaluationFixtures(BaseModel):
    """Named replay conditions required to reproduce provider-dependent cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weather: Literal["default", "rainy"] = "default"
    availability: Literal["default", "all_unavailable"] = "default"


class ResumeReleaseCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    split: Literal["holdout"]
    label_status: Literal["draft", "reviewed"]
    category: Literal["planning", "clarification", "conflict", "modification"]
    steps: tuple[EvaluationStep, ...] = Field(min_length=1)
    expected: EvaluationExpectation
    fixtures: EvaluationFixtures = Field(default_factory=EvaluationFixtures)
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_category_contract(self) -> "ResumeReleaseCase":
        if self.category == "modification":
            actions = [step.action for step in self.steps]
            if "select_plan" not in actions or "replace_stop" not in actions:
                raise ValueError("modification requires selection and replacement")
            if actions.index("select_plan") > actions.index("replace_stop"):
                raise ValueError("selection must happen before replacement")
            if not (
                self.expected.require_plan_diff
                and self.expected.preserve_non_target_stops
                and self.expected.max_replacement_candidates == 2
            ):
                raise ValueError("modification invariants are incomplete")
        return self


class ResumeReleaseDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["resume-release-eval.v1"]
    frozen_system_commit: str = Field(min_length=7)
    evaluation_clock: datetime
    default_location: str = Field(min_length=1)
    labeling_note: str = Field(min_length=1)
    cases: tuple[ResumeReleaseCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> "ResumeReleaseDataset":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


def load_resume_release_dataset(path: Path) -> ResumeReleaseDataset:
    return ResumeReleaseDataset.model_validate_json(path.read_text(encoding="utf-8"))


def dump_case_summary(dataset: ResumeReleaseDataset) -> dict[str, int]:
    """Small helper for the later runner/report without scoring any output."""

    summary: dict[str, int] = {}
    for case in dataset.cases:
        summary[case.category] = summary.get(case.category, 0) + 1
    summary["reviewed"] = sum(case.label_status == "reviewed" for case in dataset.cases)
    summary["draft"] = sum(case.label_status == "draft" for case in dataset.cases)
    return summary

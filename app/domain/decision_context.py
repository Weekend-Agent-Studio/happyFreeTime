"""Bounded context projection used by the semantic turn interpreter.

The planner's durable state is intentionally richer than the context that a
Router needs for one turn. These models form the small, model-facing seam:
they retain enough information for references such as ``第二站`` or
``还是便宜点`` without exposing provider payloads, database identifiers, or
the full conversation transcript.
"""

from __future__ import annotations

from datetime import date as Date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import (
    PendingModification,
    PlanRequest,
    QuestionDecision,
    UserActKind,
)
from app.domain.planning import Plan


class RequestContextSummary(BaseModel):
    """Safe summary of the current request, without raw provider facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=0)
    date: Date | None = None
    start_at: str | None = None
    end_at: str | None = None
    start_kind: Literal["trip_start", "departure"] = "trip_start"
    end_kind: Literal["trip_end", "return_deadline"] = "trip_end"
    location_display: str | None = None
    adults: int | None = Field(default=None, ge=0)
    children: int | None = Field(default=None, ge=0)
    companion_labels: tuple[str, ...] = ()
    budget_per_person: int | None = Field(default=None, ge=0)
    strict_budget: bool = False
    preferences: tuple[str, ...] = ()
    diet_tags: tuple[str, ...] = ()
    scene_tags: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()

    @classmethod
    def from_request(cls, request: PlanRequest) -> "RequestContextSummary":
        window = request.planning_window
        location = request.location.value if request.location is not None else None
        party = request.party.value if request.party is not None else None
        return cls(
            revision=request.revision,
            date=window.date.value if window.date is not None else None,
            start_at=window.start_at.value if window.start_at is not None else None,
            end_at=window.end_at.value if window.end_at is not None else None,
            start_kind=window.start_kind,
            end_kind=window.end_kind,
            location_display=(location.address if location is not None else None),
            adults=party.adults if party is not None else None,
            children=party.children if party is not None else None,
            companion_labels=tuple(party.members) if party is not None else (),
            budget_per_person=(
                request.budget_per_person.value
                if request.budget_per_person is not None
                else None
            ),
            strict_budget=request.strict_budget,
            preferences=tuple(request.preferences),
            diet_tags=tuple(request.diet_tags),
            scene_tags=tuple(request.scene_tags),
            avoid=tuple(request.avoid),
        )


class StopContextSummary(BaseModel):
    """A referenceable stop without resource or plan identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_index: int = Field(ge=0)
    role: str | None = None
    name: str


class SelectedPlanContextSummary(BaseModel):
    """The selected plan projection needed for natural-language references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Kept for deterministic application-side reconciliation. ``prompt_lines``
    # omits it, so database identifiers never cross into the model prompt.
    plan_version_id: str | None = None
    stops: tuple[StopContextSummary, ...] = ()

    @classmethod
    def from_plan(
        cls,
        plan: Plan,
        *,
        plan_version_id: str | None = None,
    ) -> "SelectedPlanContextSummary":
        return cls(
            plan_version_id=plan_version_id,
            stops=tuple(
                StopContextSummary(
                    stop_index=index,
                    role=stop.role.value if stop.role is not None else None,
                    name=stop.name,
                )
                for index, stop in enumerate(plan.stops)
            ),
        )


class PendingInteractionContext(BaseModel):
    """The currently resumable interaction, if one exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["clarification", "modification"]
    field: str | None = None
    issue_kind: str | None = None
    request_revision: int | None = Field(default=None, ge=0)
    allow_free_text: bool = True
    options: tuple[str, ...] = ()

    @classmethod
    def from_decision(
        cls,
        decision: QuestionDecision,
        *,
        pending_modification: PendingModification | None = None,
    ) -> "PendingInteractionContext":
        kind = "modification" if pending_modification is not None else "clarification"
        return cls(
            kind=kind,
            field=decision.field,
            issue_kind=decision.issue_kind,
            request_revision=decision.request_revision,
            allow_free_text=decision.allow_free_text,
            options=tuple(option.label for option in decision.options),
        )


class RequestLifecycle(StrEnum):
    """Whether the session has a usable request and/or generated plan."""

    EMPTY = "empty"
    DRAFT = "draft"
    PLANNED = "planned"


class DecisionContext(BaseModel):
    """Bounded semantic context for one turn.

    This is a projection, not a second source of truth. It may contain an
    opaque plan-version anchor for application reconciliation, but its prompt
    representation deliberately redacts that anchor and all resource IDs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_request: RequestContextSummary | None = None
    selected_plan: SelectedPlanContextSummary | None = None
    pending_interaction: PendingInteractionContext | None = None
    request_lifecycle: RequestLifecycle = RequestLifecycle.EMPTY
    last_user_act: UserActKind | None = None
    last_system_outcome: str | None = None
    allowed_actions: tuple[str, ...] = ()

    def prompt_lines(self) -> tuple[str, ...]:
        """Render bounded, non-identifier context for the Router prompt."""
        lines: list[str] = [
            f"当前请求生命周期：{self.request_lifecycle.value}"
        ]
        request = self.current_request
        if request is not None:
            lines.append("当前规划请求摘要：")
            lines.append(f"- 请求版本：{request.revision}")
            if request.date is not None:
                lines.append(f"- 日期：{request.date.isoformat()}")
            if request.start_at is not None or request.end_at is not None:
                lines.append(
                    "- 时间："
                    f"{request.start_at or '未设置'}–{request.end_at or '未设置'}"
                    f"（{request.start_kind}/{request.end_kind}）"
                )
            if request.location_display:
                lines.append(f"- 出发位置：{request.location_display}")
            if request.adults is not None or request.children is not None:
                lines.append(
                    "- 同行人："
                    f"{request.adults if request.adults is not None else '未设置'} 位成人，"
                    f"{request.children if request.children is not None else '未设置'} 位儿童"
                )
            if request.companion_labels:
                lines.append(f"- 同行人描述：{'、'.join(request.companion_labels)}")
            if request.budget_per_person is not None:
                strict = "（严格）" if request.strict_budget else ""
                lines.append(f"- 人均预算：{request.budget_per_person}{strict}")
            elif request.strict_budget:
                lines.append("- 预算模式：严格控制，但金额未设置")
            for label, values in (
                ("偏好", request.preferences),
                ("饮食", request.diet_tags),
                ("场景", request.scene_tags),
                ("避开", request.avoid),
            ):
                if values:
                    lines.append(f"- {label}：{'、'.join(values)}")

        if self.selected_plan is not None:
            lines.append("当前选中方案的可引用站点：")
            for stop in self.selected_plan.stops:
                role = f"（{stop.role}）" if stop.role else ""
                lines.append(f"- 第{stop.stop_index + 1}站：{stop.name}{role}")

        pending = self.pending_interaction
        if pending is not None:
            lines.append(
                "当前存在待处理交互："
                f"类型={pending.kind}，字段={pending.field or '未指定'}，"
                f"问题类型={pending.issue_kind or '未指定'}"
            )
            if pending.options:
                lines.append(f"- 可选回答：{'、'.join(pending.options)}")

        if self.last_user_act is not None:
            lines.append(f"上一轮用户动作：{self.last_user_act}")
        if self.last_system_outcome:
            lines.append(f"上一轮系统结果：{self.last_system_outcome}")
        if self.allowed_actions:
            lines.append(f"当前允许考虑的动作：{'、'.join(self.allowed_actions)}")
        return tuple(lines)


class DecisionContextBuilder:
    """Pure builder from the current Graph/session projection.

    Repository access stays outside this module. Callers load durable state
    and checkpoint values first, then pass the projection here.
    """

    @staticmethod
    def build(
        *,
        request: PlanRequest | None = None,
        selected_plan: Plan | None = None,
        active_plan_version_id: str | None = None,
        pending_issue: QuestionDecision | None = None,
        pending_modification: PendingModification | None = None,
        has_plans: bool = False,
        previous_user_act: UserActKind | None = None,
        last_system_outcome: str | None = None,
    ) -> DecisionContext:
        lifecycle = _request_lifecycle(
            request=request,
            selected_plan=selected_plan,
            active_plan_version_id=active_plan_version_id,
            has_plans=has_plans,
        )
        allowed_actions_by_lifecycle = {
            RequestLifecycle.EMPTY: (
                "create_plan",
                "check_weather",
                "chitchat",
            ),
            RequestLifecycle.DRAFT: (
                "create_plan",
                "patch_constraints",
                "check_weather",
                "chitchat",
            ),
            RequestLifecycle.PLANNED: (
                "create_plan",
                "patch_constraints",
                "replace_stop",
                "query_plan",
                "check_weather",
                "chitchat",
            ),
        }[lifecycle]
        allowed_actions = list(allowed_actions_by_lifecycle)
        if lifecycle is RequestLifecycle.PLANNED and selected_plan is None:
            # A session may contain candidate plans without a selected
            # PlanVersion.  Replacement remains a valid *kind* for a planned
            # session, but cannot execute until the UI supplies a selection.
            allowed_actions.remove("replace_stop")
        return DecisionContext(
            current_request=(
                RequestContextSummary.from_request(request)
                if request is not None and lifecycle is not RequestLifecycle.EMPTY
                else None
            ),
            selected_plan=(
                SelectedPlanContextSummary.from_plan(
                    selected_plan,
                    plan_version_id=active_plan_version_id,
                )
                if selected_plan is not None
                else None
            ),
            pending_interaction=(
                PendingInteractionContext.from_decision(
                    pending_issue,
                    pending_modification=pending_modification,
                )
                if pending_issue is not None
                else None
            ),
            request_lifecycle=lifecycle,
            last_user_act=previous_user_act,
            last_system_outcome=last_system_outcome,
            allowed_actions=tuple(allowed_actions),
        )


def _request_lifecycle(
    *,
    request: PlanRequest | None,
    selected_plan: Plan | None,
    active_plan_version_id: str | None,
    has_plans: bool,
) -> RequestLifecycle:
    if has_plans or selected_plan is not None or active_plan_version_id is not None:
        return RequestLifecycle.PLANNED
    if request is not None and _request_has_values(request):
        return RequestLifecycle.DRAFT
    return RequestLifecycle.EMPTY


def _request_has_values(request: PlanRequest) -> bool:
    """Treat ``PlanRequest()`` as construction state, not user state."""

    window = request.planning_window
    return any(
        (
            window.date is not None,
            window.start_at is not None,
            window.end_at is not None,
            window.start_kind != "trip_start",
            window.end_kind != "trip_end",
            request.duration_minutes is not None,
            request.location is not None,
            request.planning_area is not None,
            request.party is not None,
            request.budget_per_person is not None,
            request.max_distance_km is not None,
            bool(request.preferences),
            bool(request.diet_tags),
            bool(request.scene_tags),
            bool(request.avoid),
            request.strict_budget,
            request.require_availability_confirmation,
            request.exact_stop_count is not None,
            request.required_stop_roles is not None,
            request.total_distance_km is not None,
        )
    )

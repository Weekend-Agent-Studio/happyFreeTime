"""Bounded context projection used by the semantic turn interpreter.

The planner's durable state is intentionally richer than the context that a
Router needs for one turn. These models form the small, model-facing seam:
they retain enough information for references such as ``第二站`` or
``还是便宜点`` without exposing provider payloads, database identifiers, or
the full conversation transcript.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import Intent, PendingModification, PlanRequest, QuestionDecision
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
    last_user_act: Intent | None = None
    last_system_outcome: str | None = None
    allowed_actions: tuple[str, ...] = ()

    def prompt_lines(self) -> tuple[str, ...]:
        """Render bounded, non-identifier context for the Router prompt."""
        lines: list[str] = []
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
            lines.append(f"上一轮用户动作：{self.last_user_act.value}")
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
        previous_intent: Intent | None = None,
        last_system_outcome: str | None = None,
    ) -> DecisionContext:
        allowed_actions = [
            "create_plan",
            "check_weather",
            "query_plan",
            "chitchat",
        ]
        if request is not None or has_plans:
            allowed_actions.append("patch_constraints")
        if has_plans:
            allowed_actions.append("select_plan")
        if selected_plan is not None:
            allowed_actions.append("replace_stop")
        return DecisionContext(
            current_request=(
                RequestContextSummary.from_request(request)
                if request is not None
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
            last_user_act=previous_intent,
            last_system_outcome=last_system_outcome,
            allowed_actions=tuple(allowed_actions),
        )

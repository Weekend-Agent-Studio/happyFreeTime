"""Deterministic mapping from structured failures to bounded recovery choices."""

from __future__ import annotations

import math

from app.domain.constraints import (
    ConstraintSource,
    PlanRequest,
    RequestPatch,
)
from app.domain.recovery import (
    ApplyRequestPatchAction,
    CancelTurnAction,
    KeepCurrentPlanAction,
    OpenConstraintEditorAction,
    RecoveryDecision,
    RecoveryField,
    RecoveryKind,
    RecoveryReason,
    RecoveryStage,
    ReplanCurrentRequestAction,
    RequestFieldAction,
    StartNewRequestAction,
)


_MAX_RECOVERY_DISTANCE_KM = 30.0
_DISTANCE_FAILURE_CODES = frozenset(
    {
        "NO_CANDIDATES_AFTER_HARD_FILTER",
        "NO_CANDIDATES_WITHIN_SEARCH_RADIUS",
        "NO_PLAN_AFTER_ROUTE_VERIFICATION",
        "NO_PLAN_WITHIN_DISTANCE",
        "NO_VALID_REPLACEMENT",
        "NO_REPLACEMENT_PLAN",
    }
)
_FIELD_INPUTS: dict[RecoveryField, tuple[str, str]] = {
    "date": ("日期", "text"),
    "time_window": ("可用时间", "text"),
    "departure_at": ("出发时间", "clock"),
    "return_by": ("最晚到家时间", "clock"),
    "budget_per_person": ("人均预算", "number"),
    "max_distance_km": ("搜索范围", "number"),
    "total_distance_km": ("全程距离上限", "number"),
    "location": ("出发地点", "text"),
    "planning_area": ("活动区域", "text"),
    "exact_stop_count": ("站点数量", "number"),
    "availability": ("营业确认方式", "choice"),
}


class RecoveryPolicy:
    """Produce typed choices from failure facts without I/O or model calls."""

    def decide(
        self,
        reason: RecoveryReason,
        request: PlanRequest,
        *,
        current_plan_version_id: str | None = None,
    ) -> RecoveryDecision:
        if request.revision != reason.request_revision or (
            reason.plan_version_id is not None
            and reason.plan_version_id != current_plan_version_id
        ):
            return RecoveryDecision(reason=reason, stale=True)

        plan_version_id = reason.plan_version_id or current_plan_version_id
        actions = []
        auto_action = None

        if (
            reason.code in _DISTANCE_FAILURE_CODES
            and "max_distance_km" in reason.fields
            and request.max_distance_km is not None
        ):
            target = self._next_distance(
                request.max_distance_km.value,
                reason.diagnostics.nearest_candidate_distance_km,
            )
            if target is not None:
                actions.append(
                    self._distance_patch_action(
                        reason,
                        request,
                        target,
                        source=ConstraintSource.SESSION_CONFIRMED,
                        action_id=f"expand-distance-r{request.revision}-{target:g}",
                        auto_eligible=False,
                        plan_version_id=plan_version_id,
                    )
                )
                if request.max_distance_km.source in {
                    ConstraintSource.DEFAULT_RULE,
                    ConstraintSource.DERIVED,
                    ConstraintSource.SYSTEM_CONTEXT,
                } and reason.diagnostics.constraint_sources.get(
                    "max_distance_km", request.max_distance_km.source
                ) == request.max_distance_km.source:
                    auto_action = self._distance_patch_action(
                        reason,
                        request,
                        target,
                        source=ConstraintSource.DERIVED,
                        action_id=f"auto-expand-distance-r{request.revision}-{target:g}",
                        auto_eligible=True,
                        plan_version_id=plan_version_id,
                    )

        if (
            reason.code == "NO_PLAN_WITHIN_STRICT_BUDGET"
            and request.strict_budget
        ):
            actions.append(
                ApplyRequestPatchAction(
                    action_id=f"use-budget-as-preference-r{request.revision}",
                    kind="apply_request_patch",
                    label="把预算改为参考条件并重新规划",
                    description="方案可能超过当前预算，生成后请核对价格。",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation=(
                        "modify"
                        if reason.kind == RecoveryKind.MODIFICATION_FAILED
                        else "plan"
                    ),
                    patch=RequestPatch(
                        base_revision=request.revision,
                        set_fields={"strict_budget": False},
                        source=ConstraintSource.SESSION_CONFIRMED,
                    ),
                )
            )

        if (
            reason.kind == RecoveryKind.NO_FEASIBLE_PLAN
            and reason.stage in {RecoveryStage.PLAN_STRUCTURE, RecoveryStage.SCHEDULING}
            and "exact_stop_count" in reason.fields
            and request.exact_stop_count is not None
        ):
            current_count = request.exact_stop_count.value
            required_count = len(
                request.required_stop_roles.value
                if request.required_stop_roles is not None
                else ()
            )
            if current_count > max(1, required_count):
                actions.append(
                    ApplyRequestPatchAction(
                        action_id=f"reduce-stops-r{request.revision}-{current_count - 1}",
                        kind="apply_request_patch",
                        label=f"改为 {current_count - 1} 站并重新规划",
                        description="会按剩余条件重新生成并验证整套行程。",
                        request_revision=request.revision,
                        plan_version_id=plan_version_id,
                        continuation="plan",
                        patch=RequestPatch(
                            base_revision=request.revision,
                            set_fields={"exact_stop_count": current_count - 1},
                            source=ConstraintSource.SESSION_CONFIRMED,
                            evidence={"exact_stop_count": "用户确认调整站点数量"},
                        ),
                    )
                )

        for field in reason.fields:
            metadata = _FIELD_INPUTS.get(field)
            if metadata is None:
                continue
            label, input_type = metadata
            actions.append(
                RequestFieldAction(
                    action_id=f"edit-{field}-r{request.revision}",
                    kind="request_field",
                    label=f"调整{label}",
                    description="修改后会重新检查约束并规划。",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation=(
                        "modify"
                        if reason.kind == RecoveryKind.MODIFICATION_FAILED
                        else "plan"
                    ),
                    field=field,
                    input_type=input_type,
                    choices=(
                        ("必须确认", "不需要确认")
                        if field == "availability"
                        else ()
                    ),
                )
            )

        if reason.code in {"PROVIDER_UNAVAILABLE", "TEMPORARY_PROVIDER_FAILURE"}:
            actions.append(
                ReplanCurrentRequestAction(
                    action_id=f"retry-plan-r{request.revision}",
                    kind="replan_current_request",
                    label="重试规划",
                    description="使用当前条件重新请求规划。",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation="plan",
                )
            )

        if reason.kind in {
            RecoveryKind.HARD_CONFLICT,
            RecoveryKind.NO_FEASIBLE_PLAN,
            RecoveryKind.MODIFICATION_FAILED,
        }:
            actions.append(
                OpenConstraintEditorAction(
                    action_id=f"edit-constraints-r{request.revision}",
                    kind="open_constraint_editor",
                    label="手动调整条件",
                    description="打开条件编辑；系统不会替你修改任何约束。",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation="finish",
                )
            )

        if plan_version_id is not None:
            actions.append(
                KeepCurrentPlanAction(
                    action_id=f"keep-plan-v{plan_version_id}",
                    kind="keep_current_plan",
                    label="保留当前方案",
                    description="本次失败不会覆盖已生成的方案。",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation="finish",
                )
            )
        actions.extend(
            (
                CancelTurnAction(
                    action_id=f"cancel-r{request.revision}",
                    kind="cancel_turn",
                    label="取消本轮",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation="finish",
                ),
                StartNewRequestAction(
                    action_id=f"new-request-r{request.revision}",
                    kind="start_new_request",
                    label="开始新需求",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation="finish",
                ),
            )
        )
        return RecoveryDecision(
            reason=reason,
            actions=tuple(actions),
            auto_action=auto_action,
        )

    @staticmethod
    def _next_distance(current: float, nearest: float | None) -> float | None:
        if current >= _MAX_RECOVERY_DISTANCE_KM:
            return None
        if nearest is not None and nearest > current:
            proposed = float(math.ceil(nearest))
        else:
            proposed = max(current + 2.0, current * 1.5)
        proposed = min(_MAX_RECOVERY_DISTANCE_KM, proposed)
        if proposed <= current:
            return None
        return round(proposed, 1)

    @staticmethod
    def _distance_patch_action(
        reason: RecoveryReason,
        request: PlanRequest,
        target: float,
        *,
        source: ConstraintSource,
        action_id: str,
        auto_eligible: bool,
        plan_version_id: str | None,
    ) -> ApplyRequestPatchAction:
        return ApplyRequestPatchAction(
            action_id=action_id,
            kind="apply_request_patch",
            label=f"尝试扩大到 {target:g} 公里并重新规划",
            description="这是一次扩大搜索范围的尝试，不保证一定能找到方案。",
            request_revision=request.revision,
            plan_version_id=plan_version_id,
            continuation=(
                "modify"
                if reason.kind == RecoveryKind.MODIFICATION_FAILED
                else "plan"
            ),
            auto_eligible=auto_eligible,
            patch=RequestPatch(
                base_revision=request.revision,
                set_fields={"max_distance_km": target},
                source=source,
                field_sources={"max_distance_km": source},
                evidence={
                    "max_distance_km": (
                        "系统进行一次有界恢复"
                        if auto_eligible
                        else f"用户确认尝试扩大范围；原阻塞条件：{reason.code}"
                    )
                },
            ),
        )

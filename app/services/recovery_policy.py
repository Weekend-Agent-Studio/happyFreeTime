"""Deterministic mapping from structured failures to bounded recovery choices."""

from __future__ import annotations

import math

from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
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
    RecoveryContext,
    RecoveryReason,
    RecoveryStage,
    PrimaryBottleneckKind,
    ReplanCurrentRequestAction,
    RequestFieldAction,
    StartNewRequestAction,
    RecoveryAction,
)
from app.services.recovery_bottleneck import resolve_primary_bottleneck


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
    "required_stop_roles": ("站点要求", "text"),
}


class RecoveryPolicy:
    """Produce typed choices from failure facts without I/O or model calls."""

    def decide(
        self,
        reason: RecoveryReason,
        request: PlanRequest,
        *,
        current_plan_version_id: str | None = None,
        context: RecoveryContext | None = None,
    ) -> RecoveryDecision:
        if request.revision != reason.request_revision or (
            reason.plan_version_id is not None
            and reason.plan_version_id != current_plan_version_id
        ):
            return RecoveryDecision(reason=reason, stale=True)

        plan_version_id = reason.plan_version_id or current_plan_version_id
        primary_bottleneck = (
            resolve_primary_bottleneck(
                reason,
                reason.diagnostics,
                context.previous_attempts,
            )
            if context is not None
            else reason.primary_bottleneck
            or resolve_primary_bottleneck(reason, reason.diagnostics)
        )
        if reason.primary_bottleneck != primary_bottleneck:
            reason = reason.model_copy(update={"primary_bottleneck": primary_bottleneck})
        context = context or RecoveryContext(
            reason=reason,
            primary_bottleneck=primary_bottleneck,
            request=request,
            current_plan_version_id=plan_version_id,
        )
        actions = []
        auto_action = None

        schedule_bottleneck = (
            primary_bottleneck.kind == PrimaryBottleneckKind.SCHEDULE_CAPACITY
        )

        if (
            primary_bottleneck.kind
            in {
                PrimaryBottleneckKind.DISTANCE_LIMIT,
                PrimaryBottleneckKind.ROUTE_FEASIBILITY,
            }
            and reason.code in _DISTANCE_FAILURE_CODES
            and "max_distance_km" in primary_bottleneck.affected_fields
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

        if primary_bottleneck.kind == PrimaryBottleneckKind.SEARCH_BUDGET:
            actions.append(
                ReplanCurrentRequestAction(
                    action_id=f"retry-search-budget-r{request.revision}",
                    kind="replan_current_request",
                    label="继续搜索剩余候选并重新规划",
                    description="当前条件不变；系统将重新执行一次有界搜索，不会自动放宽你的约束。",
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation="plan",
                )
            )

        if primary_bottleneck.kind == PrimaryBottleneckKind.AVAILABILITY:
            availability_codes = set(reason.diagnostics.availability_failure_codes)
            if (
                availability_codes
                == {"availability_unknown_requires_explicit_acceptance"}
                and request.require_availability_confirmation
            ):
                actions.append(
                    ApplyRequestPatchAction(
                        action_id=f"accept-unconfirmed-availability-r{request.revision}",
                        kind="apply_request_patch",
                        label="允许使用未确认可用状态的地点并重新规划",
                        description="系统无法确认实际有位；生成后请自行向地点核实。",
                        request_revision=request.revision,
                        plan_version_id=plan_version_id,
                        continuation=(
                            "modify"
                            if reason.kind == RecoveryKind.MODIFICATION_FAILED
                            else "plan"
                        ),
                        patch=RequestPatch(
                            base_revision=request.revision,
                            set_fields={"require_availability_confirmation": False},
                            source=ConstraintSource.SESSION_CONFIRMED,
                            evidence={
                                "require_availability_confirmation": (
                                    "用户明确接受动态可用性尚未确认"
                                )
                            },
                        ),
                    )
                )
            elif (
                availability_codes
                & {
                    "availability_provider_degraded",
                    "availability_fact_missing",
                }
                or (
                    request.require_availability_confirmation
                    and "availability_unconfirmed" in availability_codes
                )
            ) and not availability_codes & {
                "visit_outside_opening_hours",
                "availability_verified_unavailable",
            }:
                actions.append(
                    ReplanCurrentRequestAction(
                        action_id=f"retry-availability-r{request.revision}",
                        kind="replan_current_request",
                        label="重新核查营业状态并规划",
                        description="本轮缺少足以判断的动态营业状态证据；系统将按原条件重新核查。",
                        request_revision=request.revision,
                        plan_version_id=plan_version_id,
                        continuation=(
                            "modify"
                            if reason.kind == RecoveryKind.MODIFICATION_FAILED
                            else "plan"
                        ),
                    )
                )

        if (
            reason.kind == RecoveryKind.NO_FEASIBLE_PLAN
            and (
                primary_bottleneck.kind == PrimaryBottleneckKind.SCHEDULE_CAPACITY
                or primary_bottleneck.kind == PrimaryBottleneckKind.CANDIDATE_SUPPLY
                or reason.stage == RecoveryStage.PLAN_STRUCTURE
                or schedule_bottleneck
            )
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

        if schedule_bottleneck:
            end_at = request.planning_window.end_at
            target_end = self._extended_end_time(
                request,
                reason.diagnostics.optimistic_duration_minutes,
            )
            if end_at is not None and target_end is not None:
                end_value = ConstraintValue[str](
                    value=target_end,
                    source=ConstraintSource.SESSION_CONFIRMED,
                    raw_text=f"延长到 {target_end}",
                    rule_id="recovery.schedule.extend_end.v1",
                )
                actions.insert(
                    0,
                    ApplyRequestPatchAction(
                        action_id=f"extend-end-r{request.revision}-{target_end.replace(':', '')}",
                        kind="apply_request_patch",
                        label=f"延长到 {target_end} 并重新规划",
                        description="当前主要瓶颈是可用时间不足；这是一次用户确认的时间调整。",
                        request_revision=request.revision,
                        plan_version_id=plan_version_id,
                        continuation=(
                            "modify"
                            if reason.kind == RecoveryKind.MODIFICATION_FAILED
                            else "plan"
                        ),
                        patch=RequestPatch(
                            base_revision=request.revision,
                            set_fields={"planning_window.end_at": end_value},
                            source=ConstraintSource.SESSION_CONFIRMED,
                            field_sources={"planning_window.end_at": ConstraintSource.SESSION_CONFIRMED},
                            evidence={
                                "planning_window.end_at": (
                                    f"排程失败；可用时间 {reason.diagnostics.available_minutes or '未知'} 分钟"
                                )
                            },
                        ),
                    ),
                )

        for field in self._editable_fields(primary_bottleneck, reason, request):
            metadata = _FIELD_INPUTS.get(field)
            if metadata is None:
                continue
            label, input_type = metadata
            description = (
                "诊断没有定位到具体缺少的类型；请填写你愿意保留的站点类型，用逗号或顿号分隔。支持活动、餐食、午餐、晚餐、休息；输入“不限制”可清除此要求。"
                if field == "required_stop_roles"
                else "修改后会重新检查约束并规划。"
            )
            actions.append(
                RequestFieldAction(
                    action_id=f"edit-{field}-r{request.revision}",
                    kind="request_field",
                    label=f"调整{label}",
                    description=description,
                    request_revision=request.revision,
                    plan_version_id=plan_version_id,
                    continuation=(
                        "modify"
                        if reason.kind == RecoveryKind.MODIFICATION_FAILED
                        else "plan"
                    ),
                    field=field,
                    input_type=input_type,
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
        actions = self._filter_history(
            actions,
            context.previous_attempts,
            context.used_concrete_fingerprints,
        )
        actions = self._bounded_actions(actions)
        if auto_action is not None:
            auto_action = auto_action.model_copy(
                update=self._fingerprint_update(auto_action)
            )
        return RecoveryDecision(
            reason=reason,
            actions=tuple(actions),
            auto_action=auto_action,
        )

    @staticmethod
    def _bounded_actions(actions: list[RecoveryAction]) -> list[RecoveryAction]:
        """Keep suggestions small while preserving the fixed safety exits.

        The policy may discover several fields from one failure, but showing
        every editable field at once makes the card look like a menu of
        guesses.  At most three evidence-backed suggestions are exposed; the
        manual editor/keep/cancel/new-request controls remain available as a
        separate safety area in the client.
        """

        fixed_types = (
            OpenConstraintEditorAction,
            KeepCurrentPlanAction,
            CancelTurnAction,
            StartNewRequestAction,
        )
        suggestions: list[RecoveryAction] = []
        fixed: list[RecoveryAction] = []
        seen: set[str] = set()
        for action in actions:
            if action.action_id in seen:
                continue
            seen.add(action.action_id)
            (fixed if isinstance(action, fixed_types) else suggestions).append(action)

        def priority(action: RecoveryAction) -> tuple[int, str]:
            if isinstance(action, ApplyRequestPatchAction):
                return (0, action.action_id)
            if isinstance(action, ReplanCurrentRequestAction):
                return (1, action.action_id)
            if isinstance(action, RequestFieldAction):
                return (2, action.action_id)
            return (9, action.action_id)

        suggestions.sort(key=priority)
        bounded = [*suggestions[:3], *fixed]
        return [
            action.model_copy(update=RecoveryPolicy._fingerprint_update(action))
            for action in bounded
        ]

    @staticmethod
    def _editable_fields(
        bottleneck: object,
        reason: RecoveryReason,
        request: PlanRequest,
    ) -> tuple[str, ...]:
        """Return only fields supported by the selected bottleneck.

        The failure's raw ``fields`` are evidence, not a menu definition.  A
        bounded mapping keeps one noisy finding from producing unrelated
        buttons while still preserving a safe generic editor for unknown cases.
        """

        kind = getattr(bottleneck, "kind", PrimaryBottleneckKind.UNKNOWN)
        by_kind: dict[PrimaryBottleneckKind, tuple[str, ...]] = {
            PrimaryBottleneckKind.SCHEDULE_CAPACITY: (
                "departure_at",
                "return_by",
                "time_window",
                "exact_stop_count",
            ),
            PrimaryBottleneckKind.DISTANCE_LIMIT: ("max_distance_km", "planning_area"),
            PrimaryBottleneckKind.ROUTE_FEASIBILITY: (
                "max_distance_km",
                "planning_area",
                "exact_stop_count",
            ),
            PrimaryBottleneckKind.TEMPORAL_CONFLICT: (
                "departure_at",
                "return_by",
                "time_window",
            ),
            PrimaryBottleneckKind.CANDIDATE_SUPPLY: (
                "planning_area",
                "max_distance_km",
                "exact_stop_count",
                "required_stop_roles",
            ),
            PrimaryBottleneckKind.AVAILABILITY: ("date", "time_window"),
            PrimaryBottleneckKind.SEARCH_BUDGET: (),
            # Unknown evidence is not enough to choose a typed field.  Keep
            # the explicit editor below as the safe escape hatch instead of
            # turning arbitrary diagnostic fields into a button menu.
            PrimaryBottleneckKind.UNKNOWN: (),
        }
        fields = by_kind.get(kind, ())
        if (
            kind == PrimaryBottleneckKind.CANDIDATE_SUPPLY
            and request.required_stop_roles is not None
        ):
            # Aggregate candidate diagnostics do not identify which individual
            # role is missing. Let the user edit the complete role requirement
            # explicitly instead of deleting roles or preferences by guess.
            fields = ("required_stop_roles",)
        elif (
            kind == PrimaryBottleneckKind.CANDIDATE_SUPPLY
            and reason.diagnostics.nearest_candidate_distance_km is None
        ):
            # Without a nearest candidate outside the radius, another radius
            # increase is not supported by the observed candidate funnel.
            fields = tuple(field for field in fields if field != "max_distance_km")
        if (
            kind == PrimaryBottleneckKind.DISTANCE_LIMIT
            and "total_distance_km" in getattr(bottleneck, "affected_fields", ())
        ):
            fields = ("total_distance_km",)
        availability_codes = set(reason.diagnostics.availability_failure_codes)
        availability_data_only = not availability_codes & {
            "visit_outside_opening_hours",
            "availability_verified_unavailable",
        }
        if (
            kind == PrimaryBottleneckKind.AVAILABILITY
            and availability_data_only
            and (
                availability_codes
                & {"availability_provider_degraded", "availability_fact_missing"}
                or (
                    request.require_availability_confirmation
                    and "availability_unconfirmed" in availability_codes
                )
            )
        ):
            # Missing, degraded, or unverified dynamic facts are not repaired
            # by changing the user's date/time. Offer retry instead, unless
            # the same run also has a concrete closed/unavailable finding.
            fields = ()
        if kind == PrimaryBottleneckKind.TEMPORAL_CONFLICT:
            # A temporal contradiction is a field-edit problem, not a
            # capacity problem.  Restrict the editor choices to the exact
            # bounds named by the resolver so a contradictory 09:00/08:00
            # request can never surface a misleading "reduce stops" action.
            affected = set(getattr(bottleneck, "affected_fields", ()))
            fields = tuple(field for field in fields if field in affected)
        if kind == PrimaryBottleneckKind.UNKNOWN:
            # A few explicit ConstraintEngine codes have a stable target even
            # without a planner funnel.  This is a code allowlist, not a
            # pass-through of arbitrary ``reason.fields``.
            fields = {
                "NO_PLAN_WITHIN_STRICT_BUDGET": ("budget_per_person",),
            }.get(reason.code, ())
        available = set(reason.fields) | {
            field
            for field in fields
            if getattr(request, field, None) is not None
            or field in {"time_window", "departure_at", "return_by", "availability"}
        }
        return tuple(field for field in fields if field in available)

    @staticmethod
    def _filter_history(
        actions: list[RecoveryAction],
        attempts: tuple[object, ...],
        used_concrete_fingerprints: tuple[str, ...] = (),
    ) -> list[RecoveryAction]:
        """Filter semantic duplicates before the bounded suggestion budget."""

        concrete_used = {
            getattr(attempt, "concrete_fingerprint", "")
            for attempt in attempts
            if getattr(attempt, "concrete_fingerprint", "")
        }
        concrete_used.update(used_concrete_fingerprints)
        strategy_failures: dict[str, int] = {}
        for attempt in attempts:
            strategy = getattr(attempt, "strategy_fingerprint", None)
            if not strategy:
                continue
            if getattr(attempt, "improved", False):
                strategy_failures[strategy] = 0
            else:
                strategy_failures[strategy] = strategy_failures.get(strategy, 0) + 1
        ineffective_strategies = {
            strategy
            for strategy, failures in strategy_failures.items()
            if failures >= 2
        }
        safety_kinds = {
            "open_constraint_editor",
            "keep_current_plan",
            "cancel_turn",
            "start_new_request",
        }
        filtered: list[RecoveryAction] = []
        for action in actions:
            concrete = RecoveryPolicy._fingerprint(action)
            strategy = RecoveryPolicy._strategy_fingerprint(action)
            # Safety exits remain available after a previous choice; they are
            # not recovery attempts and must not disappear from the only
            # escape path for a still-failing plan.
            if action.kind not in safety_kinds and concrete in concrete_used:
                continue
            # Keep the strategy available as a non-primary alternative only
            # when it has not repeatedly failed; a safety action is never in
            # this set because it has no recovery strategy fingerprint.
            if strategy and strategy in ineffective_strategies:
                continue
            filtered.append(action)
        return filtered

    @staticmethod
    def _fingerprint(action: RecoveryAction) -> str:
        if isinstance(action, ApplyRequestPatchAction):
            fields = ",".join(sorted(action.patch.set_fields))
            values = ",".join(
                f"{key}={action.patch.set_fields[key]}"
                for key in sorted(action.patch.set_fields)
            )
            fingerprint = f"apply_request_patch:{fields}:{values}"
            if action.patch.clear_fields:
                fingerprint += ":clear=" + ",".join(sorted(action.patch.clear_fields))
            if action.patch.add_to_fields:
                fingerprint += ":add=" + ";".join(
                    f"{key}={','.join(action.patch.add_to_fields[key])}"
                    for key in sorted(action.patch.add_to_fields)
                )
            if action.patch.remove_from_fields:
                fingerprint += ":remove=" + ";".join(
                    f"{key}={','.join(action.patch.remove_from_fields[key])}"
                    for key in sorted(action.patch.remove_from_fields)
                )
            return fingerprint
        if isinstance(action, RequestFieldAction):
            return f"request_field:{action.field}"
        return action.kind

    @staticmethod
    def _strategy_fingerprint(action: RecoveryAction) -> str | None:
        if isinstance(action, ApplyRequestPatchAction):
            fields = set(action.patch.set_fields) | set(action.patch.clear_fields)
            if "max_distance_km" in fields:
                return "expand:max_distance"
            if "preferences" in fields:
                return "relax:preference"
            if "required_stop_roles" in fields:
                return "relax:role_requirements"
            if any(field.startswith("planning_window.") for field in fields):
                return "extend:planning_window"
            if "exact_stop_count" in fields:
                return "reduce:stop_count"
            if "planning_area" in fields:
                return "adjust:planning_area"
            if "budget_per_person" in fields or "strict_budget" in fields:
                return "adjust:budget"
            if fields:
                return "adjust:constraints"
        if isinstance(action, RequestFieldAction):
            return f"edit:{action.field}"
        if isinstance(action, ReplanCurrentRequestAction):
            if action.action_id.startswith("retry-search-budget-"):
                return "retry:search_budget"
            if action.action_id.startswith("retry-availability-"):
                return "retry:availability"
            return "retry:plan"
        return None

    @classmethod
    def _fingerprint_update(cls, action: RecoveryAction) -> dict[str, str | None]:
        return {
            "action_fingerprint": cls._fingerprint(action),
            "strategy_fingerprint": cls._strategy_fingerprint(action),
        }

    @staticmethod
    def _extended_end_time(
        request: PlanRequest,
        optimistic_duration_minutes: int | None,
    ) -> str | None:
        end_at = request.planning_window.end_at
        if end_at is None:
            return None
        # A numeric extension is only safe when the funnel supplied both a
        # lower-bound duration and a concrete start.  Otherwise offer the
        # typed time-field editor instead of inventing a two-hour value.
        if optimistic_duration_minutes is None or request.planning_window.start_at is None:
            return None
        hour, minute = (int(part) for part in end_at.value.split(":"))
        current = hour * 60 + minute
        start_at = request.planning_window.start_at
        start_hour, start_minute = (int(part) for part in start_at.value.split(":"))
        target = start_hour * 60 + start_minute + optimistic_duration_minutes
        target = max(target, current + 60)
        target = min(target, 23 * 60 + 59)
        if target <= current:
            return None
        return f"{target // 60:02d}:{target % 60:02d}"

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

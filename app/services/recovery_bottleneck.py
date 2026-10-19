"""Deterministic classification of the dominant recovery bottleneck.

This module is deliberately small: it only reads the already collected failure
funnel and returns a bounded semantic category.  It never reruns planning or
invents a new diagnostic source.
"""

from __future__ import annotations

from app.domain.recovery import (
    PrimaryBottleneck,
    PrimaryBottleneckKind,
    RecoveryAttempt,
    RecoveryDiagnostics,
    RecoveryReason,
    RecoveryStage,
)


def resolve_primary_bottleneck(
    reason: RecoveryReason,
    diagnostics: RecoveryDiagnostics | None = None,
    previous_attempts: tuple[RecoveryAttempt, ...] = (),
) -> PrimaryBottleneck:
    """Choose one dominant funnel category from existing safe evidence."""

    facts = diagnostics or reason.diagnostics
    fields = tuple(reason.fields)
    # Conflict codes are intentionally preserved on RecoveryReason, but the
    # nested evidence list is a lowercase safe identifier vocabulary.
    codes = [reason.code.lower()]

    # ConstraintEngine can reject an impossible clock relationship before the
    # planner produces a funnel trace.  Preserve that temporal meaning rather
    # than classifying it as ``unknown`` merely because all counters are zero.
    temporal_conflicts = {
        "DEPARTURE_NOT_BEFORE_RETURN_BY": ("departure_at", "return_by"),
        "DEPARTURE_OUTSIDE_TIME_WINDOW": ("departure_at", "time_window"),
        "INVALID_TIME_WINDOW_ORDER": ("time_window",),
    }
    if reason.code in temporal_conflicts:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.TEMPORAL_CONFLICT,
            evidence_codes=tuple(dict.fromkeys((*codes, "temporal_conflict"))),
            affected_fields=temporal_conflicts[reason.code],
            evidence_summary=reason.public_summary,
        )

    # An explicit downstream stage is stronger than mixed aggregate counters:
    # for example, an availability verifier may also report time-window
    # rejections from the candidates it inspected.
    if reason.stage == RecoveryStage.AVAILABILITY:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.AVAILABILITY,
            evidence_codes=tuple(
                dict.fromkeys((*codes, "availability", *facts.availability_failure_codes))
            ),
            affected_fields=("date", "time_window", "availability"),
            evidence_summary=_availability_summary(facts),
        )

    # Total itinerary distance is different from the search radius. When the
    # verifier names total_distance_km, edit that exact user constraint rather
    # than widening max_distance_km.
    if (
        "total_distance_km" in fields
        and (
            reason.stage == RecoveryStage.ROUTE
            or reason.code == "NO_PLAN_WITHIN_DISTANCE"
        )
    ):
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.DISTANCE_LIMIT,
            evidence_codes=tuple(dict.fromkeys((*codes, "total_distance_limit"))),
            affected_fields=("total_distance_km",),
            evidence_summary="路线复核未满足用户设定的全程距离上限",
        )

    # A retrieval failure with no role candidates and no nearest-distance or
    # route evidence cannot justify another radius increase. It falls through
    # to the candidate-supply classification below, not the radius heuristic.
    empty_unlocated_candidate_pool = (
        reason.stage == RecoveryStage.RETRIEVAL
        and facts.role_pool_entries == 0
        and facts.retrieved_candidate_count in (None, 0)
        and facts.nearest_candidate_distance_km is None
        and (
            facts.rejected_by_distance == 0
            or _two_failed_radius_expansions(previous_attempts)
        )
        and facts.route_candidates == 0
        and facts.route_verification_failures == 0
        and facts.rejected_by_route == 0
    )

    if reason.stage == RecoveryStage.ROUTE:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.ROUTE_FEASIBILITY,
            evidence_codes=tuple(dict.fromkeys((*codes, "route_feasibility"))),
            affected_fields=("max_distance_km", "planning_area", "exact_stop_count"),
            evidence_summary=(
                f"路线候选 {facts.route_candidates} 个，复核失败 "
                f"{facts.route_verification_failures or facts.rejected_by_route} 个"
            ),
        )

    # Exhausting the bounded search budget is a distinct recoverable state
    # when the funnel has not already shown a concrete scheduling failure.
    # Do this before the broad ``SCHEDULING`` stage check below; an expansion
    # trace may be labelled scheduling even though no time-window candidate
    # was rejected.
    if (
        facts.search_budget_exhausted
        and facts.local_schedule_failures == 0
        and facts.rejected_by_time_window == 0
    ):
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.SEARCH_BUDGET,
            evidence_codes=tuple(dict.fromkeys((*codes, "search_budget"))),
            affected_fields=(),
            evidence_summary="搜索预算已耗尽，尚未完成全部候选组合复核",
        )

    # A failed local schedule is stronger evidence than a distance hint: it is
    # the direct reason no candidate plan survived the temporal verifier.
    if (
        reason.stage == RecoveryStage.SCHEDULING
        or facts.local_schedule_failures > 0
        and facts.local_schedule_passes == 0
        or facts.rejected_by_time_window > 0
        and facts.local_schedule_passes == 0
    ):
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.SCHEDULE_CAPACITY,
            evidence_codes=tuple(dict.fromkeys((*codes, "schedule_capacity"))),
            affected_fields=tuple(
                field
                for field in ("planning_window", "exact_stop_count", "duration_minutes")
                if field in fields or field == "planning_window"
            ),
            evidence_summary=_schedule_summary(facts),
        )

    if facts.rejected_by_availability > 0:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.AVAILABILITY,
            evidence_codes=tuple(
                dict.fromkeys((*codes, "availability", *facts.availability_failure_codes))
            ),
            affected_fields=("date", "time_window", "availability"),
            evidence_summary=_availability_summary(facts),
        )

    if facts.route_verification_failures > 0 or facts.rejected_by_route > 0:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.ROUTE_FEASIBILITY,
            evidence_codes=tuple(dict.fromkeys((*codes, "route_feasibility"))),
            affected_fields=("max_distance_km", "planning_area", "exact_stop_count"),
            evidence_summary=(
                f"路线候选 {facts.route_candidates} 个，复核失败 "
                f"{facts.route_verification_failures or facts.rejected_by_route} 个"
            ),
        )

    # A radius-specific retrieval failure is still a distance blocker, not a
    # generic candidate-supply issue.  Preserve that distinction before the
    # broader candidate code check below.
    if not empty_unlocated_candidate_pool and (
        reason.code
        in {
            "NO_CANDIDATES_WITHIN_SEARCH_RADIUS",
            "NO_PLAN_WITHIN_DISTANCE",
        }
        or "max_distance_km" in fields
    ):
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.DISTANCE_LIMIT,
            evidence_codes=tuple(dict.fromkeys((*codes, "distance_limit"))),
            affected_fields=("max_distance_km", "planning_area"),
            evidence_summary=_distance_summary(facts),
        )

    if facts.search_budget_exhausted:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.SEARCH_BUDGET,
            evidence_codes=tuple(dict.fromkeys((*codes, "search_budget"))),
            affected_fields=(),
            evidence_summary="搜索预算已耗尽，尚未完成全部候选组合复核",
        )

    if (
        reason.stage == RecoveryStage.RETRIEVAL
        and (
            facts.retrieved_candidate_count == 0
            or facts.role_pool_entries == 0
            or facts.rejected_by_hard_filter > 0
        )
    ) or any(
        marker in reason.code
        for marker in ("CANDIDATE", "NO_CANDIDATES", "HARD_FILTER")
    ):
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.CANDIDATE_SUPPLY,
            evidence_codes=tuple(dict.fromkeys((*codes, "candidate_supply"))),
            affected_fields=(
                "planning_area",
                "required_stop_roles",
                "preferences",
                "exact_stop_count",
            ),
            evidence_summary=(
                f"召回候选 {facts.retrieved_candidate_count or 0} 个，"
                f"角色池 {facts.role_pool_entries} 个"
                + (
                    "；没有距离或路线证据支持继续扩距"
                    if empty_unlocated_candidate_pool
                    else ""
                )
            ),
        )

    return PrimaryBottleneck(
        kind=PrimaryBottleneckKind.UNKNOWN,
        evidence_codes=tuple(dict.fromkeys((*codes, "unknown"))),
        affected_fields=fields,
        evidence_summary="当前失败证据不足以安全归因到单一恢复方向",
    )


def _two_failed_radius_expansions(
    attempts: tuple[RecoveryAttempt, ...],
) -> bool:
    """Treat a still-empty role pool as supply failure after two failed expansions."""

    consecutive_failures = 0
    for attempt in reversed(attempts):
        if (
            attempt.strategy_fingerprint != "expand:max_distance"
            or "max_distance_km" not in attempt.changed_fields
            or attempt.outcome != "still_failed"
            or attempt.plan_generated
        ):
            break
        consecutive_failures += 1
        if consecutive_failures >= 2:
            return True
    return False


def _schedule_summary(facts: RecoveryDiagnostics) -> str:
    if facts.available_minutes is not None and facts.optimistic_duration_minutes is not None:
        return (
            f"预计至少需要 {facts.optimistic_duration_minutes} 分钟，"
            f"当前可用 {facts.available_minutes} 分钟"
        )
    if facts.local_schedule_failures:
        return f"本地排程失败 {facts.local_schedule_failures} 次，未留下可行前缀"
    return "当前时间窗口内未找到可执行排程"


def _distance_summary(facts: RecoveryDiagnostics) -> str:
    if facts.current_max_distance_km is not None and facts.nearest_candidate_distance_km is not None:
        return (
            f"当前范围 {facts.current_max_distance_km:g} km，"
            f"最近候选约 {facts.nearest_candidate_distance_km:g} km"
        )
    return "候选或路线距离超出当前搜索范围"


def _availability_summary(facts: RecoveryDiagnostics) -> str:
    codes = set(facts.availability_failure_codes)
    if "visit_outside_opening_hours" in codes:
        return "候选地点的营业时间与计划到访时段不匹配"
    if "availability_verified_unavailable" in codes:
        return f"动态事实确认 {facts.rejected_by_availability} 个候选不可用"
    if "availability_provider_degraded" in codes:
        return "营业状态服务返回降级或过期结果，需要重新核查"
    if "availability_fact_missing" in codes:
        return "部分候选缺少营业状态核验结果，需要重新核查"
    if "availability_unknown_requires_explicit_acceptance" in codes:
        return "动态可用性结果为未知，需用户决定是否接受未确认状态"
    if "availability_unconfirmed" in codes or "availability_confirmation_required" in codes:
        return "当前动态可用性信息不足以确认地点有位"
    return f"营业状态复核淘汰 {facts.rejected_by_availability} 个候选"

"""Deterministic classification of the dominant recovery bottleneck.

This module is deliberately small: it only reads the already collected failure
funnel and returns a bounded semantic category.  It never reruns planning or
invents a new diagnostic source.
"""

from __future__ import annotations

from app.domain.recovery import (
    PrimaryBottleneck,
    PrimaryBottleneckKind,
    RecoveryDiagnostics,
    RecoveryReason,
    RecoveryStage,
)


def resolve_primary_bottleneck(
    reason: RecoveryReason,
    diagnostics: RecoveryDiagnostics | None = None,
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
            kind=PrimaryBottleneckKind.SCHEDULE_CAPACITY,
            evidence_codes=tuple(dict.fromkeys((*codes, "schedule_capacity"))),
            affected_fields=temporal_conflicts[reason.code],
            evidence_summary=reason.public_summary,
        )

    # An explicit downstream stage is stronger than mixed aggregate counters:
    # for example, an availability verifier may also report time-window
    # rejections from the candidates it inspected.
    if reason.stage == RecoveryStage.AVAILABILITY:
        return PrimaryBottleneck(
            kind=PrimaryBottleneckKind.AVAILABILITY,
            evidence_codes=tuple(dict.fromkeys((*codes, "availability"))),
            affected_fields=("date", "time_window", "availability"),
            evidence_summary=(
                f"营业状态复核淘汰 {facts.rejected_by_availability} 个候选"
            ),
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
            evidence_codes=tuple(dict.fromkeys((*codes, "availability"))),
            affected_fields=("date", "time_window", "availability"),
            evidence_summary=(
                f"营业状态复核淘汰 {facts.rejected_by_availability} 个候选"
            ),
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
    if reason.code in {
        "NO_CANDIDATES_WITHIN_SEARCH_RADIUS",
        "NO_PLAN_WITHIN_DISTANCE",
    } or "max_distance_km" in fields:
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
            affected_fields=("planning_area", "required_stop_roles", "preferences"),
            evidence_summary=(
                f"召回候选 {facts.retrieved_candidate_count or 0} 个，"
                f"角色池 {facts.role_pool_entries} 个"
            ),
        )

    return PrimaryBottleneck(
        kind=PrimaryBottleneckKind.UNKNOWN,
        evidence_codes=tuple(dict.fromkeys((*codes, "unknown"))),
        affected_fields=fields,
        evidence_summary="当前失败证据不足以安全归因到单一恢复方向",
    )


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

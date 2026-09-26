"""Deterministic user-facing summaries for already verified planning results."""

from __future__ import annotations

from app.domain.constraints import NormalizedConstraints
from app.domain.planning import CandidateSet, Plan


def present_candidate_set(
    candidate_set: CandidateSet,
    constraints: NormalizedConstraints,
) -> str:
    """Explain only facts already carried by a verified candidate set.

    This template is intentionally not a second planner: it never adds POIs, changes
    a score, or converts a warning into proof. It remains available when the LLM
    router is disabled.
    """
    if candidate_set.conflict is not None:
        return candidate_set.conflict.message
    if not candidate_set.plans:
        return "暂时没有可展示的可行方案。"

    plan_summaries = "；".join(
        _plan_summary(index, plan)
        for index, plan in enumerate(candidate_set.plans, start=1)
    )
    constraint_summaries: list[str] = []
    if constraints.return_by is not None:
        constraint_summaries.append(
            f"均已包含返程，并在 {constraints.return_by.value} 前到家"
        )
    if constraints.total_distance_km is not None:
        constraint_summaries.append(
            f"全程不超过 {constraints.total_distance_km.value:g} km"
        )
    suffix = f"{'；'.join(constraint_summaries)}。" if constraint_summaries else ""
    return (
        f"已筛出 {len(candidate_set.plans)} 个可行方案：{plan_summaries}。{suffix}"
    )


def _plan_summary(index: int, plan: Plan) -> str:
    total_distance = sum(leg.distance_km for leg in plan.route_legs)
    return (
        f"方案 {index}「{plan.title}」"
        f"，{plan.total_duration_minutes} 分钟、{total_distance:.1f} km"
    )

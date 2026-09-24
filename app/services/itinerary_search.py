"""Bounded, route-aware search for local itinerary compositions.

This module deliberately stops at *candidate composition*.  Provider-backed
route facts and the final verifier remain in ``PlanningService``.  The search
uses deterministic estimates for ordering and only applies hard pruning when
an optimistic zero-travel bound already proves a prefix impossible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from app.domain.catalog import StopCandidate
from app.domain.constraints import NormalizedConstraints, StopRole
from app.domain.providers import GeoPoint
from app.services.itinerary_scheduler import TimelineScheduler


@dataclass(frozen=True)
class BeamSearchConfig:
    """Fixed safety budget; these values are never model-controlled."""

    beam_width: int = 16
    max_expansions: int = 200
    max_finalists: int = 12
    max_candidates_per_slot: int = 16
    route_leg_budget: int = 24


@dataclass(frozen=True)
class SearchState:
    selected_candidates: tuple[StopCandidate, ...]
    next_slot: int
    current_location: GeoPoint
    estimated_time: int
    accumulated_cost: float
    semantic_score: float
    estimated_distance: float


@dataclass(frozen=True)
class BeamSearchStats:
    mode: str
    beam_width: int
    max_expansions: int
    max_finalists: int
    expansions: int
    finalists: int
    pruned_by: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class BeamSearchResult:
    sequences: tuple[tuple[StopCandidate, ...], ...]
    rejected_fields: frozenset[str]
    stats: BeamSearchStats


def bounded_beam_search(
    *,
    role_pools: Sequence[Sequence[StopCandidate]],
    roles: tuple[StopRole, ...],
    constraints: NormalizedConstraints,
    origin: GeoPoint,
    semantic_scores: dict[str, float] | None = None,
    config: BeamSearchConfig | None = None,
    scheduler: TimelineScheduler | None = None,
) -> BeamSearchResult:
    """Return a bounded set of promising role-complete sequences.

    ``role_pools`` may contain the full retrieved pool.  Beam pruning happens
    after every slot, so the Cartesian product is never materialized.  Travel
    estimates rank states but never hard-reject them; hard time pruning uses
    only the optimistic zero-travel bound.  Final truth still comes from the
    existing local planner, Route Provider and Verifier.
    """

    config = config or BeamSearchConfig()
    if len(role_pools) != len(roles):
        raise ValueError("role_pools and roles must align")
    if not role_pools:
        return BeamSearchResult(
            sequences=(),
            rejected_fields=frozenset(),
            stats=BeamSearchStats(
                mode="beam",
                beam_width=config.beam_width,
                max_expansions=config.max_expansions,
                max_finalists=config.max_finalists,
                expansions=0,
                finalists=0,
            ),
        )

    start_minutes = _planning_start_minutes(constraints)
    maximum_minutes = _maximum_planning_minutes(constraints, start_minutes)
    budget = (
        constraints.budget_per_person.value
        if constraints.budget_per_person is not None
        else None
    )
    scheduler = scheduler or TimelineScheduler()
    beam: list[SearchState] = [
        SearchState(
            selected_candidates=(),
            next_slot=0,
            current_location=origin,
            estimated_time=start_minutes,
            accumulated_cost=0.0,
            semantic_score=0.0,
            estimated_distance=0.0,
        )
    ]
    complete: list[SearchState] = []
    rejected: dict[str, int] = {}
    expansions = 0

    for slot_index, (role, pool) in enumerate(zip(roles, role_pools, strict=True)):
        expanded: list[SearchState] = []
        remaining_slots = len(roles) - slot_index
        # Keep the global expansion budget meaningful even when the retriever
        # returns the whole catalog.  The cap is deterministic and leaves
        # enough budget for every remaining slot instead of consuming all
        # expansions on the first role.
        per_state_budget = max(
            1,
            (config.max_expansions - expansions)
            // max(1, len(beam) * remaining_slots),
        )
        candidate_limit = min(config.max_candidates_per_slot, per_state_budget)
        for state in beam:
            for candidate in pool[:candidate_limit]:
                if expansions >= config.max_expansions:
                    break
                expansions += 1
                if candidate.resource_id in {
                    item.resource_id for item in state.selected_candidates
                }:
                    _count(rejected, "duplicate_resource")
                    continue

                leg_distance = _haversine_km(state.current_location, candidate.location)
                if (
                    constraints.max_distance_km is not None
                    and leg_distance > constraints.max_distance_km.value
                ):
                    _count(rejected, "max_distance_km")
                    continue

                selected = (*state.selected_candidates, candidate)
                distances = tuple(
                    _haversine_km(
                        origin if index == 0 else selected[index - 1].location,
                        item.location,
                    )
                    for index, item in enumerate(selected)
                )
                travel_minutes = tuple(_estimated_route_minutes(value) for value in distances)
                estimated_schedule = scheduler.schedule(
                    start_minutes=start_minutes,
                    roles=roles[: slot_index + 1],
                    travel_minutes=travel_minutes,
                    stop_durations=tuple(item.duration_minutes for item in selected),
                )
                # The straight-line/25 km/h estimate is useful for ordering,
                # but it is not a safe lower bound for real traffic.  Only a
                # zero-travel schedule may reject a prefix here; Route
                # Provider + Verifier remain the authority on actual timing.
                lower_bound_schedule = scheduler.schedule(
                    start_minutes=start_minutes,
                    roles=roles[: slot_index + 1],
                    travel_minutes=(0,) * len(selected),
                    stop_durations=tuple(item.duration_minutes for item in selected),
                )
                accumulated_distance = state.estimated_distance + leg_distance
                accumulated_cost = state.accumulated_cost + float(candidate.avg_price or 0)

                if lower_bound_schedule.elapsed_minutes > maximum_minutes:
                    _count(rejected, "duration_minutes")
                    continue
                if constraints.strict_budget and budget is not None and accumulated_cost > budget:
                    _count(rejected, "budget_per_person")
                    continue
                if (
                    constraints.total_distance_km is not None
                    and accumulated_distance > constraints.total_distance_km.value
                ):
                    _count(rejected, "total_distance_km")
                    continue

                semantic_score = state.semantic_score + (
                    semantic_scores.get(candidate.resource_id, 0.0)
                    if semantic_scores
                    else 0.0
                )
                expanded.append(
                    SearchState(
                        selected_candidates=selected,
                        next_slot=slot_index + 1,
                        current_location=candidate.location,
                        estimated_time=estimated_schedule.end_minutes,
                        accumulated_cost=accumulated_cost,
                        semantic_score=semantic_score,
                        estimated_distance=accumulated_distance,
                    )
                )
            if expansions >= config.max_expansions:
                break

        if not expanded:
            beam = []
            break
        expanded.sort(key=_state_rank)
        beam = expanded[: config.beam_width]
        if slot_index == len(roles) - 1:
            complete = beam[: config.max_finalists]

    sequences = tuple(
        state.selected_candidates
        for state in sorted(complete, key=_state_rank)
    )
    return BeamSearchResult(
        sequences=sequences,
        rejected_fields=frozenset(rejected),
        stats=BeamSearchStats(
            mode="beam",
            beam_width=config.beam_width,
            max_expansions=config.max_expansions,
            max_finalists=config.max_finalists,
            expansions=expansions,
            finalists=len(sequences),
            pruned_by=tuple(sorted(rejected.items())),
        ),
    )


def _state_rank(state: SearchState) -> tuple[float, float, float, float, tuple[str, ...]]:
    return (
        -state.semantic_score,
        state.estimated_distance,
        state.estimated_time,
        state.accumulated_cost,
        tuple(item.resource_id for item in state.selected_candidates),
    )


def _count(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1


def _planning_start_minutes(constraints: NormalizedConstraints) -> int:
    value = (
        constraints.departure_at.value
        if constraints.departure_at is not None
        else constraints.time_window.value.start
    )
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _maximum_planning_minutes(constraints: NormalizedConstraints, start_minutes: int) -> int:
    end = constraints.time_window.value.end
    hour, minute = (int(part) for part in end.split(":"))
    available = hour * 60 + minute - start_minutes
    if constraints.duration_minutes is not None:
        return min(available, constraints.duration_minutes.value)
    return available


def _haversine_km(origin: GeoPoint, destination: GeoPoint) -> float:
    radius_km = 6371.0088
    start_latitude = math.radians(origin.latitude)
    end_latitude = math.radians(destination.latitude)
    latitude_delta = math.radians(destination.latitude - origin.latitude)
    longitude_delta = math.radians(destination.longitude - origin.longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(start_latitude)
        * math.cos(end_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))


def _estimated_route_minutes(distance_km: float) -> int:
    return max(5, round(distance_km / 25.0 * 60))

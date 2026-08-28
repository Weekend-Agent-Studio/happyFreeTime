"""Deterministic bounded planning over normalized Catalog candidates."""

from __future__ import annotations

import hashlib
import math
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from itertools import product
from zoneinfo import ZoneInfo

from app.domain.catalog import ConstraintViolation, PriceKind, ResourceType, StopCandidate
from app.domain.constraints import NormalizedConstraints
from app.domain.planning import (
    CandidateSet,
    ConstraintConflict,
    Plan,
    PlanPace,
    PlanPriceStatus,
    PlanSkeleton,
    PlanningIntent,
    RouteLeg,
    RouteMode,
    RouteSource,
    ScoreContribution,
    Stop,
    StopRole,
    StopType,
)
from app.domain.providers import (
    GeoPoint,
    ProviderSource,
    RouteFact,
    RouteRequest,
    WeatherRequest,
)
from app.providers.route import LocalEstimateRouteProvider, RouteProvider
from app.providers.weather import WeatherProvider, clear_mock_weather
from app.services.catalog import Catalog, SnapshotCatalog
from app.services.plan_verifier import PlanVerifier


_PREFERENCE_ALIASES: dict[str, frozenset[str]] = {
    "甜品": frozenset({"甜品", "甜点", "cake", "donut", "ice_cream", "bubble_tea"}),
    "安静": frozenset({"安静", "博物馆", "美术馆", "图书馆", "书店", "tea", "cafe", "coffee_shop"}),
    "亲子": frozenset({"亲子", "主题乐园", "动物园", "博物馆", "公园"}),
    "户外": frozenset({"户外", "公园", "运动"}),
}
_MAX_RETURNED_PLANS = 3
_MAX_ROUTE_LEG_VERIFICATIONS = 24
_MAX_CANDIDATES_PER_ROLE_FOR_MULTI_STOP = 8
_ACTIVITY_MEAL_SKELETON = PlanSkeleton(
    skeleton_id="activity-meal-v1",
    roles=(StopRole.ACTIVITY, StopRole.MEAL),
)
_LUNCH_ACTIVITY_DINNER_SKELETON = PlanSkeleton(
    skeleton_id="lunch-activity-dinner-v1",
    roles=(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
)
_ACTIVITY_BREAK_DINNER_SKELETON = PlanSkeleton(
    skeleton_id="activity-break-dinner-v1",
    roles=(StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER),
)
_ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON = PlanSkeleton(
    skeleton_id="activity-lunch-activity-dinner-v1",
    roles=(
        StopRole.ACTIVITY,
        StopRole.LUNCH,
        StopRole.ACTIVITY,
        StopRole.DINNER,
    ),
)
_ROLE_RESOURCE_TYPES: dict[StopRole, frozenset[ResourceType]] = {
    StopRole.ACTIVITY: frozenset({ResourceType.ACTIVITY}),
    StopRole.MEAL: frozenset(
        {ResourceType.RESTAURANT, ResourceType.CAFE, ResourceType.DESSERT}
    ),
    StopRole.LUNCH: frozenset({ResourceType.RESTAURANT}),
    StopRole.DINNER: frozenset({ResourceType.RESTAURANT}),
    StopRole.BREAK: frozenset({ResourceType.CAFE, ResourceType.DESSERT}),
}


class PlanningService:
    """Generate and verify bounded plans behind one stable interface."""

    def __init__(
        self,
        weather_provider: WeatherProvider | None = None,
        route_provider: RouteProvider | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        self._weather_provider = weather_provider or clear_mock_weather()
        self._route_provider = route_provider or LocalEstimateRouteProvider()
        self._catalog = catalog or SnapshotCatalog()
        self._plan_verifier = PlanVerifier()

    def plan(self, constraints: NormalizedConstraints) -> CandidateSet:
        if (
            constraints.location is None
            or constraints.time_window is None
            or constraints.date is None
        ):
            raise ValueError("planning requires normalized date, time window, and location")

        location = constraints.location.value
        weather = self._weather_provider.get_weather(
            WeatherRequest(
                city=location.city,
                district=location.district,
                adcode=_beijing_weather_adcode(location.district),
                date=constraints.date.value,
            )
        )
        catalog_result = self._catalog.recall(constraints)
        candidate_by_id = {
            candidate.resource_id: candidate
            for candidate in catalog_result.candidates
        }
        weather_removed = [
            candidate
            for candidate in catalog_result.candidates
            if (
                weather.is_adverse
                and candidate.resource_type == ResourceType.ACTIVITY
                and candidate.weather_sensitive
            )
        ]
        weather_removed_ids = {item.resource_id for item in weather_removed}
        candidates = [
            candidate
            for candidate in catalog_result.candidates
            if candidate.resource_id not in weather_removed_ids
        ]
        planning_intent = _build_planning_intent(constraints)
        local_result = _rank_skeleton_plans(
            candidates,
            constraints,
            _select_plan_skeletons(constraints, planning_intent),
            planning_intent,
        )
        if weather_removed and not any(
            candidate.resource_type == ResourceType.ACTIVITY
            for candidate in candidates
        ):
            local_result.rejected_fields.add("weather")
        route_candidates = _select_route_candidates(
            local_result.plans,
            _MAX_ROUTE_LEG_VERIFICATIONS,
        )
        plans = []
        route_failure_fields: set[str] = set()
        route_failure_field_sets: list[set[str]] = []
        for plan in route_candidates:
            verified = self._rebuild_route_timeline(
                plan,
                constraints,
                candidate_by_id,
            )
            verified = _refresh_verified_score(verified, constraints)
            verification = self._plan_verifier.verify(
                verified,
                constraints,
                candidate_by_id,
            )
            if not verification.is_feasible:
                issue_fields = {
                    violation.field
                    for violation in verification.violations
                }
                route_failure_fields.update(issue_fields)
                route_failure_field_sets.append(issue_fields)
                continue
            plans.append(verified)
            if len(plans) == _MAX_RETURNED_PLANS:
                break

        if plans:
            plans.sort(
                key=lambda plan: (
                    -plan.total_score,
                    plan.total_duration_minutes,
                    plan.composition_fingerprint,
                )
            )
            selected_ids = {
                stop.resource_id
                for plan in plans
                for stop in plan.stops
            }
            return CandidateSet(
                plans=plans,
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                catalog_warnings=[
                    warning
                    for warning in catalog_result.warnings
                    if warning.resource_id in selected_ids
                ],
            )

        if route_candidates:
            universal_failure_fields = (
                set.intersection(*route_failure_field_sets)
                if route_failure_field_sets
                else set()
            )
            reported_failure_fields = (
                universal_failure_fields or route_failure_fields
            )
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=ConstraintConflict(
                    code="NO_PLAN_AFTER_ROUTE_VERIFICATION",
                    message="路线和整单可行性复核后，候选方案均违反硬约束。",
                    fields=[
                        field
                        for field in (
                            "time_window",
                            "duration_minutes",
                            "max_distance_km",
                            "opening_hours",
                        )
                        if field in reported_failure_fields
                    ],
                    relaxation_options=_route_relaxation_options(
                        reported_failure_fields
                    ),
                ),
            )

        # 严格预算是硬约束：没有满足条件的结果时返回结构化冲突，不能偷偷放宽
        # 后仍告诉用户“已满足预算”。普通不可行则返回更通用的冲突类型。
        strict_budget_is_blocking = (
            local_result.rejected_fields == {"budget_per_person"}
            or (
                not local_result.rejected_fields
                and not catalog_result.candidates
                and _catalog_budget_is_universal(catalog_result.violations)
            )
        )
        if constraints.strict_budget and strict_budget_is_blocking:
            budget = constraints.budget_per_person.value if constraints.budget_per_person else None
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=ConstraintConflict(
                    code="NO_PLAN_WITHIN_STRICT_BUDGET",
                    message=f"当前目录中没有满足人均 {budget} 元严格预算的可行行程方案。",
                    fields=["budget_per_person"],
                    relaxation_options=[
                        "提高人均预算",
                        "只保留一个核心停靠点",
                        "允许免费活动搭配简餐",
                    ],
                )
            )

        conflict_fields = [
            field
            for field in (
                "duration_minutes",
                "time_window",
                "max_distance_km",
                "budget_per_person",
                "weather",
                "party",
            )
            if field in local_result.rejected_fields
        ]
        if not conflict_fields:
            conflict_fields = ["time_window", "max_distance_km", "party"]
        relaxation_options = []
        if "duration_minutes" in conflict_fields:
            relaxation_options.extend(["增加可用时长", "缩短停留时长"])
        if "max_distance_km" in conflict_fields:
            relaxation_options.append("扩大距离范围")
        if "budget_per_person" in conflict_fields:
            relaxation_options.append("提高人均预算")
        if "weather" in conflict_fields:
            relaxation_options.extend(["选择室内活动", "调整出行日期"])
        if "party" in conflict_fields:
            relaxation_options.append("调整活动偏好")
        return CandidateSet(
            provider_facts=[weather],
            catalog_violations=catalog_result.violations,
            conflict=ConstraintConflict(
                code="NO_FEASIBLE_PLAN",
                message="当前目录中没有满足全部硬约束的可行行程方案。",
                fields=conflict_fields,
                relaxation_options=relaxation_options,
            )
        )

    def _rebuild_route_timeline(
        self,
        plan: Plan,
        constraints: NormalizedConstraints,
        candidate_by_id: dict[str, StopCandidate],
    ) -> Plan:
        location = constraints.location.value
        current_point = GeoPoint(
            latitude=location.latitude,
            longitude=location.longitude,
        )
        current_name = "出发地"
        start_minutes = _time_to_minutes(constraints.time_window.value.start)
        current_minutes = start_minutes
        rebuilt_stops: list[Stop] = []
        rebuilt_legs: list[RouteLeg] = []

        for stop in plan.stops:
            resource = candidate_by_id.get(stop.resource_id)
            if resource is None:
                raise ValueError(f"route verification requires resource {stop.resource_id}")
            destination = resource.location
            route = self._route_provider.route(
                RouteRequest(
                    origin=current_point,
                    destination=destination,
                    mode=RouteMode.TAXI,
                    departure_at=(
                        datetime.combine(
                            constraints.date.value,
                            time.min,
                            tzinfo=ZoneInfo("Asia/Shanghai"),
                        )
                        + timedelta(minutes=current_minutes)
                    ),
                )
            )
            leg_start = _minutes_to_time(current_minutes)
            current_minutes += route.duration_minutes
            leg_end = _minutes_to_time(current_minutes)
            rebuilt_legs.append(
                RouteLeg(
                    origin_name=current_name,
                    destination_name=stop.name,
                    start=leg_start,
                    end=leg_end,
                    mode=route.mode,
                    distance_km=route.distance_km,
                    duration_minutes=route.duration_minutes,
                    source=_route_source(route),
                    provider_mode=route.provider_mode,
                    degraded=route.degraded,
                    degraded_reason=route.degraded_reason,
                    verified_at=route.verified_at,
                    cache_age_seconds=route.cache_age_seconds,
                    geometry=route.geometry,
                )
            )
            stop_start = _minutes_to_time(current_minutes)
            current_minutes += stop.duration_minutes
            rebuilt_stops.append(
                stop.model_copy(
                    update={
                        "start": stop_start,
                        "end": _minutes_to_time(current_minutes),
                    }
                )
            )
            current_point = destination
            current_name = stop.name

        return plan.model_copy(
            update={
                "stops": rebuilt_stops,
                "route_legs": rebuilt_legs,
                "total_duration_minutes": current_minutes - start_minutes,
            }
        )

@dataclass(frozen=True)
class _LocalPlanningResult:
    plans: list[Plan]
    rejected_fields: set[str]


def _select_route_candidates(
    plans: list[Plan],
    route_leg_budget: int,
) -> list[Plan]:
    by_skeleton: dict[str, deque[Plan]] = {}
    for plan in plans:
        key = plan.skeleton_id or "legacy-unknown"
        by_skeleton.setdefault(key, deque()).append(plan)

    selected: list[Plan] = []
    remaining_legs = route_leg_budget
    while remaining_legs > 0:
        added_this_round = False
        for candidates in by_skeleton.values():
            if not candidates:
                continue
            required_legs = len(candidates[0].stops)
            if required_legs > remaining_legs:
                candidates.clear()
                continue
            selected.append(candidates.popleft())
            remaining_legs -= required_legs
            added_this_round = True
        if not added_this_round:
            break
    return selected


def _build_planning_intent(
    constraints: NormalizedConstraints,
) -> PlanningIntent:
    preferences = {
        item.strip().casefold()
        for item in constraints.preferences
        if item.strip()
    }
    if preferences & {"轻松", "松弛", "不赶", "休闲"}:
        pace = PlanPace.RELAXED
        maximum_stops = 2
    elif preferences & {"丰富", "充实", "多玩几个", "尽量多"}:
        pace = PlanPace.FULL
        maximum_stops = 4
    else:
        pace = PlanPace.BALANCED
        maximum_stops = 4

    window = constraints.time_window.value
    start_minutes = _time_to_minutes(window.start)
    end_minutes = _time_to_minutes(window.end)
    includes_lunch = start_minutes <= 13 * 60 and end_minutes >= 12 * 60
    includes_dinner = start_minutes <= 19 * 60 and end_minutes >= 18 * 60
    includes_break = start_minutes <= 16 * 60 and end_minutes >= 18 * 60
    optional_roles = [StopRole.MEAL]
    if includes_lunch:
        optional_roles.append(StopRole.LUNCH)
    if includes_break:
        optional_roles.append(StopRole.BREAK)
    if includes_dinner:
        optional_roles.append(StopRole.DINNER)
    precedence: list[tuple[StopRole, StopRole]] = []
    if includes_lunch and includes_dinner:
        precedence.append((StopRole.LUNCH, StopRole.DINNER))
    if includes_break and includes_dinner:
        precedence.append((StopRole.BREAK, StopRole.DINNER))
    evidence = {
        "time_window": f"{window.start}-{window.end}",
        "pace": pace.value,
    }
    return PlanningIntent(
        required_roles=(StopRole.ACTIVITY,),
        optional_roles=tuple(optional_roles),
        precedence=tuple(precedence),
        minimum_stops=2,
        maximum_stops=maximum_stops,
        pace=pace,
        evidence=evidence,
    )


def _select_plan_skeletons(
    constraints: NormalizedConstraints,
    intent: PlanningIntent,
) -> tuple[PlanSkeleton, ...]:
    selected = [_ACTIVITY_MEAL_SKELETON]
    maximum_minutes, _ = _planning_minutes(constraints)
    if (
        intent.maximum_stops >= 3
        and {StopRole.LUNCH, StopRole.DINNER}.issubset(intent.optional_roles)
        and maximum_minutes >= 6 * 60
    ):
        selected.append(_LUNCH_ACTIVITY_DINNER_SKELETON)
    if (
        intent.maximum_stops >= 3
        and {StopRole.BREAK, StopRole.DINNER}.issubset(intent.optional_roles)
        and maximum_minutes >= 5 * 60
    ):
        selected.append(_ACTIVITY_BREAK_DINNER_SKELETON)
    if (
        intent.maximum_stops >= 4
        and {StopRole.LUNCH, StopRole.DINNER}.issubset(intent.optional_roles)
        and maximum_minutes >= 8 * 60
    ):
        selected.append(_ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON)
    return tuple(selected)


def _rank_skeleton_plans(
    candidates: list[StopCandidate],
    constraints: NormalizedConstraints,
    skeletons: tuple[PlanSkeleton, ...],
    planning_intent: PlanningIntent,
) -> _LocalPlanningResult:
    plans: list[Plan] = []
    rejected_fields: set[str] = set()
    for skeleton in skeletons:
        role_pool_limit = (
            _MAX_CANDIDATES_PER_ROLE_FOR_MULTI_STOP
            if len(skeleton.roles) >= 3
            else None
        )
        role_pools = [
            _candidates_for_role(
                candidates,
                role,
                constraints,
                limit=role_pool_limit,
            )
            for role in skeleton.roles
        ]
        if any(not pool for pool in role_pools):
            continue
        for sequence in product(*role_pools):
            if len({item.resource_id for item in sequence}) != len(sequence):
                continue
            plan, rejected_field = _build_local_plan(
                sequence,
                skeleton,
                constraints,
                planning_intent,
            )
            if plan is not None:
                plans.append(plan)
            elif rejected_field:
                rejected_fields.add(rejected_field)
    ranked = sorted(
        plans,
        key=lambda plan: (
            -plan.total_score,
            plan.total_duration_minutes,
            plan.composition_fingerprint,
        ),
    )
    unique_plans: list[Plan] = []
    seen_compositions: set[tuple[str, ...]] = set()
    for plan in ranked:
        composition = tuple(stop.resource_id for stop in plan.stops)
        if composition in seen_compositions:
            continue
        seen_compositions.add(composition)
        unique_plans.append(plan)
    return _LocalPlanningResult(plans=unique_plans, rejected_fields=rejected_fields)


def _candidates_for_role(
    candidates: list[StopCandidate],
    role: StopRole,
    constraints: NormalizedConstraints,
    *,
    limit: int | None,
) -> list[StopCandidate]:
    accepted_types = _ROLE_RESOURCE_TYPES[role]
    matching = [
        candidate
        for candidate in candidates
        if candidate.resource_type in accepted_types
    ]
    if limit is None or len(matching) <= limit:
        return matching
    return sorted(
        matching,
        key=lambda candidate: _candidate_role_rank(candidate, role, constraints),
    )[:limit]


def _candidate_role_rank(
    candidate: StopCandidate,
    role: StopRole,
    constraints: NormalizedConstraints,
) -> tuple[float, float, str]:
    """Cheap deterministic pre-rank used only to bound multi-stop enumeration."""

    terms = _candidate_terms(candidate)
    preference_matches = sum(
        bool(_matching_terms(preference, terms))
        for preference in dict.fromkeys(constraints.preferences)
    )
    scene_matches = len(
        _matching_requested_tags(constraints.scene_tags, candidate.scene_tags)
    )
    diet_matches = (
        len(_matching_requested_tags(constraints.diet_tags, candidate.diet_tags))
        if role in {StopRole.MEAL, StopRole.LUNCH, StopRole.DINNER}
        else 0
    )
    avoid_matches = sum(
        bool(_matching_terms(avoid, terms))
        for avoid in dict.fromkeys(constraints.avoid)
    )
    over_budget = bool(
        constraints.budget_per_person
        and candidate.price_kind
        in {PriceKind.KNOWN, PriceKind.ESTIMATED, PriceKind.FREE}
        and (candidate.avg_price or 0) > constraints.budget_per_person.value
    )
    local_score = (
        10.0 * preference_matches
        + 5.0 * scene_matches
        + 5.0 * diet_matches
        - 20.0 * avoid_matches
        - 10.0 * over_budget
    )
    origin = GeoPoint(
        latitude=constraints.location.value.latitude,
        longitude=constraints.location.value.longitude,
    )
    distance = _haversine_km(origin, candidate.location)
    return (-local_score, distance, candidate.resource_id)


def _build_local_plan(
    sequence: tuple[StopCandidate, ...],
    skeleton: PlanSkeleton,
    constraints: NormalizedConstraints,
    planning_intent: PlanningIntent,
) -> tuple[Plan | None, str | None]:
    if len(sequence) != len(skeleton.roles):
        raise ValueError("plan sequence must fill every skeleton role")

    location = constraints.location.value
    window = constraints.time_window.value
    origin = GeoPoint(latitude=location.latitude, longitude=location.longitude)
    route_distances: list[float] = []
    current_point = origin
    for candidate in sequence:
        route_distances.append(_haversine_km(current_point, candidate.location))
        current_point = candidate.location
    route_minutes = sum(
        _estimated_route_minutes(distance)
        for distance in route_distances
    )
    total_duration = sum(item.duration_minutes for item in sequence) + route_minutes
    maximum_minutes, target_minutes = _planning_minutes(constraints)
    if total_duration > maximum_minutes:
        return None, "duration_minutes" if constraints.duration_minutes else "time_window"
    if (
        constraints.max_distance_km
        and max(route_distances)
        > constraints.max_distance_km.value
    ):
        return None, "max_distance_km"

    people = 1
    if constraints.party:
        people = max(1, constraints.party.value.adults + constraints.party.value.children)
    per_person_price = sum(
        item.avg_price or 0
        for item in sequence
    )
    total_price = per_person_price * people
    if (
        constraints.strict_budget
        and constraints.budget_per_person
        and per_person_price > constraints.budget_per_person.value
    ):
        return None, "budget_per_person"

    requested_preferences = list(dict.fromkeys(constraints.preferences))
    terms: set[str] = set()
    for candidate in sequence:
        terms.update(_candidate_terms(candidate))
    preference_evidence = {
        preference: _matching_terms(preference, terms)
        for preference in requested_preferences
    }
    matched_preferences = [
        preference for preference, evidence in preference_evidence.items() if evidence
    ]
    meal_candidates = [
        candidate
        for role, candidate in zip(skeleton.roles, sequence)
        if role in {StopRole.MEAL, StopRole.LUNCH, StopRole.DINNER}
    ]
    matched_diet_tags = _matching_requested_tags(
        constraints.diet_tags,
        [
            tag
            for candidate in meal_candidates
            for tag in candidate.diet_tags
        ],
    )
    matched_scene_tags = _matching_requested_tags(
        constraints.scene_tags,
        [tag for candidate in sequence for tag in candidate.scene_tags],
    )
    avoid_evidence = {
        avoid: _matching_terms(avoid, terms)
        for avoid in dict.fromkeys(constraints.avoid)
    }
    matched_avoid = [avoid for avoid, evidence in avoid_evidence.items() if evidence]
    preference_score = min(20.0, 10.0 * len(matched_preferences))
    diet_score = min(10.0, 5.0 * len(matched_diet_tags))
    scene_score = min(10.0, 5.0 * len(matched_scene_tags))
    duration_score = round(max(
        0.0,
        15.0 * (1 - abs(target_minutes - total_duration) / target_minutes),
    ), 1)
    total_distance = sum(route_distances)
    distance_score = round(
        max(0.0, 5.0 - total_distance / 2),
        1,
    )
    if (
        skeleton.skeleton_id
        in {
            _LUNCH_ACTIVITY_DINNER_SKELETON.skeleton_id,
            _ACTIVITY_BREAK_DINNER_SKELETON.skeleton_id,
            _ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON.skeleton_id,
        }
        and {StopRole.LUNCH, StopRole.DINNER}.issubset(
            planning_intent.optional_roles
        )
    ):
        structure_score = 10.0
    elif planning_intent.pace == PlanPace.RELAXED and len(sequence) == 2:
        structure_score = 10.0
    else:
        structure_score = 5.0
    resource_ids = [item.resource_id for item in sequence]
    score_breakdown = [
        ScoreContribution(
            rule_id="planning.catalog_feasible.v1",
            dimension="feasibility",
            points=40.0,
            message=f"骨架中的 {len(sequence)} 个停靠点均通过 Catalog 单资源硬约束筛选。",
            evidence=resource_ids,
        ),
        ScoreContribution(
            rule_id="planning.skeleton_fit.v1",
            dimension="structure",
            points=structure_score,
            message="按时间窗口、节奏和角色覆盖评价行程骨架。",
            evidence=[
                f"skeleton_id={skeleton.skeleton_id}",
                f"pace={planning_intent.pace.value}",
                f"roles={','.join(role.value for role in skeleton.roles)}",
            ],
        ),
        ScoreContribution(
            rule_id="planning.duration_fit.v1",
            dimension="duration",
            points=duration_score,
            message=f"本地估算总时长 {total_duration} 分钟，目标 {target_minutes} 分钟。",
            evidence=[
                f"estimated_total_minutes={total_duration}",
                f"target_minutes={target_minutes}",
            ],
        ),
        ScoreContribution(
            rule_id="planning.travel_distance.v1",
            dimension="travel",
            points=distance_score,
            message="按出发地和相邻停靠点之间的直线距离下界评分。",
            evidence=[
                f"leg_{index}_distance_km={distance:.2f}"
                for index, distance in enumerate(route_distances, start=1)
            ],
        ),
    ]
    if requested_preferences:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.preference_tag_match.v1",
                dimension="preference",
                points=preference_score,
                message="按可审计概念映射与候选标签证据匹配偏好。",
                evidence=[
                    f"{preference}={tag}"
                    for preference in matched_preferences
                    for tag in preference_evidence[preference]
                ],
            )
        )
    if constraints.diet_tags:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.diet_tag_match.v1",
                dimension="diet",
                points=diet_score,
                message="按餐厅的结构化饮食标签匹配饮食偏好。",
                evidence=[f"matched={item}" for item in matched_diet_tags],
            )
        )
    if constraints.scene_tags:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.scene_tag_match.v1",
                dimension="scene",
                points=scene_score,
                message="按候选的结构化场景标签匹配同行场景。",
                evidence=[f"matched={item}" for item in matched_scene_tags],
            )
        )
    if matched_avoid:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.avoid_tag_penalty.v1",
                dimension="avoid",
                points=-20.0,
                message="候选标签命中用户希望避开的体验，降低排序分。",
                evidence=[
                    f"{avoid}={tag}"
                    for avoid in matched_avoid
                    for tag in avoid_evidence[avoid]
                ],
            )
        )
    prices_are_usable = all(
        item.price_kind in {PriceKind.KNOWN, PriceKind.ESTIMATED, PriceKind.FREE}
        for item in sequence
    )
    over_budget_preference = bool(
        constraints.budget_per_person
        and prices_are_usable
        and per_person_price > constraints.budget_per_person.value
    )
    if constraints.budget_per_person:
        if prices_are_usable:
            budget_message = (
                f"已知/估算人均合计 {per_person_price} 元，"
                f"预算偏好 {constraints.budget_per_person.value} 元。"
            )
            budget_evidence = [
                f"per_person_price={per_person_price}",
                f"budget_per_person={constraints.budget_per_person.value}",
            ]
        else:
            budget_message = "价格信息不完整，非严格预算不据此淘汰候选。"
            budget_evidence = ["price_status=incomplete"]
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.budget_preference.v1",
                dimension="budget",
                points=-10.0 if over_budget_preference else 0.0,
                message=budget_message,
                evidence=budget_evidence,
            )
        )
    total_score = round(sum(item.points for item in score_breakdown), 1)

    current_minutes = _time_to_minutes(window.start)
    stops: list[Stop] = []
    for role, candidate, distance in zip(
        skeleton.roles,
        sequence,
        route_distances,
    ):
        current_minutes += _estimated_route_minutes(distance)
        stops.append(_to_stop(candidate, role, current_minutes))
        current_minutes += candidate.duration_minutes
    highlights = ["已通过 Catalog 单资源硬约束筛选"]
    if matched_preferences:
        highlights.append(f"匹配偏好：{'、'.join(matched_preferences)}")
    if matched_diet_tags:
        highlights.append(f"匹配饮食：{'、'.join(matched_diet_tags)}")
    if matched_scene_tags:
        highlights.append(f"匹配场景：{'、'.join(matched_scene_tags)}")
    unmatched = [item for item in requested_preferences if item not in matched_preferences]
    tradeoffs = [f"未找到明确标签证据：{item}" for item in unmatched]
    tradeoffs.extend(
        f"未找到饮食标签证据：{item}"
        for item in constraints.diet_tags
        if item not in matched_diet_tags
    )
    tradeoffs.extend(
        f"未找到场景标签证据：{item}"
        for item in constraints.scene_tags
        if item not in matched_scene_tags
    )
    if matched_avoid:
        tradeoffs.append(f"命中避开项：{'、'.join(matched_avoid)}")
    if over_budget_preference:
        tradeoffs.append(
            f"人均合计 {per_person_price} 元，超出预算偏好 "
            f"{constraints.budget_per_person.value} 元"
        )
    elif constraints.budget_per_person and not prices_are_usable:
        tradeoffs.append("价格信息不完整，无法确认是否满足预算偏好")
    fingerprint = hashlib.sha256(
        "|".join([skeleton.skeleton_id, *resource_ids]).encode("utf-8")
    ).hexdigest()[:12]
    return Plan(
        plan_id=f"plan-{uuid.uuid4().hex}",
        composition_fingerprint=f"composition-{fingerprint}",
        skeleton_id=skeleton.skeleton_id,
        title=" + ".join(item.name for item in sequence),
        strategy=(
            "偏好优先"
            if matched_preferences or matched_diet_tags or matched_scene_tags
            else "时间利用"
        ),
        total_score=total_score,
        total_price=total_price,
        price_status=_plan_price_status(stops),
        total_duration_minutes=total_duration,
        stops=stops,
        route_legs=[],
        score_breakdown=score_breakdown,
        highlights=highlights,
        tradeoffs=tradeoffs,
    ), None


def _to_stop(
    candidate: StopCandidate,
    role: StopRole,
    start_minutes: int,
) -> Stop:
    return Stop(
        resource_id=candidate.resource_id,
        type=StopType(candidate.resource_type.value),
        role=role,
        name=candidate.name,
        start=_minutes_to_time(start_minutes),
        end=_minutes_to_time(start_minutes + candidate.duration_minutes),
        duration_minutes=candidate.duration_minutes,
        price=candidate.avg_price or 0,
        price_kind=candidate.price_kind,
        category_tags=candidate.category_tags,
        image=candidate.image,
        source=candidate.source,
    )


def _refresh_verified_score(
    plan: Plan,
    constraints: NormalizedConstraints,
) -> Plan:
    _, target_minutes = _planning_minutes(constraints)
    duration_score = round(
        max(
            0.0,
            15.0
            * (
                1
                - abs(target_minutes - plan.total_duration_minutes)
                / target_minutes
            ),
        ),
        1,
    )
    total_distance = sum(leg.distance_km for leg in plan.route_legs)
    distance_score = round(max(0.0, 5.0 - total_distance / 2), 1)
    refreshed: list[ScoreContribution] = []
    for contribution in plan.score_breakdown:
        if contribution.rule_id == "planning.duration_fit.v1":
            refreshed.append(
                contribution.model_copy(
                    update={
                        "points": duration_score,
                        "message": (
                            f"路线复核后总时长 {plan.total_duration_minutes} 分钟，"
                            f"目标 {target_minutes} 分钟。"
                        ),
                        "evidence": [
                            f"route_checked_total_minutes={plan.total_duration_minutes}",
                            f"target_minutes={target_minutes}",
                        ],
                    }
                )
            )
        elif contribution.rule_id == "planning.travel_distance.v1":
            refreshed.append(
                contribution.model_copy(
                    update={
                        "points": distance_score,
                        "message": "按 Route Provider 返回的全部路线段距离评分。",
                        "evidence": [
                            f"route_checked_total_distance_km={total_distance:.2f}",
                        ],
                    }
                )
            )
        else:
            refreshed.append(contribution)
    return plan.model_copy(
        update={
            "score_breakdown": refreshed,
            "total_score": round(sum(item.points for item in refreshed), 1),
        }
    )


def _planning_minutes(constraints: NormalizedConstraints) -> tuple[int, int]:
    window = constraints.time_window.value
    available_minutes = _time_to_minutes(window.end) - _time_to_minutes(window.start)
    maximum_minutes = min(
        available_minutes,
        (
            constraints.duration_minutes.value
            if constraints.duration_minutes
            else available_minutes
        ),
    )
    target_minutes = (
        maximum_minutes
        if constraints.duration_minutes
        else max(1, round(available_minutes * 0.8))
    )
    return maximum_minutes, target_minutes


def _route_relaxation_options(fields: set[str]) -> list[str]:
    options: list[str] = []
    if fields & {"time_window", "duration_minutes"}:
        options.extend(["延长可用时间", "缩短停留时长"])
    if "max_distance_km" in fields:
        options.append("选择路程更短的地点")
    if "opening_hours" in fields:
        options.append("调整到店时间或选择营业时段更匹配的地点")
    return options


def _candidate_terms(candidate: StopCandidate) -> set[str]:
    return {
        value.strip().casefold()
        for value in (
            candidate.name,
            *candidate.category_tags,
            *candidate.preference_tags,
            *candidate.diet_tags,
            *candidate.scene_tags,
        )
        if value.strip()
    }


def _matching_terms(preference: str, terms: set[str]) -> list[str]:
    normalized = preference.strip().casefold()
    accepted = {normalized, *(_PREFERENCE_ALIASES.get(normalized, frozenset()))}
    return sorted(
        term
        for term in terms
        if term in accepted or normalized in term
    )


def _matching_requested_tags(
    requested: list[str],
    available: list[str],
) -> list[str]:
    available_normalized = {item.strip().casefold() for item in available}
    return [
        item
        for item in dict.fromkeys(requested)
        if item.strip().casefold() in available_normalized
    ]


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


def _catalog_budget_is_universal(
    violations: list[ConstraintViolation],
) -> bool:
    fields_by_resource: dict[str, set[str]] = {}
    for violation in violations:
        fields_by_resource.setdefault(violation.resource_id, set()).add(violation.field)
    return bool(fields_by_resource) and all(
        "budget_per_person" in fields
        for fields in fields_by_resource.values()
    )


def _beijing_weather_adcode(district: str) -> str:
    """M2 首批北京范围的显式映射；后续由 Geocoding Provider 提供。"""
    adcodes = {
        "东城区": "110101",
        "西城区": "110102",
        "朝阳区": "110105",
        "丰台区": "110106",
        "石景山区": "110107",
        "海淀区": "110108",
    }
    return adcodes.get(district, "110000")


def _route_source(fact: RouteFact) -> RouteSource:
    sources = {
        ProviderSource.AMAP_LIVE: RouteSource.REAL_PROVIDER,
        ProviderSource.CACHE: RouteSource.CACHE,
        ProviderSource.REPLAY: RouteSource.REPLAY,
        ProviderSource.MOCK: RouteSource.LOCAL_ESTIMATE,
        ProviderSource.LOCAL_ESTIMATE: RouteSource.LOCAL_ESTIMATE,
    }
    return sources[fact.source]


def _plan_price_status(stops: list[Stop]) -> PlanPriceStatus:
    kinds = {stop.price_kind for stop in stops}
    if PriceKind.UNKNOWN in kinds:
        return PlanPriceStatus.INCOMPLETE
    if PriceKind.ESTIMATED in kinds:
        return PlanPriceStatus.ESTIMATED
    return PlanPriceStatus.KNOWN


def _time_to_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _minutes_to_time(value: int) -> str:
    hour, minute = divmod(value, 60)
    return f"{hour:02d}:{minute:02d}"

"""V2 Planner 接口与现有 V1 Mock 规划实现之间的适配层。"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.domain.catalog import PriceKind, ResourceType, StopCandidate
from app.domain.constraints import NormalizedConstraints
from app.domain.planning import (
    CandidateSet,
    ConstraintConflict,
    Plan,
    PlanPriceStatus,
    RouteLeg,
    RouteMode,
    RouteSource,
    Stop,
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
from services.planning_service import generate_candidate_plans
from services.scoring_service import compare_plans


class PlanningService:
    """在稳定的 Pydantic 接口后生成 M1 双站候选方案。

    当前内部复用 V1 的 Mock 目录、组合生成和评分函数。将来更换为 Beam Search
    或真实 POI 检索时，调用方仍只依赖 ``plan(constraints) -> CandidateSet``。
    """

    def __init__(
        self,
        weather_provider: WeatherProvider | None = None,
        route_provider: RouteProvider | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        self._weather_provider = weather_provider or clear_mock_weather()
        self._route_provider = route_provider or LocalEstimateRouteProvider()
        self._catalog = catalog or SnapshotCatalog()

    def plan(self, constraints: NormalizedConstraints) -> CandidateSet:
        # 先把 V2 约束转换成冻结的 V1 字典输入，再将 V1 输出转回 V2 模型。
        # 这种适配让迁移可分阶段完成，不要求一次重写规划算法和整个调用链。
        legacy_constraints = self._to_legacy_constraints(constraints)
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
        activity_candidates = [
            candidate.to_legacy_record()
            for candidate in catalog_result.candidates
            if candidate.resource_type == ResourceType.ACTIVITY
        ]
        if weather.is_adverse:
            activity_candidates = [
                candidate
                for candidate in activity_candidates
                if not candidate.get("weather_sensitive", False)
            ]
        restaurant_candidates = [
            candidate.to_legacy_record()
            for candidate in catalog_result.candidates
            if candidate.resource_type == ResourceType.RESTAURANT
        ]
        legacy_plans = generate_candidate_plans(
            legacy_constraints,
            activity_candidates=activity_candidates,
            restaurant_candidates=restaurant_candidates,
            product_candidates=[],
        )
        scored_plans = compare_plans(legacy_plans, legacy_constraints)
        scored_plans = _apply_catalog_compatibility_score(scored_plans)
        local_finalists = [
            self._to_plan(plan, candidate_by_id)
            for plan in scored_plans
            if self._is_feasible(plan, constraints)
        ][:3]
        plans = []
        route_failure_fields: set[str] = set()
        for plan in local_finalists:
            verified = self._rebuild_route_timeline(
                plan,
                constraints,
                candidate_by_id,
            )
            if verified.stops[-1].end > constraints.time_window.value.end:
                route_failure_fields.add("time_window")
                continue
            if any(
                leg.distance_km > constraints.max_distance_km.value
                for leg in verified.route_legs
            ):
                route_failure_fields.add("max_distance_km")
                continue
            plans.append(verified)

        if plans:
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

        if local_finalists:
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=ConstraintConflict(
                    code="NO_PLAN_AFTER_ROUTE_VERIFICATION",
                    message="路线复核后，候选方案均违反时间或单段距离限制。",
                    fields=[
                        field
                        for field in ("time_window", "max_distance_km")
                        if field in route_failure_fields
                    ],
                    relaxation_options=[
                        "延长可用时间",
                        "缩短停留时长",
                        "选择路程更短的地点",
                    ],
                ),
            )

        # 严格预算是硬约束：没有满足条件的结果时返回结构化冲突，不能偷偷放宽
        # 后仍告诉用户“已满足预算”。普通不可行则返回更通用的冲突类型。
        if constraints.strict_budget:
            budget = constraints.budget_per_person.value if constraints.budget_per_person else None
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=ConstraintConflict(
                    code="NO_PLAN_WITHIN_STRICT_BUDGET",
                    message=f"当前目录中没有满足人均 {budget} 元严格预算的双站方案。",
                    fields=["budget_per_person"],
                    relaxation_options=[
                        "提高人均预算",
                        "只保留一个核心停靠点",
                        "允许免费活动搭配简餐",
                    ],
                )
            )

        return CandidateSet(
            provider_facts=[weather],
            catalog_violations=catalog_result.violations,
            conflict=ConstraintConflict(
                code="NO_FEASIBLE_TWO_STOP_PLAN",
                message="当前目录中没有满足时间、距离和人群约束的双站方案。",
                fields=["time_window", "max_distance_km", "party"],
                relaxation_options=["扩大距离范围", "延长可用时间", "调整活动偏好"],
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
                    departure_at=datetime.combine(
                        constraints.date.value,
                        time(hour=current_minutes // 60, minute=current_minutes % 60),
                        tzinfo=ZoneInfo("Asia/Shanghai"),
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

    @staticmethod
    def _to_legacy_constraints(constraints: NormalizedConstraints) -> dict:
        """把强类型 V2 约束转换成 V1 Planner 仍在使用的字典格式。"""
        if constraints.location is None or constraints.time_window is None or constraints.date is None:
            raise ValueError("planning requires normalized date, time window, and location")

        location = constraints.location.value
        window = constraints.time_window.value
        party = constraints.party.value if constraints.party else None
        return {
            "location": {
                "lat": location.latitude,
                "lng": location.longitude,
                "address": location.address,
            },
            "time_window": {
                "date": constraints.date.value.isoformat(),
                "start": window.start,
                "end": window.end,
            },
            "party_profile": party.model_dump() if party else {"adults": 1, "children": 0},
            "max_distance_km": (
                constraints.max_distance_km.value
                if constraints.max_distance_km
                else 8.0
            ),
            "budget_per_person": (
                constraints.budget_per_person.value
                if constraints.budget_per_person
                else None
            ),
            "preferences": constraints.preferences,
            "diet_tags": constraints.diet_tags,
            "scene_tags": constraints.scene_tags,
            "avoid_tags": constraints.avoid,
        }

    @staticmethod
    def _is_feasible(plan: dict, constraints: NormalizedConstraints) -> bool:
        # 评分高不代表候选有效。硬约束必须在输出 API 前再次过滤，避免排序算法
        # 把超时、超严格预算或缺少双站结构的方案推到前端。
        resource_items = [
            item for item in plan.get("items", [])
            if item.get("type") in {"activity", "restaurant"}
        ]
        if len(resource_items) != 2:
            return False
        if constraints.time_window:
            window = constraints.time_window.value
            if resource_items[-1]["end"] > window.end:
                return False
        if constraints.strict_budget and constraints.budget_per_person:
            party = constraints.party.value if constraints.party else None
            people = (party.adults + party.children) if party else 1
            allowed_total = constraints.budget_per_person.value * max(people, 1)
            if plan.get("total_price", 0) > allowed_total:
                return False
        return plan.get("total_score", 0) > 0

    @staticmethod
    def _to_plan(
        plan: dict,
        candidate_by_id: dict[str, StopCandidate],
    ) -> Plan:
        """将 V1 混合 items 列表拆成 V2 的 stops 与 route_legs。"""
        stops: list[Stop] = []
        route_legs: list[RouteLeg] = []
        previous_name = "出发地"

        for item in plan.get("items", []):
            item_type = item.get("type")
            if item_type == "route":
                title = item.get("title", "本地路线估算")
                destination_name = title.split("前往", 1)[-1] if "前往" in title else "下一站"
                route_legs.append(
                    RouteLeg(
                        origin_name=previous_name,
                        destination_name=destination_name,
                        start=item["start"],
                        end=item["end"],
                        mode=RouteMode.TAXI,
                        distance_km=item.get("distance_km", 0),
                        duration_minutes=item.get("duration_minutes", 0),
                        source=RouteSource.LOCAL_ESTIMATE,
                        degraded=True,
                        degraded_reason="M1 使用 Haversine 距离与规则速度估算",
                    )
                )
                continue
            if item_type not in {"activity", "restaurant"}:
                continue
            candidate = candidate_by_id[item["resource_id"]]
            stop = Stop(
                resource_id=item["resource_id"],
                type=StopType(item_type),
                name=item["title"],
                start=item["start"],
                end=item["end"],
                duration_minutes=item["duration_minutes"],
                price=item.get("price", 0),
                price_kind=candidate.price_kind,
                category_tags=candidate.category_tags,
                image=candidate.image,
                source=candidate.source,
            )
            stops.append(stop)
            previous_name = stop.name

        return Plan(
            plan_id=plan["plan_id"],
            title=plan["title"],
            strategy=plan["plan_type"],
            total_score=plan.get("total_score", 0),
            total_price=plan.get("total_price", 0),
            price_status=_plan_price_status(stops),
            total_duration_minutes=plan.get("total_duration_minutes", 0),
            stops=stops,
            route_legs=route_legs,
            highlights=plan.get("highlights", []),
            tradeoffs=plan.get("tradeoffs", []),
        )


def _apply_catalog_compatibility_score(plans: list[dict]) -> list[dict]:
    """Keep new Catalog IDs usable until the V1 scorer is replaced in slice E.

    The legacy scorer only knows IDs from its bundled JSON fixtures.  Catalog
    hard pruning has already established feasibility, so an entirely unscored
    result set receives an explicit neutral score at this adapter boundary.
    """
    if not plans or any(plan.get("total_score", 0) > 0 for plan in plans):
        return plans

    compatible: list[dict] = []
    for plan in plans:
        plan_copy = dict(plan)
        plan_copy["total_score"] = 60.0
        plan_copy["highlights"] = list(dict.fromkeys([
            *plan.get("highlights", []),
            "已通过 Catalog 单资源硬约束筛选",
        ]))
        plan_copy["tradeoffs"] = list(dict.fromkeys([
            *plan.get("tradeoffs", []),
            "当前为兼容层中性评分，完整多策略质量评分尚未实现",
        ]))
        compatible.append(plan_copy)
    return compatible


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

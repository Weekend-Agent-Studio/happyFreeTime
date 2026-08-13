"""V2 Planner 接口与现有 V1 Mock 规划实现之间的适配层。"""

from __future__ import annotations

from app.domain.constraints import NormalizedConstraints
from app.domain.planning import (
    CandidateSet,
    ConstraintConflict,
    Plan,
    RouteLeg,
    RouteMode,
    RouteSource,
    Stop,
    StopType,
)
from services.planning_service import generate_candidate_plans
from services.scoring_service import compare_plans


class PlanningService:
    """在稳定的 Pydantic 接口后生成 M1 双站候选方案。

    当前内部复用 V1 的 Mock 目录、组合生成和评分函数。将来更换为 Beam Search
    或真实 POI 检索时，调用方仍只依赖 ``plan(constraints) -> CandidateSet``。
    """

    def plan(self, constraints: NormalizedConstraints) -> CandidateSet:
        # 先把 V2 约束转换成冻结的 V1 字典输入，再将 V1 输出转回 V2 模型。
        # 这种适配让迁移可分阶段完成，不要求一次重写规划算法和整个调用链。
        legacy_constraints = self._to_legacy_constraints(constraints)
        legacy_plans = generate_candidate_plans(
            legacy_constraints,
            product_candidates=[],
        )
        scored_plans = compare_plans(legacy_plans, legacy_constraints)
        plans = [
            self._to_plan(plan)
            for plan in scored_plans
            if self._is_feasible(plan, constraints)
        ][:3]

        if plans:
            return CandidateSet(plans=plans)

        # 严格预算是硬约束：没有满足条件的结果时返回结构化冲突，不能偷偷放宽
        # 后仍告诉用户“已满足预算”。普通不可行则返回更通用的冲突类型。
        if constraints.strict_budget:
            budget = constraints.budget_per_person.value if constraints.budget_per_person else None
            return CandidateSet(
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
            conflict=ConstraintConflict(
                code="NO_FEASIBLE_TWO_STOP_PLAN",
                message="当前目录中没有满足时间、距离和人群约束的双站方案。",
                fields=["time_window", "max_distance_km", "party"],
                relaxation_options=["扩大距离范围", "延长可用时间", "调整活动偏好"],
            )
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
    def _to_plan(plan: dict) -> Plan:
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
            stop = Stop(
                resource_id=item["resource_id"],
                type=StopType(item_type),
                name=item["title"],
                start=item["start"],
                end=item["end"],
                duration_minutes=item["duration_minutes"],
                price=item.get("price", 0),
            )
            stops.append(stop)
            previous_name = stop.name

        return Plan(
            plan_id=plan["plan_id"],
            title=plan["title"],
            strategy=plan["plan_type"],
            total_score=plan.get("total_score", 0),
            total_price=plan.get("total_price", 0),
            total_duration_minutes=plan.get("total_duration_minutes", 0),
            stops=stops,
            route_legs=route_legs,
            highlights=plan.get("highlights", []),
            tradeoffs=plan.get("tradeoffs", []),
        )

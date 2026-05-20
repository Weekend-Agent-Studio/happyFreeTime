"""
方案生成服务：将活动、餐厅、商品候选组装成完整 itinerary，
生成 safe/budget/rich 三类候选方案，并校验时间线。
"""
import uuid
from typing import Optional

from services.catalog_service import get_resource_detail, search_activities, search_restaurants, search_products
from services.feasibility_service import estimate_route
from services.scoring_service import score_activity, score_restaurant


# ---- 时间工具 ----

def _time_to_minutes(t: str) -> int:
    """将 'HH:MM' 转为分钟数。"""
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _minutes_to_time(m: int) -> str:
    """将分钟数转为 'HH:MM'。"""
    h, m2 = divmod(m, 60)
    return f"{h:02d}:{m2:02d}"


def _add_minutes(start_time: str, minutes: int) -> str:
    """start_time + minutes → 新时间字符串。"""
    return _minutes_to_time(_time_to_minutes(start_time) + minutes)


# ---- 核心方案生成 ----

def generate_candidate_plans(
    constraints: dict,
    activity_candidates: Optional[list[dict]] = None,
    restaurant_candidates: Optional[list[dict]] = None,
    product_candidates: Optional[list[dict]] = None,
) -> list[dict]:
    """
    根据约束和候选资源生成 2-3 个候选方案。

    约束结构：
        constraints = {
            "location": {"lat": 39.90, "lng": 116.47, "address": "..."},
            "time_window": {"date": "2026-05-23", "start": "14:00", "end": "18:00"},
            "party_profile": {"adults": 2, "children": 1, "child_age": 5},
            "max_distance_km": 8,
            "budget_per_person": 120,
            "preferences": ["亲子", "室内"],
            "diet_tags": ["低卡", "儿童餐"],
            "scene_tags": ["家庭"],
        }

    返回：
        [{"plan_id": str, "title": str, "plan_type": str,
          "total_score": float, "total_price": int,
          "total_duration_minutes": int, "items": [...],
          "highlights": [str], "tradeoffs": [str],
          "required_confirmations": [...]}, ...]
    """
    # 1. 获取或搜索候选
    location = constraints.get("location", {})
    time_window = constraints.get("time_window", {})
    party = constraints.get("party_profile", {})
    max_dist = constraints.get("max_distance_km", 8)

    if activity_candidates is None:
        tags = constraints.get("preferences", [])
        activity_candidates = search_activities(
            tags=tags, location=location, radius_km=max_dist,
            time_window=time_window, party_profile=party,
        )
    if restaurant_candidates is None:
        diet_tags = constraints.get("diet_tags", [])
        scene_tags = constraints.get("scene_tags", [])
        total_people = party.get("adults", 1) + party.get("children", 0)
        restaurant_candidates = search_restaurants(
            diet_tags=diet_tags, scene_tags=scene_tags,
            location=location, radius_km=max_dist,
            party_size=total_people, time_window=time_window,
        )
    if product_candidates is None:
        product_candidates = search_products(
            tags=constraints.get("preferences", []),
            location=location, radius_km=max_dist,
        )

    # 2. 评分
    scored_activities = [_score_and_attach(a, constraints) for a in activity_candidates]
    scored_restaurants = [_score_and_attach(r, constraints, is_restaurant=True) for r in restaurant_candidates]
    scored_activities.sort(key=lambda x: x["_score"], reverse=True)
    scored_restaurants.sort(key=lambda x: x["_score"], reverse=True)

    if not scored_activities or not scored_restaurants:
        return _empty_plans(constraints)

    # 3. 生成方案
    plans = []

    # --- safe 方案：高分室内 + 高匹配餐厅 ---
    safe_plan = _build_plan(
        plan_type="safe",
        title="稳妥舒适方案",
        activity=scored_activities[0],  # 最高分活动
        restaurant=_find_best_restaurant(scored_restaurants, scored_activities[0]),
        constraints=constraints,
        product=_pick_product(product_candidates, constraints),
    )
    if safe_plan:
        plans.append(safe_plan)

    budget_plan = None
    # --- budget 方案：用免费或低价活动 + 实惠餐厅 ---
    budget_act = _find_budget_candidate(scored_activities)
    budget_rest = _find_budget_candidate(scored_restaurants)
    if not budget_act:
        budget_act = scored_activities[-1] if len(scored_activities) > 1 else scored_activities[0]
    if not budget_rest:
        budget_rest = scored_restaurants[-1] if len(scored_restaurants) > 1 else scored_restaurants[0]

    if budget_act != scored_activities[0] or budget_rest != scored_restaurants[0]:
        budget_plan = _build_plan(
            plan_type="budget",
            title="经济实惠方案",
            activity=budget_act,
            restaurant=budget_rest,
            constraints=constraints,
        )
        if budget_plan:
            plans.append(budget_plan)

    # --- rich 方案：体验最丰富的组合（可能距离远或预算高） ---
    if len(scored_activities) > 1 and len(scored_restaurants) > 1:
        # 选不同风格的组合
        rich_act = scored_activities[1] if len(scored_activities) > 1 else scored_activities[0]
        rich_rest = scored_restaurants[1] if len(scored_restaurants) > 1 else scored_restaurants[0]
        rich_plan = _build_plan(
            plan_type="rich",
            title="丰富体验方案",
            activity=rich_act,
            restaurant=rich_rest or scored_restaurants[0],
            constraints=constraints,
            product=_pick_product(product_candidates, constraints),
        )
        if rich_plan:
            plans.append(rich_plan)

    # 去重：plan type 不同但内容相同
    unique_plans = _deduplicate_plans(plans)
    return unique_plans


def _score_and_attach(item: dict, constraints: dict, is_restaurant: bool = False) -> dict:
    """对单个 item 评分并将分数附加到 item 上。"""
    rid = item["id"]
    if is_restaurant:
        result = score_restaurant(rid, constraints)
        score_val = result.get("score", 0)
    else:
        result = score_activity(rid, constraints)
        score_val = result.get("score", 0)
    item["_score"] = score_val
    item["_score_reasons"] = result.get("reasons", [])
    item["_score_penalties"] = result.get("penalties", [])
    return item


def _find_best_restaurant(restaurants: list[dict], activity: dict) -> Optional[dict]:
    """找到与活动最匹配的餐厅（考虑距离衔接）。"""
    if not restaurants:
        return None
    # 简化：返回最高分餐厅
    return restaurants[0]


def _find_budget_candidate(candidates: list[dict]) -> Optional[dict]:
    """找低价候选：免费或最低价。"""
    free = [c for c in candidates if c.get("avg_price", 0) == 0]
    if free:
        return free[0]
    # 返回价格最低且评分不太低的
    sorted_by_price = sorted(candidates, key=lambda c: c.get("avg_price", 999))
    for c in sorted_by_price:
        if c.get("rating", 0) >= 4.0:
            return c
    return sorted_by_price[0] if sorted_by_price else None


def _pick_product(products: list[dict], constraints: dict) -> Optional[dict]:
    """从商品列表中挑一个合适的。"""
    if not products:
        return None
    prefs = set(constraints.get("preferences", []))
    party = constraints.get("party_profile", {})
    children = party.get("children", 0)

    for p in products:
        p_id = p.get("id", "")
        if children > 0 and "适合家庭" in p.get("tags", []):
            return p
    return products[0]


def _build_plan(
    *,
    plan_type: str,
    title: str,
    activity: dict,
    restaurant: dict,
    constraints: dict,
    product: Optional[dict] = None,
) -> Optional[dict]:
    """
    构建单个方案：组装活动 → 路线 → 餐厅 → (可选路线 → 商品)，并校验时间线。
    返回 None 表示无法构建有效方案。
    """
    location = constraints.get("location", {})
    time_window = constraints.get("time_window", {})
    start_time = time_window.get("start", "14:00")
    date = time_window.get("date", "")
    party = constraints.get("party_profile", {})

    items: list[dict] = []
    current_time = start_time
    total_price = 0
    highlights: list[str] = []
    tradeoffs: list[str] = []
    confirmations: list[dict] = []

    act_lat = activity.get("lat")
    act_lng = activity.get("lng")
    act_addr = activity.get("address", "")
    act_name = activity.get("name", "")

    # Step 1: 路线 → 活动
    if location and act_lat is not None:
        dest = {"lat": act_lat, "lng": act_lng, "address": act_addr}
        route = estimate_route(location, dest, mode="taxi")
        items.append({
            "start": current_time,
            "end": _add_minutes(current_time, route["duration_minutes"]),
            "type": "route",
            "title": f"从{location.get('address', '当前')}打车前往{act_name}",
            "duration_minutes": route["duration_minutes"],
            "distance_km": route["distance_km"],
        })
        current_time = items[-1]["end"]

    # Step 2: 活动
    act_duration = activity.get("duration_minutes", 90)
    act_id = activity.get("id", "")
    act_price = 0
    packages = activity.get("packages", [])
    if packages:
        # 选择合适的套餐
        pkg = _select_package(packages, party)
        act_price = pkg.get("price", 0) if pkg else activity.get("avg_price", 0) * party.get("adults", 1)
    else:
        act_price = activity.get("avg_price", 0) * party.get("adults", 1)

    items.append({
        "start": current_time,
        "end": _add_minutes(current_time, act_duration),
        "type": "activity",
        "resource_id": act_id,
        "title": act_name,
        "duration_minutes": act_duration,
        "price": act_price,
    })
    total_price += act_price
    current_time = items[-1]["end"]

    # 收集亮点
    highlights.extend(activity.get("_score_reasons", [])[:3])
    tradeoffs.extend(activity.get("_score_penalties", [])[:2])

    if activity.get("booking_required"):
        confirmations.append({
            "action": "book_tickets",
            "resource_id": act_id,
            "resource_name": act_name,
            "time": items[-1]["start"],
        })

    # Step 3: 路线 → 餐厅
    rest_lat = restaurant.get("lat")
    rest_lng = restaurant.get("lng")
    rest_addr = restaurant.get("address", "")
    rest_name = restaurant.get("name", "")
    rest_id = restaurant.get("id", "")

    if act_lat is not None and rest_lat is not None:
        origin = {"lat": act_lat, "lng": act_lng, "address": act_addr}
        dest = {"lat": rest_lat, "lng": rest_lng, "address": rest_addr}
        route = estimate_route(origin, dest, mode="taxi")
        items.append({
            "start": current_time,
            "end": _add_minutes(current_time, route["duration_minutes"]),
            "type": "route",
            "title": f"从{act_name}打车前往{rest_name}",
            "duration_minutes": route["duration_minutes"],
            "distance_km": route["distance_km"],
        })
        current_time = items[-1]["end"]

    # Step 4: 餐厅
    rest_duration = restaurant.get("duration_minutes", 60)
    rest_price = restaurant.get("avg_price", 0)
    party_count = party.get("adults", 1) + party.get("children", 0)

    items.append({
        "start": current_time,
        "end": _add_minutes(current_time, rest_duration),
        "type": "restaurant",
        "resource_id": rest_id,
        "title": rest_name,
        "duration_minutes": rest_duration,
        "price": rest_price * party_count,
    })
    total_price += rest_price * party_count
    current_time = items[-1]["end"]

    highlights.extend(restaurant.get("_score_reasons", [])[:2])
    tradeoffs.extend(restaurant.get("_score_penalties", [])[:2])

    if restaurant.get("reservation_required"):
        confirmations.append({
            "action": "reserve_restaurant",
            "resource_id": rest_id,
            "resource_name": rest_name,
            "time": items[-1]["start"],
        })

    # Step 5: 可选商品（配送）
    if product:
        prod_id = product.get("id", "")
        prod_name = product.get("name", "")
        prod_price = product.get("avg_price", 0)
        items.append({
            "start": current_time,
            "end": _add_minutes(current_time, 10),
            "type": "product",
            "resource_id": prod_id,
            "title": f"配送 {prod_name} 到 {rest_name}",
            "duration_minutes": 10,
            "price": prod_price,
        })
        total_price += prod_price
        highlights.append(f"包含{prod_name}")
        confirmations.append({
            "action": "place_order",
            "resource_id": prod_id,
            "resource_name": prod_name,
        })

    total_dur = _time_to_minutes(current_time) - _time_to_minutes(start_time)

    # 简单时间线校验
    time_window_end = time_window.get("end", "")
    if time_window_end and _time_to_minutes(current_time) > _time_to_minutes(time_window_end):
        tradeoffs.append(f"方案结束时间 {current_time} 略微超出窗口 {time_window_end}")

    return {
        "plan_id": f"plan_{plan_type}_{uuid.uuid4().hex[:6]}",
        "title": title,
        "plan_type": plan_type,
        "total_score": 0,  # 外部 compare_plans 填充
        "total_price": total_price,
        "total_duration_minutes": total_dur,
        "items": items,
        "highlights": list(dict.fromkeys(highlights))[:5],  # 去重保序
        "tradeoffs": list(dict.fromkeys(tradeoffs))[:5],
        "required_confirmations": confirmations,
    }


def _select_package(packages: list[dict], party: dict) -> Optional[dict]:
    """根据同行人选择最合适的套餐。"""
    children = party.get("children", 0)
    adults = party.get("adults", 1)

    for pkg in packages:
        name = pkg.get("name", "")
        if children > 0 and "亲子" in name:
            return pkg
    # 默认返回第一个
    return packages[0] if packages else None


def _deduplicate_plans(plans: list[dict]) -> list[dict]:
    """去除内容完全相同的方案。"""
    seen = set()
    result = []
    for p in plans:
        # 用 plan_type + items 的 resource_id 做指纹
        ids = tuple(
            item.get("resource_id", "") for item in p.get("items", [])
            if item.get("type") in ("activity", "restaurant", "product")
        )
        key = (p.get("plan_type", ""), ids)
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _empty_plans(constraints: dict) -> list[dict]:
    """当没有可用候选时返回空方案列表。"""
    return [{
        "plan_id": "plan_empty",
        "title": "未找到合适方案",
        "plan_type": "safe",
        "total_score": 0,
        "total_price": 0,
        "total_duration_minutes": 0,
        "items": [],
        "highlights": [],
        "tradeoffs": ["无满足约束的活动或餐厅"],
        "required_confirmations": [],
    }]


# ---- 时间线校验 ----

def validate_plan_timeline(plan: dict) -> dict:
    """
    校验方案时间线是否合理。

    返回：
        {"is_valid": bool, "issues": [str], "warnings": [str]}
    """
    items = plan.get("items", [])
    if not items:
        return {"is_valid": False, "issues": ["方案无内容"], "warnings": []}

    issues: list[str] = []
    warnings: list[str] = []

    prev_end: Optional[str] = None
    for i, entry in enumerate(items):
        start = entry.get("start", "")
        end = entry.get("end", "")

        # 检查时间顺序
        if start and end and _time_to_minutes(start) > _time_to_minutes(end):
            issues.append(f"第{i+1}项: 开始时间{start}晚于结束时间{end}")

        # 检查与前一项不重叠
        if prev_end and start and _time_to_minutes(prev_end) > _time_to_minutes(start):
            warnings.append(f"第{i+1}项与上一项时间重叠（{prev_end} > {start}）")

        prev_end = end

    # 总时长检查
    total_dur = plan.get("total_duration_minutes", 0)
    if total_dur > 480:  # 超过 8 小时
        warnings.append(f"方案总时长{total_dur}分钟（超过 8 小时）")

    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }

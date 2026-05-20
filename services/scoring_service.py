"""
评分服务：对候选活动、餐厅、商品和完整方案进行规则评分。
总分 100，按维度加权，返回分数 + 扣分/加分原因。
"""
from typing import Optional

from services.catalog_service import get_resource_detail
from services.feasibility_service import (
    check_availability,
    check_open_hours,
    check_weather_suitability,
)


# ---- 评分配置 ----

# 活动评分维度
ACTIVITY_DIMENSIONS = {
    "人群匹配": 20,
    "距离匹配": 15,
    "预算匹配": 15,
    "营业时间": 15,
    "库存可预约": 15,
    "天气适配": 10,
    "口碑评分": 5,
    "排队风险": 5,
}

# 餐厅评分维度
RESTAURANT_DIMENSIONS = {
    "人群匹配": 15,
    "饮食匹配": 15,
    "场景匹配": 10,
    "距离匹配": 15,
    "预算匹配": 15,
    "营业时间": 10,
    "桌位库存": 10,
    "口碑评分": 5,
    "排队风险": 5,
}


def _clamp_score(score: float) -> float:
    return max(0.0, min(100.0, round(score, 1)))


# ---- 活动评分 ----

def score_activity(activity_id: str, constraints: dict) -> dict:
    """
    对单个活动按约束条件评分。

    参数：
        activity_id: 活动 ID
        constraints: 用户约束字典，结构见各子函数
    返回：
        {"activity_id": str, "score": float, "reasons": [str], "penalties": [str]}

    当资源不存在时返回 score=0、penalties 包含原因。
    """
    item = get_resource_detail(activity_id)
    if not item:
        return {"activity_id": activity_id, "score": 0,
                "reasons": [], "penalties": ["活动不存在"]}

    reasons: list[str] = []
    penalties: list[str] = []
    total = 0.0

    party = constraints.get("party_profile", {})
    max_dist = constraints.get("max_distance_km")
    budget = constraints.get("budget_per_person")
    time_window = constraints.get("time_window", {})
    weather = constraints.get("weather", {})
    preferences = constraints.get("preferences", [])
    location = constraints.get("location", {})
    avoid_tags = constraints.get("avoid_tags", [])

    # 1. 人群匹配 (20)
    dim_score = _score_party_match(item, party)
    total += dim_score
    if dim_score < 20:
        penalties.append(f"人群匹配得分 {dim_score}/20")
    else:
        reasons.append("人群匹配度高")

    # 2. 距离匹配 (15)
    distance_km = item.get("_distance_km")
    dim_score = _score_distance(distance_km, max_dist)
    total += dim_score
    if dim_score < 15:
        if distance_km is not None:
            penalties.append(f"距离 {distance_km}km，得分 {dim_score}/15")
        else:
            reasons.append("距离信息可用")

    # 3. 预算匹配 (15)
    dim_score = _score_budget(item.get("avg_price", 0), budget)
    total += dim_score
    if dim_score < 15:
        penalties.append(f"价格 {item['avg_price']}元/人，得分 {dim_score}/15")
    else:
        reasons.append(f"价格 {item['avg_price']}元/人，预算内")

    # 4. 营业时间 (15)
    tw_dim = 0.0
    if time_window:
        arrival = time_window.get("start", "14:00")
        open_result = check_open_hours(activity_id, arrival)
        remaining = _calc_remaining_minutes(open_result.get("closes_at"), arrival)
        duration = item.get("duration_minutes", 60)
        if open_result.get("is_open"):
            if remaining is None or remaining >= duration:
                tw_dim = 15.0
                reasons.append("营业时间充足")
            elif remaining >= duration * 0.7:
                tw_dim = 10.0
                penalties.append(f"活动时长{duration}min，营业剩余{remaining}min较紧")
            else:
                tw_dim = 5.0
                penalties.append(f"活动时长{duration}min超过营业剩余{remaining}min")
        else:
            tw_dim = 0.0
            penalties.append(open_result.get("reason", "营业时间不匹配"))
    total += tw_dim

    # 5. 库存/可预约 (15)
    if time_window:
        date = time_window.get("date", "")
        avail_result = check_availability(activity_id, date, time_window.get("start", "14:00"))
        if avail_result.get("is_available"):
            total += 15.0
            reasons.append("有库存/可预约")
        else:
            # 有替代时段给 5 分
            alts = avail_result.get("alternatives", [])
            if alts:
                total += 5.0
                penalties.append(f"目标时间不可用，有 {len(alts)} 个替代时段")
            else:
                penalties.append(avail_result.get("reason", "目标时间不可用"))
    else:
        total += 15.0

    # 6. 天气适配 (10)
    weather_result = check_weather_suitability(activity_id, weather)
    if weather_result.get("is_suitable"):
        total += 10.0
        reasons.append("天气适配")
    else:
        penalties.append(weather_result.get("reason", "天气不适配"))

    # 7. 口碑评分 (5)
    rating = item.get("rating", 0)
    dim_score = rating / 5.0 * 5.0
    total += dim_score
    if rating >= 4.5:
        reasons.append(f"评分 {rating}")

    # 8. 排队风险 (5)
    queue_risk = item.get("queue_risk", "low")
    risk_scores = {"low": 5, "medium": 3, "high": 1}
    dim_score = risk_scores.get(queue_risk, 3)
    total += dim_score
    if queue_risk == "high":
        penalties.append(f"排队风险高（预计{item.get('queue_minutes_estimate', 0)}分钟）")

    # 偏好标签加分（额外，不算在维度内）
    if preferences:
        item_tags = set(item.get("tags", [])) | set(item.get("category", []))
        prefs_set = set(preferences)
        matched = item_tags & prefs_set
        if matched:
            reasons.append(f"偏好匹配: {', '.join(matched)}")

    # 避讳标签扣分
    if avoid_tags:
        item_tags = set(item.get("tags", [])) | set(item.get("category", []))
        avoid_set = set(avoid_tags)
        conflict = item_tags & avoid_set
        if conflict:
            total = max(0, total - 15)
            penalties.append(f"与避讳标签冲突: {', '.join(conflict)}")

    return {
        "activity_id": activity_id,
        "score": _clamp_score(total),
        "reasons": reasons,
        "penalties": penalties,
    }


def _score_party_match(item: dict, party: dict) -> float:
    """人群匹配评分，满分 20。"""
    if not party:
        return 20.0
    score = 20.0
    children = party.get("children", 0)
    child_age = party.get("child_age")
    adults = party.get("adults", 1)

    suitable = set(item.get("suitable_for", []))
    not_suitable = set(item.get("not_suitable_for", []))

    # 有儿童时必须适合 family 或 children
    if children > 0:
        if "family" not in suitable and not any("children" in s for s in suitable):
            score -= 10
        if "family" in not_suitable:
            score -= 10
        # 年龄检查
        if child_age is not None:
            age_matched = False
            for s in suitable:
                if "children_" in s:
                    parts = s.replace("children_", "").split("_")
                    try:
                        lo, hi = int(parts[0]), int(parts[1])
                        if lo <= child_age <= hi:
                            age_matched = True
                            break
                    except (ValueError, IndexError):
                        pass
            if not age_matched:
                score -= 5
    # 成人聚会
    if adults >= 3 and children == 0:
        if "friends" not in suitable and "friends_party" not in suitable:
            score -= 5
    # 双人约会
    if adults == 2 and children == 0:
        if "couple" in suitable or "quiet_date" in suitable:
            score = score  # 不扣分
        elif "quiet_date" in not_suitable:
            score -= 5

    return max(0, score)


def _score_distance(distance_km: Optional[float], max_dist: Optional[float]) -> float:
    """距离匹配评分，满分 15。"""
    if distance_km is None or max_dist is None:
        return 15.0
    if distance_km <= max_dist:
        return 15.0
    # 超出范围线性扣分，超出 2 倍以上 0 分
    excess = (distance_km - max_dist) / max_dist
    if excess >= 1.0:
        return 0.0
    return max(0, round(15.0 * (1 - excess), 1))


def _score_budget(price: float, budget: Optional[float]) -> float:
    """预算匹配评分，满分 15。"""
    if budget is None or price <= 0:
        return 15.0
    ratio = price / budget
    if ratio <= 1.0:
        return 15.0
    if ratio <= 1.2:
        return 12.0
    if ratio <= 1.5:
        return 8.0
    if ratio <= 2.0:
        return 4.0
    return 0.0


def _calc_remaining_minutes(closes_at: Optional[str], arrival: str) -> Optional[int]:
    """计算从 arrival 到打烊剩余多少分钟。"""
    if not closes_at:
        return None
    try:
        h1, m1 = int(arrival.split(":")[0]), int(arrival.split(":")[1])
        h2, m2 = int(closes_at.split(":")[0]), int(closes_at.split(":")[1])
        return (h2 * 60 + m2) - (h1 * 60 + m1)
    except (ValueError, IndexError):
        return None


# ---- 餐厅评分 ----

def score_restaurant(restaurant_id: str, constraints: dict) -> dict:
    """对单个餐厅按约束条件评分。"""
    item = get_resource_detail(restaurant_id)
    if not item:
        return {"restaurant_id": restaurant_id, "score": 0,
                "reasons": [], "penalties": ["餐厅不存在"]}

    reasons: list[str] = []
    penalties: list[str] = []
    total = 0.0

    party = constraints.get("party_profile", {})
    diet_prefs = constraints.get("diet_tags", [])
    scene_prefs = constraints.get("scene_tags", [])
    max_dist = constraints.get("max_distance_km")
    budget = constraints.get("budget_per_person")
    time_window = constraints.get("time_window", {})

    # 1. 人群匹配 (15)
    dim_score = _score_restaurant_party(item, party)
    total += dim_score
    if dim_score < 15:
        penalties.append(f"人群匹配得分 {dim_score}/15")
    else:
        reasons.append("人群匹配度高")

    # 2. 饮食匹配 (15)
    if diet_prefs:
        item_diets = set(item.get("diet_tags", []))
        matched = item_diets & set(diet_prefs)
        if matched:
            total += 15.0
            reasons.append(f"饮食匹配: {', '.join(matched)}")
        else:
            total += 5.0
            penalties.append(f"饮食偏好不匹配（期望: {', '.join(diet_prefs)}）")
    else:
        total += 15.0

    # 3. 场景匹配 (10)
    if scene_prefs:
        item_scenes = set(item.get("scene_tags", []))
        matched = item_scenes & set(scene_prefs)
        if matched:
            total += 10.0
            reasons.append(f"场景匹配: {', '.join(matched)}")
        else:
            penalties.append(f"场景不匹配（期望: {', '.join(scene_prefs)}）")
    else:
        total += 10.0

    # 4. 距离匹配 (15)
    distance_km = item.get("_distance_km")
    dim_score = _score_distance(distance_km, max_dist)
    total += dim_score
    if dim_score < 15 and distance_km is not None:
        penalties.append(f"距离 {distance_km}km")

    # 5. 预算匹配 (15)
    dim_score = _score_budget(item.get("avg_price", 0), budget)
    total += dim_score
    if dim_score < 15:
        penalties.append(f"人均 {item['avg_price']}元（预算{budget}元）")

    # 6. 营业时间 (10)
    if time_window:
        arrival = time_window.get("start", "16:00")
        open_result = check_open_hours(restaurant_id, arrival)
        if open_result.get("is_open"):
            total += 10.0
        else:
            penalties.append(open_result.get("reason", "营业时间不匹配"))
    else:
        total += 10.0

    # 7. 桌位库存 (10)
    if time_window:
        party_size = party.get("adults", 1) + party.get("children", 0)
        avail_result = check_availability(restaurant_id, time_window.get("date", ""),
                                          time_window.get("start", "16:00"), party_size)
        if avail_result.get("is_available"):
            total += 10.0
            reasons.append("目标时间有桌位")
        else:
            alts = avail_result.get("alternatives", [])
            if alts:
                total += 5.0
                penalties.append(f"目标时间无桌，有 {len(alts)} 个替代时段")
            else:
                penalties.append(avail_result.get("reason", "无可用桌位"))
    else:
        total += 10.0

    # 8. 口碑 (5)
    rating = item.get("rating", 0)
    total += rating / 5.0 * 5.0

    # 9. 排队 (5)
    queue_risk = item.get("queue_risk", "low")
    risk_scores = {"low": 5, "medium": 3, "high": 1}
    total += risk_scores.get(queue_risk, 3)
    if queue_risk == "high":
        penalties.append(f"排队风险高（预计{item.get('queue_minutes_estimate', 0)}分钟）")

    return {
        "restaurant_id": restaurant_id,
        "score": _clamp_score(total),
        "reasons": reasons,
        "penalties": penalties,
    }


def _score_restaurant_party(item: dict, party: dict) -> float:
    """餐厅人群匹配 (15分)。"""
    if not party:
        return 15.0
    score = 15.0
    children = party.get("children", 0)
    total_count = party.get("adults", 1) + children
    supported = item.get("party_size_supported", [])

    if total_count not in supported and not any(s >= total_count for s in supported):
        score -= 8
    if children > 0 and not item.get("has_child_chair"):
        score -= 5
    return max(0, score)


# ---- 商品评分 ----

def score_product(product_id: str, constraints: dict) -> dict:
    """对单个商品评分（简化版）。"""
    item = get_resource_detail(product_id)
    if not item:
        return {"product_id": product_id, "score": 0,
                "reasons": [], "penalties": ["商品不存在"]}

    reasons: list[str] = []
    penalties: list[str] = []
    total = 50.0  # 商品用 50 分制，因为维度比活动/餐厅少

    preference_tags = constraints.get("preferences", [])
    if preference_tags:
        item_tags = set(item.get("tags", []))
        matched = item_tags & set(preference_tags)
        if matched:
            total += 20.0
            reasons.append(f"标签匹配: {', '.join(matched)}")

    rating = item.get("rating", 0)
    total += rating / 5.0 * 15.0

    stock = item.get("stock", 0)
    if stock > 0:
        total += 15.0
        reasons.append(f"库存充足（{stock}件）")
    else:
        penalties.append("库存不足")

    return {
        "product_id": product_id,
        "score": _clamp_score(total),
        "reasons": reasons,
        "penalties": penalties,
    }


# ---- 方案评分 ----

def score_plan(plan: dict, constraints: dict) -> dict:
    """
    对完整方案评分。基于方案内各 item 的评分加权平均。

    方案结构（来自 planning_service）:
        {"plan_id": str, "items": [{...}]}

    返回：
        {"plan_id": str, "score": float, "reasons": [str], "penalties": [str]}
    """
    items = plan.get("items", [])
    if not items:
        return {"plan_id": plan.get("plan_id", ""), "score": 0,
                "reasons": [], "penalties": ["方案无内容"]}

    all_reasons: list[str] = []
    all_penalties: list[str] = []
    scores: list[float] = []

    for entry in items:
        entry_type = entry.get("type")
        rid = entry.get("resource_id")
        if not rid:
            continue

        if entry_type == "activity":
            result = score_activity(rid, constraints)
        elif entry_type == "restaurant":
            result = score_restaurant(rid, constraints)
        elif entry_type == "product":
            result = score_product(rid, constraints)
        else:
            continue

        scores.append(result.get("score", 0))
        # 收集原因（去重前缀）
        for r in result.get("reasons", []):
            if r not in all_reasons:
                all_reasons.append(r)
        for p in result.get("penalties", []):
            if p not in all_penalties:
                all_penalties.append(p)

    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    # 加分：时间合理性
    total_dur = plan.get("total_duration_minutes", 0)
    expected_dur = constraints.get("expected_duration_hours", 4) * 60
    # 方案时长越接近需求越好
    dur_bonus = 0.0
    if expected_dur > 0:
        ratio = min(total_dur / expected_dur, 2.0)
        if 0.8 <= ratio <= 1.2:
            dur_bonus = 5.0
            all_reasons.append(f"方案时长 {total_dur}min 符合预期")
        elif ratio < 0.8:
            all_penalties.append(f"方案时长 {total_dur}min 偏短")
        else:
            all_penalties.append(f"方案时长 {total_dur}min 偏长")

    final_score = _clamp_score(avg_score + dur_bonus)

    return {
        "plan_id": plan.get("plan_id", ""),
        "score": final_score,
        "reasons": all_reasons,
        "penalties": all_penalties,
    }


def compare_plans(plans: list[dict], constraints: dict) -> list[dict]:
    """对多个方案评分并排序，返回带分数的排序列表。"""
    scored = []
    for plan in plans:
        result = score_plan(plan, constraints)
        plan_copy = dict(plan)
        plan_copy["total_score"] = result["score"]
        plan_copy["score_reasons"] = result["reasons"]
        plan_copy["score_penalties"] = result["penalties"]
        scored.append(plan_copy)
    scored.sort(key=lambda p: p["total_score"], reverse=True)
    return scored

"""
数据目录服务：负责从本地 JSON 文件加载 Mock 数据，并提供搜索和详情查询。
"""
import json
import math
import os
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# 模块级缓存：首次加载后常驻内存
_cache: dict[str, list[dict]] = {}


def _load_json(filename: str) -> list[dict]:
    """加载 JSON 数据文件，带内存缓存。"""
    if filename not in _cache:
        path = os.path.join(_DATA_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            _cache[filename] = json.load(f)
    return _cache[filename]


# ---- 距离计算 ----

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """计算两点间球面距离（km）。"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _filter_by_radius(items: list[dict], center_lat: float, center_lng: float,
                       radius_km: Optional[float]) -> list[dict]:
    """按距离过滤，返回在范围内的结果（附带计算距离）。"""
    if radius_km is None:
        return items
    result = []
    for item in items:
        dist = _haversine_km(center_lat, center_lng, item["lat"], item["lng"])
        if dist <= radius_km:
            item_copy = dict(item)
            item_copy["_distance_km"] = round(dist, 1)
            result.append(item_copy)
    return result


# ---- 资源详情 ----

def get_resource_detail(resource_id: str) -> Optional[dict]:
    """根据 ID 前缀查找资源：act_ → activities, rest_ → restaurants, prod_ → products。"""
    if resource_id.startswith("act_"):
        items = _load_json("activities.json")
    elif resource_id.startswith("rest_"):
        items = _load_json("restaurants.json")
    elif resource_id.startswith("prod_"):
        items = _load_json("products.json")
    else:
        return None
    for item in items:
        if item["id"] == resource_id:
            return item
    return None


# ---- 活动搜索 ----

def search_activities(
    *,
    tags: Optional[list[str]] = None,
    location: Optional[dict] = None,
    radius_km: Optional[float] = None,
    time_window: Optional[dict] = None,
    party_profile: Optional[dict] = None,
) -> list[dict]:
    """
    搜索符合条件的活动。

    参数：
        tags: 偏好标签，如 ["亲子", "室内"]
        location: {"lat": float, "lng": float}
        radius_km: 搜索半径
        time_window: {"date": "2026-05-23", "start": "14:00", "end": "18:00"}
        party_profile: {"adults": 2, "children": 1, "child_age": 5}
    返回：
        匹配的活动列表，附带 _distance_km 和 _match_reasons（匹配原因）。
    """
    items = _load_json("activities.json")
    lat = location.get("lat") if location else None
    lng = location.get("lng") if location else None

    # 距离过滤
    if lat is not None and lng is not None and radius_km is not None:
        items = _filter_by_radius(items, lat, lng, radius_km)

    results = []
    for item in items:
        match_reasons = []
        exclude_reasons = []

        # 标签匹配
        if tags:
            item_tags = set(item.get("tags", []))
            item_cats = set(item.get("category", []))
            all_item_tags = item_tags | item_cats
            matched_tags = [t for t in tags if t in all_item_tags]
            if matched_tags:
                match_reasons.append(f"匹配标签: {', '.join(matched_tags)}")

        # 人群匹配：suitable_for
        if party_profile:
            children = party_profile.get("children", 0)
            child_age = party_profile.get("child_age")
            suitable = set(item.get("suitable_for", []))
            not_suitable = set(item.get("not_suitable_for", []))

            if children > 0:
                if "family" in suitable:
                    match_reasons.append("适合家庭")
                # 儿童年龄匹配
                if child_age is not None:
                    # 从 suitable_for 中检测年龄段
                    for s in suitable:
                        if "children_" in s:
                            parts = s.replace("children_", "").split("_")
                            try:
                                lo, hi = int(parts[0]), int(parts[1])
                                if lo <= child_age <= hi:
                                    match_reasons.append(f"适合{child_age}岁儿童")
                            except (ValueError, IndexError):
                                pass
                if "family" in not_suitable or any("children" in ns for ns in not_suitable):
                    exclude_reasons.append("不适合带儿童")

            adults = party_profile.get("adults", 1)
            if adults == 2 and children == 0:
                if "couple" in suitable or "quiet_date" in suitable:
                    match_reasons.append("适合情侣/双人")
            if adults >= 3:
                if "friends" in suitable or "friends_party" in suitable:
                    match_reasons.append("适合多人聚会")

        # 开放时间匹配
        if time_window:
            date_str = time_window.get("date", "")
            weekday = _guess_weekday(date_str)
            open_hours = item.get("open_hours", {})
            if weekday and weekday in open_hours:
                match_reasons.append(f"{weekday}正常营业（{open_hours[weekday]}）")

        item_copy = dict(item)
        item_copy["_match_reasons"] = match_reasons
        item_copy["_exclude_reasons"] = exclude_reasons

        # 只有有匹配原因且没有排除原因的才放入结果
        # 如果没指定过滤条件，则全部返回
        has_filters = bool(tags or party_profile or time_window)
        if not has_filters or (match_reasons and not exclude_reasons):
            results.append(item_copy)

    return results


# ---- 餐厅搜索 ----

def search_restaurants(
    *,
    diet_tags: Optional[list[str]] = None,
    scene_tags: Optional[list[str]] = None,
    location: Optional[dict] = None,
    radius_km: Optional[float] = None,
    party_size: Optional[int] = None,
    time_window: Optional[dict] = None,
) -> list[dict]:
    """
    搜索符合条件的餐厅。

    参数：
        diet_tags: 饮食标签，如 ["低卡", "儿童餐"]
        scene_tags: 场景标签，如 ["家庭", "安静"]
        location: {"lat": float, "lng": float}
        radius_km: 搜索半径
        party_size: 用餐人数
        time_window: {"date": "2026-05-23", "start": "14:00", "end": "18:00"}
    返回：
        匹配的餐厅列表，附带 _distance_km 和 _match_reasons。
    """
    items = _load_json("restaurants.json")
    lat = location.get("lat") if location else None
    lng = location.get("lng") if location else None

    if lat is not None and lng is not None and radius_km is not None:
        items = _filter_by_radius(items, lat, lng, radius_km)

    results = []
    for item in items:
        match_reasons = []
        exclude_reasons = []

        # 饮食标签匹配
        if diet_tags:
            item_diets = set(item.get("diet_tags", []))
            matched = item_diets & set(diet_tags)
            if matched:
                match_reasons.append(f"饮食匹配: {', '.join(matched)}")

        # 场景标签匹配
        if scene_tags:
            item_scenes = set(item.get("scene_tags", []))
            matched = item_scenes & set(scene_tags)
            if matched:
                match_reasons.append(f"场景匹配: {', '.join(matched)}")

        # 人数匹配
        if party_size is not None:
            supported = item.get("party_size_supported", [])
            if party_size in supported:
                match_reasons.append(f"支持{party_size}人用餐")
            elif any(s >= party_size for s in supported):
                match_reasons.append(f"可容纳{party_size}人")
            else:
                exclude_reasons.append(f"不支持{party_size}人用餐")

            # 儿童友好
            if not item.get("has_child_chair") and party_size >= 3:
                exclude_reasons.append("无儿童椅")

        item_copy = dict(item)
        item_copy["_match_reasons"] = match_reasons
        item_copy["_exclude_reasons"] = exclude_reasons

        has_filters = bool(diet_tags or scene_tags or party_size)
        if not has_filters or (match_reasons and not exclude_reasons):
            results.append(item_copy)

    return results


# ---- 商品搜索 ----

def search_products(
    *,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    location: Optional[dict] = None,
    radius_km: Optional[float] = None,
    delivery_time: Optional[str] = None,
) -> list[dict]:
    """
    搜索商品（蛋糕、鲜花等增量消费）。

    参数：
        category: 商品类别，如 "蛋糕"、"鲜花"
        tags: 商品标签
        location: {"lat": float, "lng": float}
        radius_km: 搜索半径
        delivery_time: 期望送达时间字符串
    返回：
        匹配的商品列表，附带 _match_reasons。
    """
    items = _load_json("products.json")
    lat = location.get("lat") if location else None
    lng = location.get("lng") if location else None

    if lat is not None and lng is not None and radius_km is not None:
        items = _filter_by_radius(items, lat, lng, radius_km)

    results = []
    for item in items:
        match_reasons = []

        if category and item.get("category") == category:
            match_reasons.append(f"匹配类别: {category}")
        if tags:
            item_tags = set(item.get("tags", []))
            matched = item_tags & set(tags)
            if matched:
                match_reasons.append(f"匹配标签: {', '.join(matched)}")

        item_copy = dict(item)
        item_copy["_match_reasons"] = match_reasons

        has_filters = bool(category or tags)
        if not has_filters or match_reasons:
            results.append(item_copy)

    return results


# ---- 工具函数 ----

def _guess_weekday(date_str: str) -> Optional[str]:
    """根据日期字符串推算星期，用于匹配 open_hours 的 key。"""
    import datetime
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        weekday = dt.weekday()  # 0=Monday
        day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        return day_keys[weekday]
    except ValueError:
        return None

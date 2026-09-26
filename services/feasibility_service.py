"""
可行性校验服务：对候选资源和方案进行现实约束校验。
包括营业时间、库存/桌位、路线估算、排队预估、天气适配。
"""
import math
from typing import Optional

from services.catalog_service import get_resource_detail


# ---- 营业时间校验 ----

def check_open_hours(resource_id: str, arrival_time: str) -> dict:
    """
    校验资源在 arrival_time 是否营业。

    参数：
        resource_id: 资源 ID
        arrival_time: 到达时间，格式 "14:30"
    返回：
        {
            "resource_id": str,
            "resource_name": str,
            "arrival_time": str,
            "is_open": bool,
            "open_time": str | None,    # 当天营业时段
            "closes_at": str | None,    # 打烊时间
            "reason": str
        }
    """
    item = get_resource_detail(resource_id)
    if not item:
        return {"resource_id": resource_id, "arrival_time": arrival_time,
                "is_open": False, "reason": "资源不存在"}

    open_hours = item.get("open_hours", {})
    # 默认取 sat 时段；若调用方传入具体 weekday 可优先匹配
    today_key = "sat"  # 简化处理：Demo 默认周末
    hours_str = open_hours.get(today_key)
    if not hours_str:
        # 取第一个可用的营业时间
        hours_str = next(iter(open_hours.values()), None)
        if not hours_str:
            return {
                "resource_id": resource_id,
                "resource_name": item["name"],
                "arrival_time": arrival_time,
                "is_open": False,
                "reason": f"{item['name']}无营业时间数据",
            }

    open_t, close_t = hours_str.split("-")
    if open_t <= arrival_time < close_t:
        return {
            "resource_id": resource_id,
            "resource_name": item["name"],
            "arrival_time": arrival_time,
            "is_open": True,
            "open_time": hours_str,
            "closes_at": close_t,
            "reason": f"{item['name']}营业中（{hours_str}），{arrival_time}到达正常",
        }
    else:
        return {
            "resource_id": resource_id,
            "resource_name": item["name"],
            "arrival_time": arrival_time,
            "is_open": False,
            "open_time": hours_str,
            "closes_at": close_t,
            "reason": (
                f"{item['name']}当日营业{hours_str}，{arrival_time}"
                f"{'尚未开门' if arrival_time < open_t else '已打烊'}"
            ),
        }


# ---- 库存 / 桌位校验 ----

def check_availability(
    resource_id: str,
    date: str,
    time: str,
    party_size: Optional[int] = None,
) -> dict:
    """
    校验资源在指定 date + time 是否有库存/桌位。

    参数：
        resource_id: 资源 ID
        date: 日期，如 "2026-05-23"
        time: 时间，如 "14:30"
        party_size: 人数（餐厅需要）
    返回：
        {
            "resource_id": str,
            "resource_name": str,
            "date": str, "time": str,
            "is_available": bool,
            "available_slots": int | None,
            "alternatives": [{"date": str, "time": str, "status": str}],
            "reason": str
        }
    """
    item = get_resource_detail(resource_id)
    if not item:
        return {"resource_id": resource_id, "is_available": False, "reason": "资源不存在"}

    resource_type = item.get("type", "")

    # 活动类：查 availability
    avail_list = item.get("availability", [])
    if not avail_list and item.get("booking_required") is False:
        # 不需要预约的活动（如公园），默认可用
        return {
            "resource_id": resource_id,
            "resource_name": item["name"],
            "date": date, "time": time,
            "is_available": True,
            "reason": f"{item['name']}无需预约，随时可去",
        }

    # 餐厅类：查 available_tables
    if resource_type == "restaurant":
        avail_list = item.get("available_tables", [])

    # 商品类：查 delivery_slots
    if resource_type == "product":
        avail_list = item.get("delivery_slots", [])

    # 精确匹配
    for slot in avail_list:
        slot_date = slot.get("date", "")
        slot_time = slot.get("time", "")
        if slot_date == date and slot_time == time:
            if party_size is not None and resource_type == "restaurant":
                slot_ps = slot.get("party_size")
                if slot_ps is not None and slot_ps < party_size:
                    continue
            status = slot.get("status", "unknown")
            is_avail = status in ("available", "few_left")
            return {
                "resource_id": resource_id,
                "resource_name": item["name"],
                "date": date, "time": time,
                "is_available": is_avail,
                "available_slots": slot.get("slots") or slot.get("remaining"),
                "reason": (
                    f"{item['name']} {date} {time} "
                    f"{'有库存' if is_avail else '已售罄'}（{status}）"
                ),
            }

    # 未精确匹配：收集 alternatives
    alternatives = [
        {"date": s.get("date", ""), "time": s.get("time", ""),
         "status": s.get("status", "unknown")}
        for s in avail_list if s.get("status") in ("available", "few_left")
    ]
    return {
        "resource_id": resource_id,
        "resource_name": item["name"],
        "date": date, "time": time,
        "is_available": False,
        "alternatives": alternatives[:5],
        "reason": (
            f"{item['name']} {date} {time} 不可用"
            + (f"，可选: {', '.join(a['time'] for a in alternatives[:3])}" if alternatives else "，无替代时段")
        ),
    }


# ---- 路线估算 ----

# 出行方式到速度（km/h）的映射
_SPEED_KMH = {
    "walk": 5.0,
    "bike": 12.0,
    "taxi": 25.0,
    "transit": 18.0,
}


def estimate_route(
    origin: dict,
    destination: dict,
    mode: str = "taxi",
) -> dict:
    """
    估算两点间路程和时间。

    参数：
        origin: {"lat": float, "lng": float, "address": str}
        destination: {"lat": float, "lng": float, "address": str}
        mode: 出行方式 (walk / bike / taxi / transit)
    返回：
        {
            "distance_km": float,
            "duration_minutes": int,
            "mode": str,
            "from": str,
            "to": str
        }
    """
    lat1, lng1 = origin["lat"], origin["lng"]
    lat2, lng2 = destination["lat"], destination["lng"]

    # Haversine 距离
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    speed = _SPEED_KMH.get(mode, 25.0)
    duration_min = max(5, round(dist_km / speed * 60))

    return {
        "distance_km": round(dist_km, 1),
        "duration_minutes": duration_min,
        "mode": mode,
        "from": origin.get("address", "起点"),
        "to": destination.get("address", "终点"),
    }


# ---- 排队预估 ----

def estimate_queue_time(resource_id: str, date: str = "", time: str = "") -> dict:
    """
    预估某个资源的排队等候时间。

    参数：
        resource_id: 资源 ID
        date: 日期
        time: 时间
    返回：
        {
            "resource_id": str,
            "resource_name": str,
            "queue_risk": "low" | "medium" | "high",
            "estimated_minutes": int,
            "reason": str
        }
    """
    item = get_resource_detail(resource_id)
    if not item:
        return {"resource_id": resource_id, "queue_risk": "unknown",
                "estimated_minutes": 0, "reason": "资源不存在"}

    risk = item.get("queue_risk", "low")
    estimate = item.get("queue_minutes_estimate", 0)

    risk_labels = {
        "low": "排队风险低",
        "medium": "排队风险中等",
        "high": "排队风险高",
    }
    return {
        "resource_id": resource_id,
        "resource_name": item["name"],
        "queue_risk": risk,
        "estimated_minutes": estimate,
        "reason": f"{item['name']}: {risk_labels.get(risk, risk)}，预计排队{estimate}分钟",
    }


# ---- 天气适配 ----

def check_weather_suitability(activity_id: str, weather: dict) -> dict:
    """
    检查活动是否适合当前天气。

    参数：
        activity_id: 活动 ID
        weather: {"weather": "晴朗"|"雨天"|..., "temperature": str}
    返回：
        {
            "activity_id": str,
            "activity_name": str,
            "is_suitable": bool,
            "weather_sensitive": bool,
            "reason": str
        }
    """
    item = get_resource_detail(activity_id)
    if not item:
        return {"activity_id": activity_id, "is_suitable": True, "reason": "资源不存在"}

    sensitive = item.get("weather_sensitive", False)
    weather_cond = weather.get("weather", "")

    if not sensitive:
        return {
            "activity_id": activity_id,
            "activity_name": item["name"],
            "is_suitable": True,
            "weather_sensitive": False,
            "reason": f"{item['name']}不受天气影响",
        }

    # 户外活动：判断是否适合
    bad_weather = any(w in weather_cond for w in ["雨", "雪", "大风", "沙尘", "高温"])
    if bad_weather:
        return {
            "activity_id": activity_id,
            "activity_name": item["name"],
            "is_suitable": False,
            "weather_sensitive": True,
            "reason": f"{item['name']}是户外活动，{weather_cond}天气不建议前往",
        }
    else:
        return {
            "activity_id": activity_id,
            "activity_name": item["name"],
            "is_suitable": True,
            "weather_sensitive": True,
            "reason": f"{item['name']}是户外活动，当前{weather_cond}，适合出行",
        }

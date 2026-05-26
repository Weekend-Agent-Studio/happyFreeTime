"""
工具层：将 services/ 中的业务逻辑包装为 LangChain 可调用工具。
供 Agent（intent / slot / planning）通过 tool calling 调用。

工具分类：
  - 基础信息：时间、位置、天气
  - 资源搜索：活动、餐厅、商品
  - 可行性校验：营业时间、库存、路线、排队、天气适配
  - 方案生成：生成候选方案、对比方案
  - 执行动作：订票、预约餐厅、下单、取消（需用户确认后调用）
"""
import json
from datetime import datetime
from typing import Optional

import asyncio

from langchain_core.tools import tool

# ---- 基础信息工具 ----

@tool
def get_cur_time() -> dict:
    """获取当前日期、时间、星期，用于确定出行时间窗口。"""
    now = datetime.now()
    week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "weekday": week_list[now.weekday()],
        "hour": now.hour,
        "minute": now.minute,
        "date_iso": now.strftime("%Y-%m-%d"),
    }


@tool
def get_cur_loc() -> dict:
    """获取当前默认地理位置（Demo 版固定北京朝阳区国贸附近）。"""
    return {
        "address": "北京市朝阳区建国路88号",
        "lat": 39.9087,
        "lng": 116.4713,
        "district": "朝阳区",
        "nearby": ["国贸", "双井", "大望路"],
    }


@tool
def get_weather(latitude: str, longitude: str) -> str:
    """查询指定经纬度的实时天气。latitude是纬度如39.9087，longitude是经度如116.4713。"""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    async def _call():
        server_params = StdioServerParameters(
            command="python",
            args=["MCP/mcp_server.py"],
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_weather", {
                    "latitude": latitude,
                    "longitude": longitude,
                })
                return result.content[0].text

    return asyncio.run(_call())


# ---- 资源搜索工具 ----

@tool
def search_activities(
    loc: dict,
    tags: Optional[list[str]] = None,
    radius_km: Optional[float] = None,
    date: str = "",
    time_start: str = "",
    time_end: str = "",
    adults: int = 1,
    children: int = 0,
    child_age: Optional[int] = None,
) -> list[dict]:
    """
    搜索符合条件的活动（亲子乐园、展览、运动、密室等）。

    参数：
        loc: 位置字典（来自 get_cur_loc）
        tags: 偏好标签列表，如 ["亲子", "室内", "低体力消耗"]
        radius_km: 搜索半径(km)，不传则不限制距离
        date: 出行日期 "2026-05-23"
        time_start: 时间窗口起点 "14:00"
        time_end: 时间窗口终点 "18:00"
        adults: 成人数
        children: 儿童数
        child_age: 儿童年龄
    返回：
        匹配的活动列表，含 _match_reasons 和 _distance_km
    """
    from services.catalog_service import search_activities as _search

    time_window = None
    if date or time_start or time_end:
        time_window = {"date": date, "start": time_start or "14:00", "end": time_end or "18:00"}

    party = {
        "adults": adults, "children": children,
        "child_age": child_age,
    }

    results = _search(
        tags=tags,
        location=loc,
        radius_km=radius_km,
        time_window=time_window,
        party_profile=party,
    )
    # 移除内部字段以减少输出噪音
    for r in results:
        r.pop("_exclude_reasons", None)
    return results


@tool
def search_restaurants(
    loc: dict,
    diet_tags: Optional[list[str]] = None,
    scene_tags: Optional[list[str]] = None,
    radius_km: Optional[float] = None,
    party_size: Optional[int] = None,
    date: str = "",
    time_start: str = "",
    time_end: str = "",
) -> list[dict]:
    """
    搜索符合条件的餐厅。

    参数：
        loc: 位置字典
        diet_tags: 饮食偏好，如 ["低卡", "儿童餐", "高蛋白"]
        scene_tags: 场景偏好，如 ["家庭", "安静", "约会", "朋友聚餐"]
        radius_km: 搜索半径(km)
        party_size: 用餐总人数
        date: 出行日期
        time_start / time_end: 时间窗口
    返回：
        匹配的餐厅列表，含 _match_reasons 和 _distance_km
    """
    from services.catalog_service import search_restaurants as _search

    time_window = None
    if date or time_start or time_end:
        time_window = {"date": date, "start": time_start or "14:00", "end": time_end or "18:00"}

    results = _search(
        diet_tags=diet_tags,
        scene_tags=scene_tags,
        location=loc,
        radius_km=radius_km,
        party_size=party_size,
        time_window=time_window,
    )
    for r in results:
        r.pop("_exclude_reasons", None)
    return results


@tool
def search_products(
    loc: dict,
    category: str = "",
    tags: Optional[list[str]] = None,
    radius_km: Optional[float] = None,
) -> list[dict]:
    """
    搜索增量消费商品，如蛋糕、鲜花、甜品。

    参数：
        loc: 位置字典
        category: 商品类别，可选 "蛋糕"、"鲜花"
        tags: 商品标签，如 ["低糖", "可配送", "约会"]
        radius_km: 搜索半径(km)
    """
    from services.catalog_service import search_products as _search

    return _search(
        category=category or None,
        tags=tags,
        location=loc,
        radius_km=radius_km,
    )


@tool
def get_resource_detail(resource_id: str) -> Optional[dict]:
    """
    根据资源 ID 获取详细信息（活动、餐厅、商品的完整数据）。

    参数：
        resource_id: 资源 ID，如 "act_001"、"rest_001"、"prod_001"
    """
    from services.catalog_service import get_resource_detail as _get

    return _get(resource_id)


# ---- 可行性校验工具 ----

@tool
def check_open_hours(resource_id: str, arrival_time: str) -> dict:
    """
    校验资源在到达时间是否营业。

    参数：
        resource_id: 资源 ID
        arrival_time: 到达时间 "14:30"
    返回：
        {"is_open": bool, "open_time": str, "closes_at": str, "reason": str}
    """
    from services.feasibility_service import check_open_hours as _check

    return _check(resource_id, arrival_time)


@tool
def check_availability(
    resource_id: str,
    date: str,
    time: str,
    party_size: Optional[int] = None,
) -> dict:
    """
    校验资源在指定日期时间是否有库存/桌位。

    参数：
        resource_id: 资源 ID
        date: 日期 "2026-05-23"
        time: 时间 "14:30"
        party_size: 人数（餐厅需要）
    返回：
        {"is_available": bool, "reason": str, "alternatives": [...]}
    """
    from services.feasibility_service import check_availability as _check

    return _check(resource_id, date, time, party_size)


@tool
def estimate_route(
    origin: dict,
    destination: dict,
    mode: str = "taxi",
) -> dict:
    """
    估算两点间路程距离和耗时。

    参数：
        origin: {"lat": float, "lng": float, "address": str}
        destination: {"lat": float, "lng": float, "address": str}
        mode: 出行方式，可选 "walk"、"bike"、"taxi"、"transit"
    返回：
        {"distance_km": float, "duration_minutes": int, "mode": str}
    """
    from services.feasibility_service import estimate_route as _est

    return _est(origin, destination, mode)


@tool
def estimate_queue_time(resource_id: str, date: str = "", time: str = "") -> dict:
    """
    预估资源的排队等候时间。

    参数：
        resource_id: 资源 ID
        date: 日期
        time: 时间
    返回：
        {"queue_risk": "low"|"medium"|"high", "estimated_minutes": int, "reason": str}
    """
    from services.feasibility_service import estimate_queue_time as _est

    return _est(resource_id, date, time)


@tool
def check_weather_suitability(activity_id: str, weather: dict) -> dict:
    """
    检查活动是否适合当前天气（户外活动在雨天不建议）。

    参数：
        activity_id: 活动 ID
        weather: get_cur_weather 返回的天气字典
    返回：
        {"is_suitable": bool, "weather_sensitive": bool, "reason": str}
    """
    from services.feasibility_service import check_weather_suitability as _check

    return _check(activity_id, weather)


# ---- 方案生成工具 ----

@tool
def generate_candidate_plans(constraints_json: str) -> list[dict]:
    """
    根据结构化约束生成 2-3 个候选出行方案（safe / budget / rich）。

    参数：
        constraints_json: JSON 字符串，包含以下字段：
            - location: {"lat": float, "lng": float, "address": str}
            - time_window: {"date": "2026-05-23", "start": "14:00", "end": "18:00"}
            - party_profile: {"adults": 2, "children": 1, "child_age": 5}
            - max_distance_km: int
            - budget_per_person: int
            - preferences: [str]    活动偏好标签
            - diet_tags: [str]      饮食偏好标签
            - scene_tags: [str]     场景偏好标签
    返回：
        候选方案列表，每个方案含 items（时间线）、highlights、tradeoffs、required_confirmations
    """
    from services.planning_service import generate_candidate_plans as _gen
    from services.scoring_service import compare_plans

    constraints = json.loads(constraints_json)
    plans = _gen(constraints)
    return compare_plans(plans, constraints)


@tool
def validate_plan_timeline(plan_json: str) -> dict:
    """
    校验方案时间线是否合理（无重叠、时长合理）。

    参数：
        plan_json: 方案 JSON 字符串
    返回：
        {"is_valid": bool, "issues": [str], "warnings": [str]}
    """
    from services.planning_service import validate_plan_timeline as _validate

    plan = json.loads(plan_json)
    return _validate(plan)


# ---- 执行动作工具（需用户确认后调用） ----

@tool
def book_tickets(
    activity_id: str,
    date: str,
    time: str,
    count: int = 1,
) -> dict:
    """
    预订活动门票。仅在用户明确确认后调用。

    参数：
        activity_id: 活动 ID，如 "act_001"
        date: 日期 "2026-05-23"
        time: 时间 "14:30"
        count: 票数
    返回：
        {"success": bool, "confirmation_id": str, "total_price": int, "message": str}
    """
    from services.order_service import book_tickets as _book

    return _book(activity_id, date, time, count)


@tool
def reserve_restaurant(
    restaurant_id: str,
    date: str,
    time: str,
    party_size: int,
    requirements: str = "",
) -> dict:
    """
    预订餐厅桌位。仅在用户明确确认后调用。

    参数：
        restaurant_id: 餐厅 ID，如 "rest_001"
        date: 日期
        time: 时间
        party_size: 用餐人数
        requirements: 特殊要求（如 "需要儿童椅"）
    返回：
        {"success": bool, "confirmation_id": str, "message": str}
    """
    from services.order_service import reserve_restaurant as _reserve

    return _reserve(restaurant_id, date, time, party_size, requirements or None)


@tool
def place_order(
    product_id: str,
    quantity: int,
    delivery_address: str,
    delivery_time: str,
) -> dict:
    """
    下单购买商品（蛋糕、鲜花等）并配送。仅在用户明确确认后调用。

    参数：
        product_id: 商品 ID，如 "prod_001"
        quantity: 数量
        delivery_address: 配送地址
        delivery_time: 期望送达时间
    返回：
        {"success": bool, "confirmation_id": str, "total_price": int, "message": str}
    """
    from services.order_service import place_order as _order

    return _order(product_id, quantity, delivery_address, delivery_time)


@tool
def cancel_booking(confirmation_id: str) -> dict:
    """
    取消已执行的订单/预约。

    参数：
        confirmation_id: 订单确认号
    返回：
        {"success": bool, "message": str}
    """
    from services.order_service import cancel_booking as _cancel

    return _cancel(confirmation_id)

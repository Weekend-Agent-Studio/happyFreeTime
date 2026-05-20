"""
订单执行服务：模拟预约、订票、下单等交易动作。
内存中维护库存计数器，支持成功/失败/替代时段返回值。
"""
import datetime
import uuid
from typing import Optional

from services.catalog_service import get_resource_detail

# ---- 内存状态 ----

# 已执行订单记录（模拟数据库）
_orders: dict[str, dict] = {}

# 库存计数器：{resource_id: {slot_key: remaining_count}}
# slot_key 格式："date|time" 或 "date|time|party_size"
_inventory: dict[str, dict[str, int]] = {}


def _slot_key(date: str, time: str, party_size: Optional[int] = None) -> str:
    """生成库存查询 key。"""
    if party_size is not None:
        return f"{date}|{time}|p{party_size}"
    return f"{date}|{time}"


def _init_inventory(resource_id: str) -> None:
    """从资源数据初始化内存库存（仅首次）。"""
    if resource_id in _inventory:
        return
    item = get_resource_detail(resource_id)
    if not item:
        return
    _inventory[resource_id] = {}

    resource_type = item.get("type", "")
    if resource_type == "restaurant":
        slots = item.get("available_tables", [])
    elif resource_type == "product":
        # 商品用总库存
        _inventory[resource_id]["_total"] = item.get("stock", 0)
        return
    else:
        slots = item.get("availability", [])

    for s in slots:
        date = s.get("date", "")
        time = s.get("time", "")
        ps = s.get("party_size")
        key = _slot_key(date, time, ps)
        count = s.get("slots") or s.get("remaining", 0)
        _inventory[resource_id][key] = count


def _get_inventory(resource_id: str, date: str, time: str,
                   party_size: Optional[int] = None) -> int:
    """查询剩余库存。"""
    _init_inventory(resource_id)
    inv = _inventory.get(resource_id, {})

    # 商品类
    if "_total" in inv:
        return inv["_total"]

    key = _slot_key(date, time, party_size)
    return inv.get(key, 0)


def _reduce_inventory(resource_id: str, date: str, time: str,
                      party_size: Optional[int] = None, count: int = 1) -> bool:
    """扣减库存，返回是否成功。"""
    _init_inventory(resource_id)
    inv = _inventory.get(resource_id, {})
    if not inv:
        return False

    # 商品类
    if "_total" in inv:
        if inv["_total"] >= count:
            inv["_total"] -= count
            return True
        return False

    key = _slot_key(date, time, party_size)
    if inv.get(key, 0) >= count:
        inv[key] -= count
        return True
    return False


# ---- 执行动作 ----

def _gen_confirmation_id(prefix: str) -> str:
    """生成确认号，如 TK20260523-A1B2C3。"""
    today = datetime.date.today().strftime("%Y%m%d")
    short_uid = uuid.uuid4().hex[:6].upper()
    return f"{prefix}{today}-{short_uid}"


def book_tickets(
    activity_id: str,
    date: str,
    time: str,
    count: int = 1,
    package_id: Optional[str] = None,
) -> dict:
    """
    预订活动门票。

    返回：
        成功: {"success": true, "confirmation_id": str, "total_price": int, ...}
        失败: {"success": false, "error_code": str, "message": str,
               "alternatives": [{"time": str, "status": str}]}
    """
    item = get_resource_detail(activity_id)
    if not item:
        return {"success": False, "action_type": "book_tickets",
                "error_code": "NOT_FOUND", "message": "活动不存在"}

    # 库存检查
    avail = _get_inventory(activity_id, date, time)
    if avail <= 0:
        # 找替代时段
        alternatives = _find_alternatives(activity_id, date)
        return {
            "success": False,
            "action_type": "book_tickets",
            "target_id": activity_id,
            "target_name": item["name"],
            "error_code": "SOLD_OUT",
            "message": f"{item['name']} {date} {time} 已售罄，请选择其他时段",
            "alternatives": alternatives,
        }

    # 计算价格
    packages = item.get("packages", [])
    total_price = 0
    selected_pkg = None
    if package_id and packages:
        for p in packages:
            if p["id"] == package_id:
                selected_pkg = p
                total_price = p["price"] * count
                break
    if not selected_pkg:
        total_price = item.get("avg_price", 0) * count

    # 扣库存
    if not _reduce_inventory(activity_id, date, time, count=count):
        return {
            "success": False,
            "action_type": "book_tickets",
            "target_id": activity_id,
            "target_name": item["name"],
            "error_code": "INSUFFICIENT_STOCK",
            "message": f"库存不足，剩余 {avail} 张",
            "alternatives": _find_alternatives(activity_id, date),
        }

    conf_id = _gen_confirmation_id("TK")
    result = {
        "success": True,
        "action_type": "book_tickets",
        "confirmation_id": conf_id,
        "target_id": activity_id,
        "target_name": item["name"],
        "scheduled_time": f"{date} {time}",
        "count": count,
        "total_price": total_price,
        "message": f"订票成功！{item['name']} {date} {time}，共{count}张，请提前10分钟到场。",
    }
    _orders[conf_id] = result
    return result


def reserve_restaurant(
    restaurant_id: str,
    date: str,
    time: str,
    party_size: int,
    requirements: Optional[str] = None,
) -> dict:
    """
    预订餐厅桌位。

    返回：
        成功/失败结构同上，error_code 可能为 NO_TABLE。
    """
    item = get_resource_detail(restaurant_id)
    if not item:
        return {"success": False, "action_type": "reserve_restaurant",
                "error_code": "NOT_FOUND", "message": "餐厅不存在"}

    # 桌位检查
    avail = _get_inventory(restaurant_id, date, time, party_size)
    if avail <= 0:
        alternatives = _find_alternatives(restaurant_id, date, party_size)
        return {
            "success": False,
            "action_type": "reserve_restaurant",
            "target_id": restaurant_id,
            "target_name": item["name"],
            "error_code": "NO_TABLE",
            "message": f"{item['name']} {date} {time} 无{party_size}人桌位",
            "alternatives": alternatives,
        }

    if not _reduce_inventory(restaurant_id, date, time, party_size):
        return {
            "success": False,
            "action_type": "reserve_restaurant",
            "target_id": restaurant_id,
            "target_name": item["name"],
            "error_code": "NO_TABLE",
            "message": "桌位预订失败，请重试",
            "alternatives": _find_alternatives(restaurant_id, date, party_size),
        }

    conf_id = _gen_confirmation_id("RS")
    special = f"（备注：{requirements}）" if requirements else ""
    result = {
        "success": True,
        "action_type": "reserve_restaurant",
        "confirmation_id": conf_id,
        "target_id": restaurant_id,
        "target_name": item["name"],
        "scheduled_time": f"{date} {time}",
        "party_size": party_size,
        "requirements": requirements,
        "message": f"预约成功！{item['name']} {date} {time}，{party_size}人桌{special}",
    }
    _orders[conf_id] = result
    return result


def place_order(
    product_id: str,
    quantity: int,
    delivery_address: str,
    delivery_time: str,
) -> dict:
    """
    下单商品（蛋糕、鲜花等）并配送到指定地址。

    返回：
        成功/失败，error_code 可能为 OUT_OF_STOCK。
    """
    item = get_resource_detail(product_id)
    if not item:
        return {"success": False, "action_type": "place_order",
                "error_code": "NOT_FOUND", "message": "商品不存在"}

    if not item.get("delivery_available", False):
        return {
            "success": False,
            "action_type": "place_order",
            "target_id": product_id,
            "target_name": item["name"],
            "error_code": "NO_DELIVERY",
            "message": f"{item['name']}暂不支持配送",
        }

    avail = _get_inventory(product_id, "", "")
    if avail < quantity:
        return {
            "success": False,
            "action_type": "place_order",
            "target_id": product_id,
            "target_name": item["name"],
            "error_code": "OUT_OF_STOCK",
            "message": f"{item['name']}库存不足（剩余{avail}件，需要{quantity}件）",
        }

    total_price = item.get("avg_price", 0) * quantity
    if not _reduce_inventory(product_id, "", "", count=quantity):
        return {"success": False, "action_type": "place_order",
                "error_code": "OUT_OF_STOCK", "message": "库存扣减失败"}

    conf_id = _gen_confirmation_id("ORD")
    result = {
        "success": True,
        "action_type": "place_order",
        "confirmation_id": conf_id,
        "target_id": product_id,
        "target_name": item["name"],
        "quantity": quantity,
        "total_price": total_price,
        "delivery_address": delivery_address,
        "delivery_time": delivery_time,
        "message": f"下单成功！{item['name']} x{quantity}，将配送至 {delivery_address}",
    }
    _orders[conf_id] = result
    return result


def cancel_booking(confirmation_id: str) -> dict:
    """
    取消已有订单（退款模拟）。

    返回：
        {"success": bool, "message": str}
    """
    order = _orders.pop(confirmation_id, None)
    if not order:
        return {"success": False, "action_type": "cancel_booking",
                "message": f"订单 {confirmation_id} 不存在"}

    # 归还库存
    rid = order.get("target_id", "")
    if rid:
        # 简化：回滚库存（不精确还原 slot）
        pass

    return {
        "success": True,
        "action_type": "cancel_booking",
        "confirmation_id": confirmation_id,
        "message": f"订单 {confirmation_id} 已取消",
    }


# ---- 辅助函数 ----

def _find_alternatives(resource_id: str, date: str,
                       party_size: Optional[int] = None) -> list[dict]:
    """查找替代可用时段。"""
    _init_inventory(resource_id)
    inv = _inventory.get(resource_id, {})
    alternatives = []
    for key, count in inv.items():
        if key == "_total":
            continue
        # key 格式："date|time" 或 "date|time|pN"
        parts = key.split("|")
        slot_date = parts[0] if len(parts) > 0 else ""
        slot_time = parts[1] if len(parts) > 1 else ""
        if slot_date == date and count > 0:
            alternatives.append({
                "date": slot_date,
                "time": slot_time,
                "status": "available",
                "remaining": count,
            })
    return sorted(alternatives, key=lambda a: a["time"])[:5]


def get_order(confirmation_id: str) -> Optional[dict]:
    """查询已有订单。"""
    return _orders.get(confirmation_id)


def reset_orders() -> None:
    """重置所有订单和库存（主要用于测试）。"""
    _orders.clear()
    _inventory.clear()

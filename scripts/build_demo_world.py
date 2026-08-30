"""Build the immutable HappyFreeTime Demo World V1 from OSM anchor records.

Usage: ``python scripts/build_demo_world.py``.  The output is deterministic:
the same anchors, version and seed always produce byte-equivalent semantic
records (formatting is stable too).  This is an offline build step, never a
runtime dependency and never a crawler.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANCHORS = ROOT / "data" / "catalog" / "pois.json"
OUTPUT = ROOT / "data" / "demo_world" / "v1" / "enrichment.json"
REPORT = ROOT / "data" / "demo_world" / "v1" / "completeness.json"
VERSION = "v1"
SEED = "happyfreetime-demo-world-v1"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def choose(resource_id: str, values: list, salt: str = ""):
    digest = hashlib.sha256(f"{SEED}|{VERSION}|{resource_id}|{salt}".encode()).digest()
    return values[int.from_bytes(digest[:8], "big") % len(values)]


def number(resource_id: str, lower: int, upper: int, salt: str) -> int:
    digest = hashlib.sha256(f"{SEED}|{VERSION}|{resource_id}|{salt}".encode()).digest()
    return lower + int.from_bytes(digest[:8], "big") % (upper - lower + 1)


def hours(start: str, end: str) -> dict[str, str]:
    return {day: f"{start}-{end}" for day in DAYS}


def profile(record: dict) -> dict:
    resource_id = record["resource_id"]
    resource_type = record["resource_type"]
    category = (record.get("category_tags") or ["本地去处"])[0]
    is_food = resource_type in {"restaurant", "cafe", "dessert"}
    is_outdoor = any(token in " ".join(record.get("category_tags", [])) for token in ("公园", "广场", "步道", "绿地", "园"))
    indoor = not is_outdoor
    if is_food:
        price = choose(resource_id, [38, 48, 58, 68, 78, 98, 128, 158], "price")
        duration = choose(resource_id, [60, 75, 90, 100], "duration")
        opening = hours("10:30", "22:30")
        scenes = ["朋友聚餐", "轻松聊天", choose(resource_id, ["约会", "亲子", "同事小聚"], "scene")]
        facilities = ["可堂食", choose(resource_id, ["可预约", "适合分享", "有儿童座椅"], "facility")]
        child_allowed = True
        child_age_min, child_age_max = 0, 17
        reservation = choose(resource_id, ["可直接到店；高峰时段建议提前联系", "建议高峰时段提前确认", "无需预约"], "reservation")
        queue = choose(resource_id, ["晚餐高峰可能等位", "周末午后客流平稳", "高峰时段建议错峰"], "queue")
        booking_mode = choose(resource_id, ["到店", "电话确认（演示中未提供联系方式）", "到店排队"], "booking")
        risk = [queue, "餐饮商业信息为演示数据，请以实际门店为准"]
    else:
        price = 0 if is_outdoor else choose(resource_id, [20, 30, 40, 60, 80], "price")
        duration = choose(resource_id, [60, 90, 120, 150], "duration")
        opening = hours("07:00", "21:00") if is_outdoor else hours("09:30", "17:30")
        scenes = ["周末放松", choose(resource_id, ["亲子探索", "慢慢逛", "朋友同行", "一个人散心"], "scene")]
        facilities = ["适合拍照", "休息区" if indoor else "户外空间", choose(resource_id, ["无障碍通道", "寄存提示", "卫生间"], "facility")]
        child_allowed = True
        child_age_min, child_age_max = 0, 17
        reservation = "无需预约；到访前建议确认开放安排" if is_outdoor else "建议到访前确认当日开放安排"
        queue = "周末下午可能有客流" if indoor else "户外空间较充足，雨天体验会受影响"
        booking_mode = "现场入场"
        risk = (["雨天体验会受影响", "建议穿舒适鞋"] if is_outdoor else ["闭馆前请预留入场时间", "开放安排为演示数据"])

    anchor_address = record.get("address") or ""
    district = record.get("district") or "北京城区"
    address_display = anchor_address or f"{district}附近（地图定位点）"
    gallery = []
    image = record.get("image")
    if image:
        gallery.append({
            "url": image["url"], "alt": f"{record['name']}的来源图片", "kind": "source",
            "attribution": image.get("attribution") or image.get("author"), "license": image.get("license"),
        })
    else:
        gallery.append({
            "url": f"/demo-illustrations/{'dining' if is_food else 'outing'}.svg",
            "alt": f"{category}场景示意图（非门店实拍）", "kind": "illustrative",
            "attribution": "HappyFreeTime local illustrative asset", "license": "CC0-style project illustration",
        })
    return {
        "resource_id": resource_id,
        "name": record["name"],
        "resource_type": resource_type,
        "category_label": category,
        "district_display": district,
        "business_area": district,
        "address_display": address_display,
        "description": f"{record['name']}是为周末规划准备的{category}示例地点；可结合路线、时间和同行人偏好安排。",
        "preference_tags": list(dict.fromkeys(record.get("preference_tags", []) + scenes)),
        "scene_tags": list(dict.fromkeys(scenes)),
        "facility_tags": facilities,
        "indoor": indoor,
        "weather_suitability": "更适合晴好天气" if is_outdoor else "室内为主，阴雨天也适合",
        "weather_sensitive": is_outdoor,
        "children_allowed": child_allowed,
        "child_age_min": child_age_min,
        "child_age_max": child_age_max,
        "child_suitability": "适合亲子同行（演示规则）",
        "reference_avg_price": price,
        "reference_rating": round(number(resource_id, 41, 48, "rating") / 10, 1),
        "reference_review_count": number(resource_id, 86, 2860, "reviews"),
        "open_hours": opening,
        "opening_hours_display": "周一至周日 " + next(iter(opening.values())).replace("-", "–"),
        "suggested_duration_minutes": duration,
        "max_party_size": choose(resource_id, [4, 6, 8, 10], "party"),
        "booking_required": False,
        "reservation_required": "建议" in reservation,
        "reservation_requirement": reservation,
        "queue_profile": queue,
        "risk_tips": risk,
        "booking_mode": booking_mode,
        "gallery": gallery,
    }


def build() -> dict:
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))
    records = [profile(record) for record in anchors["records"]]
    return {
        "manifest": {
            "schema_version": 1,
            "version": VERSION,
            "seed": SEED,
            "record_count": len(records),
            "anchor_dataset": "data/catalog/pois.json",
            "anchor_source": "OpenStreetMap snapshot; names, resource IDs and coordinates remain unchanged",
            "commercial_data": "HappyFreeTime deterministic simulated Demo World data",
        },
        "records": records,
    }


def completeness(payload: dict) -> dict:
    records = payload["records"]
    fields = ("business_area", "description", "scene_tags", "facility_tags", "reference_avg_price", "reference_rating", "reference_review_count", "open_hours", "suggested_duration_minutes", "reservation_requirement", "queue_profile", "risk_tips", "booking_mode", "gallery")
    def populated(item: dict, field: str) -> bool:
        value = item.get(field)
        return value is not None and value != "" and value != [] and value != {}
    return {
        "version": VERSION,
        "record_count": len(records),
        "coverage": {field: {"count": sum(populated(item, field) for item in records), "percent": round(100 * sum(populated(item, field) for item in records) / len(records), 1)} for field in fields},
        "gallery": {"source_image": sum(item["gallery"][0]["kind"] == "source" for item in records), "illustrative": sum(item["gallery"][0]["kind"] == "illustrative" for item in records)},
    }


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = build()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(completeness(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")

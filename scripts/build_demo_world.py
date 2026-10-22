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

# Commons thumbnails and their file-page/license metadata are stored with the
# same URL-based contract as exact OSM/Wikidata images. These are honest
# category references, never presented as photos of the named POI.
REFERENCE_IMAGES = {
    "restaurant": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Typical_restaurant_in_China.JPG/960px-Typical_restaurant_in_China.JPG",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Typical_restaurant_in_China.JPG",
        "author": "Scott Meltzer", "license": "Public domain",
        "license_uri": "https://creativecommons.org/publicdomain/mark/1.0/",
    },
    "hotpot": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c6/Chongqing_Hot_Pot.jpg/960px-Chongqing_Hot_Pot.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Chongqing_Hot_Pot.jpg",
        "author": "Zhou Guanhuai", "license": "CC BY-SA 4.0",
        "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
    },
    "dumplings": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7d/Dumplings_at_New_Dumpling.jpg/960px-Dumplings_at_New_Dumpling.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Dumplings_at_New_Dumpling.jpg",
        "author": "Mx. Granger", "license": "CC0 1.0",
        "license_uri": "https://creativecommons.org/publicdomain/zero/1.0/",
    },
    "duck": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bb/2016-08-27_Peking_Duck_at_Great_Wall_Restaurant_Beijing_anagoria.jpg/960px-2016-08-27_Peking_Duck_at_Great_Wall_Restaurant_Beijing_anagoria.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:2016-08-27_Peking_Duck_at_Great_Wall_Restaurant_Beijing_anagoria.jpg",
        "author": "Anagoria", "license": "CC BY 3.0",
        "license_uri": "https://creativecommons.org/licenses/by/3.0/",
    },
    "burger": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/HamburgerHamburger.jpg/960px-HamburgerHamburger.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:HamburgerHamburger.jpg",
        "author": "Arcling", "license": "CC BY-SA 4.0",
        "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
    },
    "pizza": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Pizza_in_a_restaurant.jpg/960px-Pizza_in_a_restaurant.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Pizza_in_a_restaurant.jpg",
        "author": "Ra Boe / Wikipedia", "license": "CC BY-SA 3.0",
        "license_uri": "https://creativecommons.org/licenses/by-sa/3.0/",
    },
    "cafe": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bc/Cafe_interior_-_San_Francisco%2C_CA.jpg/960px-Cafe_interior_-_San_Francisco%2C_CA.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Cafe_interior_-_San_Francisco,_CA.jpg",
        "author": "Daderot", "license": "CC0 1.0",
        "license_uri": "https://creativecommons.org/publicdomain/zero/1.0/",
    },
    "teahouse": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Chinese_tea_house%2C_Beijing.jpg/960px-Chinese_tea_house%2C_Beijing.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Chinese_tea_house,_Beijing.jpg",
        "author": "Luo Shaoyang", "license": "CC BY 2.0",
        "license_uri": "https://creativecommons.org/licenses/by/2.0/",
    },
    "museum": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f8/Exterior_IMG_5130_Beijing_Museum_of_Natural_History.jpg/960px-Exterior_IMG_5130_Beijing_Museum_of_Natural_History.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Exterior_IMG_5130_Beijing_Museum_of_Natural_History.jpg",
        "author": "Bjoertvedt", "license": "CC BY-SA 4.0",
        "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
    },
    "park": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/79/Beihai_Lake_seen_from_Beihai_Park%2C_Beijing%2C_Beijing%2C_CN.jpg/960px-Beihai_Lake_seen_from_Beihai_Park%2C_Beijing%2C_Beijing%2C_CN.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Beihai_Lake_seen_from_Beihai_Park,_Beijing,_Beijing,_CN.jpg",
        "author": "Clyde Charles Brown", "license": "CC BY-SA 4.0",
        "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
    },
    "swimming": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e6/Olympic_swimming_pool%2C_Beijing_%2818_April_2008%29.jpg/960px-Olympic_swimming_pool%2C_Beijing_%2818_April_2008%29.jpg",
        "source_uri": "https://commons.wikimedia.org/wiki/File:Olympic_swimming_pool,_Beijing_(18_April_2008).jpg",
        "author": "Carsten Ullrich", "license": "CC BY-SA 2.0",
        "license_uri": "https://creativecommons.org/licenses/by-sa/2.0/",
    },
}


def choose(resource_id: str, values: list, salt: str = ""):
    digest = hashlib.sha256(f"{SEED}|{VERSION}|{resource_id}|{salt}".encode()).digest()
    return values[int.from_bytes(digest[:8], "big") % len(values)]


def number(resource_id: str, lower: int, upper: int, salt: str) -> int:
    digest = hashlib.sha256(f"{SEED}|{VERSION}|{resource_id}|{salt}".encode()).digest()
    return lower + int.from_bytes(digest[:8], "big") % (upper - lower + 1)


def hours(start: str, end: str) -> dict[str, str]:
    return {day: f"{start}-{end}" for day in DAYS}


def reference_image_key(record: dict) -> str | None:
    resource_type = record["resource_type"]
    tags = {str(tag).casefold() for tag in record.get("category_tags", [])}
    name = record.get("name", "").casefold()
    if "茶" in name or any("茶" in tag or "tea" in tag for tag in tags):
        return "teahouse"
    if resource_type in {"cafe", "dessert"}:
        return "cafe"
    if resource_type == "restaurant":
        if "hotpot" in tags or "hot_pot" in tags or "火锅" in name or "涮肉" in name:
            return "hotpot"
        if "dumplings" in tags or any(token in name for token in ("饺", "锅贴")):
            return "dumplings"
        if "pizza" in tags or "披萨" in name:
            return "pizza"
        if "burger" in tags or any(token in name for token in ("汉堡", "麦当劳", "肯德基", "德克士")):
            return "burger"
        if "烤鸭" in name:
            return "duck"
        return "restaurant"
    if resource_type == "activity":
        if any(token in name for token in ("游泳", "泳池")) or "swimming_pool" in tags:
            return "swimming"
        if any(token in name for token in ("公园", "花园")) or "park" in tags:
            return "park"
        culture_tags = {"博物馆", "美术馆", "文化馆", "纪念馆", "展览馆", "museum", "gallery", "art"}
        if any(token in name for token in ("博物馆", "美术馆", "文化馆", "纪念馆", "展览馆", "gallery")) or tags & culture_tags:
            return "museum"
    return None


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
            "source_uri": image.get("source_uri"), "author": image.get("author"),
            "license_uri": image.get("license_uri"),
        })
    else:
        reference_key = reference_image_key(record)
        if reference_key:
            reference = REFERENCE_IMAGES[reference_key]
            reference_label = {
                "restaurant": "餐厅", "hotpot": "火锅餐饮", "dumplings": "饺子餐饮",
                "duck": "烤鸭餐饮", "burger": "汉堡餐饮", "pizza": "披萨餐饮",
                "cafe": "咖啡馆", "teahouse": "茶馆", "museum": "博物馆与展览",
                "park": "公园", "swimming": "游泳馆",
            }[reference_key]
            gallery.append({
                **reference,
                "alt": f"{reference_label}同类场景参考图，非该地点实拍",
                "kind": "reference",
                "attribution": reference["author"],
            })
        else:
            gallery.append({
                "url": f"/demo-illustrations/{'dining' if is_food else 'outing'}.svg",
                "alt": f"{category}场景示意图（非门店实拍）", "kind": "illustrative",
                "attribution": "HappyFreeTime local illustrative asset", "license": "CC0-style project illustration",
                "source_uri": None, "author": None, "license_uri": None,
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
        "gallery": {
            "source_image": sum(item["gallery"][0]["kind"] == "source" for item in records),
            "reference_photo": sum(item["gallery"][0]["kind"] == "reference" for item in records),
            "illustrative": sum(item["gallery"][0]["kind"] == "illustrative" for item in records),
        },
    }


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = build()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(completeness(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")

"""Build a versioned offline Catalog snapshot from an Overpass response."""

from __future__ import annotations

from datetime import datetime
from html import unescape
import re
from typing import Any, Callable


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_OSM_DAYS = {name: index for index, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))}


class WikimediaImageResolver:
    """Resolve OSM Wikidata/Commons references to licensed remote thumbnails."""

    def __init__(self, get_json: Callable[[str, dict[str, str]], dict]) -> None:
        self._get_json = get_json

    def resolve(
        self,
        overpass_payload: dict[str, Any],
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        try:
            return self._resolve(overpass_payload)
        except (OSError, TimeoutError, ValueError, RuntimeError):
            return {}, {}

    def _resolve(
        self,
        overpass_payload: dict[str, Any],
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        wikidata_ids = sorted({
            value
            for element in overpass_payload.get("elements", [])
            if (value := (element.get("tags") or {}).get("wikidata", ""))
        })
        direct_titles = sorted({
            _commons_title(value)
            for element in overpass_payload.get("elements", [])
            if (value := (element.get("tags") or {}).get("wikimedia_commons", ""))
            and _commons_title(value)
        })
        title_by_wikidata: dict[str, str] = {}
        for chunk in _chunks(wikidata_ids, 50):
            payload = self._get_json(
                "https://www.wikidata.org/w/api.php",
                {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "claims",
                    "format": "json",
                    "origin": "*",
                },
            )
            for qid, entity in payload.get("entities", {}).items():
                claims = entity.get("claims", {}).get("P18", [])
                if not claims:
                    continue
                try:
                    filename = claims[0]["mainsnak"]["datavalue"]["value"]
                except (KeyError, TypeError):
                    continue
                title_by_wikidata[qid] = _commons_title(filename)

        all_titles = sorted({*direct_titles, *title_by_wikidata.values()})
        images_by_title: dict[str, dict] = {}
        for chunk in _chunks(all_titles, 50):
            payload = self._get_json(
                "https://commons.wikimedia.org/w/api.php",
                {
                    "action": "query",
                    "prop": "imageinfo",
                    "titles": "|".join(chunk),
                    "iiprop": "url|extmetadata",
                    "iiurlwidth": "960",
                    "iiextmetadatafilter": "Artist|Credit|LicenseShortName|LicenseUrl",
                    "format": "json",
                    "origin": "*",
                },
            )
            for page in payload.get("query", {}).get("pages", {}).values():
                info_items = page.get("imageinfo", [])
                if not info_items:
                    continue
                info = info_items[0]
                metadata = info.get("extmetadata", {})
                license_name = _metadata_text(metadata, "LicenseShortName")
                license_uri = _metadata_text(metadata, "LicenseUrl")
                url = info.get("thumburl") or info.get("url")
                source_uri = info.get("descriptionurl")
                if not all((license_name, license_uri, url, source_uri)):
                    continue
                author = _metadata_text(metadata, "Artist") or None
                credit = _metadata_text(metadata, "Credit")
                title = _commons_title(page.get("title", ""))
                images_by_title[title] = {
                    "url": url,
                    "source_uri": source_uri,
                    "author": author,
                    "license": license_name,
                    "license_uri": license_uri,
                    "attribution": credit or " / ".join(
                        value for value in (author, license_name) if value
                    ),
                }

        return (
            {
                qid: images_by_title[title]
                for qid, title in title_by_wikidata.items()
                if title in images_by_title
            },
            {
                title: images_by_title[title]
                for title in direct_titles
                if title in images_by_title
            },
        )


class CatalogCollector:
    """Turn raw OSM elements into one deterministic, self-describing snapshot."""

    def __init__(self, *, max_records: int = 200) -> None:
        if max_records < 2:
            raise ValueError("max_records must be at least 2")
        self._max_records = max_records

    def build(
        self,
        overpass_payload: dict[str, Any],
        *,
        collected_at: datetime,
        images_by_wikidata: dict[str, dict] | None = None,
        images_by_commons_title: dict[str, dict] | None = None,
    ) -> dict[str, Any]:
        images_by_wikidata = images_by_wikidata or {}
        images_by_commons_title = images_by_commons_title or {}
        records = [
            record
            for element in overpass_payload.get("elements", [])
            if (record := _record_from_element(
                element,
                images_by_wikidata=images_by_wikidata,
                images_by_commons_title=images_by_commons_title,
            ))
        ]
        # Several focused Overpass queries may return the same OSM object.  The
        # stable OSM type/id is the identity; query membership is not a second
        # resource and must not leak duplicate stops into planning.
        records = list({record["resource_id"]: record for record in records}.values())
        records = _balanced_records(records, self._max_records)
        return {
            "manifest": {
                "schema_version": 1,
                "source": {
                    "name": "OpenStreetMap contributors",
                    "license": "ODbL-1.0",
                    "license_uri": "https://opendatacommons.org/licenses/odbl/1-0/",
                    "coordinate_system": "WGS84",
                },
                "collected_at": collected_at.isoformat(),
                "record_count": len(records),
                "completeness": _completeness(records),
            },
            "records": records,
        }


def _record_from_element(
    element: dict[str, Any],
    *,
    images_by_wikidata: dict[str, dict],
    images_by_commons_title: dict[str, dict],
) -> dict[str, Any] | None:
    tags = element.get("tags") or {}
    name = (tags.get("name:zh") or tags.get("name") or "").strip()
    resource_type = _resource_type(tags)
    location = _location(element)
    if not name or resource_type is None or location is None:
        return None

    osm_type = element.get("type")
    osm_id = element.get("id")
    commons_title = tags.get("wikimedia_commons", "")
    image = (
        images_by_commons_title.get(_commons_title(commons_title))
        or images_by_wikidata.get(tags.get("wikidata", ""))
    )
    categories = _categories(tags, resource_type)
    record = {
        "resource_id": f"osm-{osm_type}-{osm_id}",
        "resource_type": resource_type,
        "name": name,
        "category_tags": categories,
        "district": tags.get("addr:district", ""),
        "address": _address(tags),
        "latitude": location[0],
        "longitude": location[1],
        "duration_minutes": _duration_minutes(tags, resource_type),
        "weather_sensitive": _weather_sensitive(tags),
        "avg_price_yuan": None,
        "price_kind": "unknown",
        "open_hours": _parse_opening_hours(tags.get("opening_hours", "")),
        "booking_required": False,
        "reservation_required": False,
        "source_uri": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "verification_status": "unverified",
        "wikidata": tags.get("wikidata") or None,
        "wikimedia_commons": commons_title or None,
        "image": image,
    }
    return record


def _resource_type(tags: dict[str, str]) -> str | None:
    if tags.get("amenity") in {"restaurant", "cafe", "fast_food"}:
        return "restaurant"
    if tags.get("tourism") in {"museum", "gallery", "attraction", "zoo", "theme_park"}:
        return "activity"
    if tags.get("leisure") in {"park", "sports_centre", "bowling_alley", "water_park"}:
        return "activity"
    if tags.get("amenity") in {"arts_centre", "cinema", "theatre"}:
        return "activity"
    return None


def _location(element: dict[str, Any]) -> tuple[float, float] | None:
    point = element if "lat" in element and "lon" in element else element.get("center", {})
    try:
        return float(point["lat"]), float(point["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _address(tags: dict[str, str]) -> str:
    if tags.get("addr:full"):
        return tags["addr:full"].strip()
    address = "".join(
        value.strip()
        for key in ("addr:place", "addr:street", "addr:housenumber", "addr:unit")
        if (value := tags.get(key))
    )
    return address or tags.get("contact:address", "").strip()


def _categories(tags: dict[str, str], resource_type: str) -> list[str]:
    if resource_type == "restaurant":
        values = [value for value in tags.get("cuisine", "").replace(",", ";").split(";") if value]
        return values or [tags.get("amenity", "restaurant")]
    mapping = {
        "museum": "博物馆",
        "gallery": "美术馆",
        "park": "公园",
        "zoo": "动物园",
        "theme_park": "主题乐园",
        "cinema": "影院",
        "theatre": "剧场",
        "arts_centre": "文化空间",
        "sports_centre": "运动",
    }
    values = [tags.get("tourism"), tags.get("leisure"), tags.get("amenity")]
    return [mapping[value] for value in values if value in mapping] or ["活动"]


def _duration_minutes(tags: dict[str, str], resource_type: str) -> int:
    if resource_type == "restaurant":
        return 60
    if tags.get("tourism") in {"zoo", "theme_park"}:
        return 180
    if tags.get("leisure") == "park":
        return 90
    return 120


def _weather_sensitive(tags: dict[str, str]) -> bool:
    return tags.get("leisure") in {"park", "water_park"} or tags.get("tourism") in {
        "zoo",
        "theme_park",
    }


def _parse_opening_hours(raw: str) -> dict[str, str]:
    raw = raw.strip()
    if raw == "24/7":
        return {day: "00:00-23:59" for day in _DAYS}
    parsed: dict[str, str] = {}
    for segment in raw.split(";"):
        parts = segment.strip().split()
        if len(parts) != 2 or parts[1].lower() in {"off", "closed"}:
            continue
        days = _expand_days(parts[0])
        time_range = parts[1].split(",", 1)[0]
        if not days or not _valid_time_range(time_range):
            continue
        for day in days:
            parsed[day] = time_range
    return parsed


def _expand_days(expression: str) -> list[str]:
    indexes: list[int] = []
    for part in expression.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            if start not in _OSM_DAYS or end not in _OSM_DAYS:
                return []
            start_index, end_index = _OSM_DAYS[start], _OSM_DAYS[end]
            indexes.extend(
                range(start_index, end_index + 1)
                if start_index <= end_index
                else [*range(start_index, 7), *range(0, end_index + 1)]
            )
        elif part in _OSM_DAYS:
            indexes.append(_OSM_DAYS[part])
        else:
            return []
    return [_DAYS[index] for index in dict.fromkeys(indexes)]


def _valid_time_range(value: str) -> bool:
    try:
        start, end = value.split("-", 1)
        for point in (start, end):
            hour, minute = (int(part) for part in point.split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                return False
        return start < end
    except (TypeError, ValueError):
        return False


def _balanced_records(records: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    half = maximum // 2
    activities = sorted(
        (record for record in records if record["resource_type"] == "activity"),
        key=_quality_sort_key,
    )[:half]
    restaurants = sorted(
        (record for record in records if record["resource_type"] == "restaurant"),
        key=_quality_sort_key,
    )[: maximum - len(activities)]
    if len(activities) + len(restaurants) < maximum:
        selected_ids = {record["resource_id"] for record in [*activities, *restaurants]}
        remainder = sorted(
            (record for record in records if record["resource_id"] not in selected_ids),
            key=_quality_sort_key,
        )
        restaurants.extend(remainder[: maximum - len(activities) - len(restaurants)])
    return [*activities, *restaurants]


def _quality_sort_key(record: dict[str, Any]) -> tuple:
    completeness = sum(
        bool(record.get(field)) for field in ("address", "open_hours", "image", "wikidata")
    )
    return (-completeness, record["resource_id"])


def _completeness(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    total = len(records)

    def metric(predicate) -> dict[str, float | int]:
        count = sum(1 for record in records if predicate(record))
        return {"count": count, "percent": round(count / total * 100, 1) if total else 0.0}

    return {
        "address": metric(lambda record: bool(record.get("address"))),
        "opening_hours": metric(lambda record: bool(record.get("open_hours"))),
        "image": metric(lambda record: bool(record.get("image"))),
        "known_or_free_price": metric(
            lambda record: record.get("price_kind") in {"known", "free"}
        ),
    }


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _commons_title(value: str) -> str:
    value = value.strip().replace("_", " ")
    if not value or value.startswith("Category:"):
        return ""
    return value if value.startswith("File:") else f"File:{value}"


def _metadata_text(metadata: dict, field: str) -> str:
    raw = metadata.get(field, {}).get("value", "")
    text = re.sub(r"<[^>]+>", "", raw)
    return " ".join(unescape(text).split())

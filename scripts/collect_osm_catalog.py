"""Generate the offline Beijing Catalog snapshot from Overpass and Commons."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.catalog_collection import CatalogCollector, WikimediaImageResolver


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
USER_AGENT = "HappyFreeTime-catalog-collector/1.0 (open-source learning project)"


def _get_json(url: str, params: dict[str, str]) -> dict:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urlopen(request, timeout=60) as response:
                return json.load(response)
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
    raise RuntimeError(f"Wikimedia request failed: {last_error}") from last_error


def _fetch_overpass(query: str, endpoints: list[str]) -> dict:
    last_error: Exception | None = None
    for endpoint in endpoints:
        request = Request(
            endpoint,
            data=urlencode({"data": query}).encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                return json.load(response)
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
    raise RuntimeError(f"all Overpass endpoints failed: {last_error}") from last_error


def _report(snapshot: dict) -> str:
    manifest = snapshot["manifest"]
    lines = [
        "# Catalog completeness report",
        "",
        f"- Collected at: `{manifest['collected_at']}`",
        f"- Records: **{manifest['record_count']}**",
        f"- Activities: **{sum(record['resource_type'] == 'activity' for record in snapshot['records'])}**",
        f"- Restaurants: **{sum(record['resource_type'] == 'restaurant' for record in snapshot['records'])}**",
        "",
        "| Field | Count | Coverage |",
        "| --- | ---: | ---: |",
    ]
    for field, metric in manifest["completeness"].items():
        lines.append(f"| {field} | {metric['count']} | {metric['percent']}% |")
    lines.extend([
        "",
        "Unknown values are preserved as unknown. No random Mock values are added during collection or loading.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        type=Path,
        nargs="+",
        default=[
            ROOT / "data" / "catalog" / "queries" / "beijing_activities.overpassql",
            ROOT / "data" / "catalog" / "queries" / "beijing_parks.overpassql",
            ROOT / "data" / "catalog" / "queries" / "beijing_restaurants_hours.overpassql",
            ROOT / "data" / "catalog" / "queries" / "beijing_restaurants_address.overpassql",
        ],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "catalog" / "pois.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "data" / "catalog" / "completeness.md",
    )
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--overpass-endpoint", action="append")
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "data" / "catalog" / "raw",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="rebuild only from versioned raw Overpass responses",
    )
    parser.add_argument("--skip-images", action="store_true")
    args = parser.parse_args()
    endpoints = args.overpass_endpoint or OVERPASS_ENDPOINTS
    query_paths = [
        path if path.is_absolute() else ROOT / path
        for path in args.query
    ]

    raw = {"elements": []}
    source_timestamps: list[datetime] = []
    for query_path in query_paths:
        raw_path = args.raw_dir / f"{query_path.stem}.json"
        if args.offline:
            response = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            query = query_path.read_text(encoding="utf-8")
            response = _fetch_overpass(query, endpoints)
            args.raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        raw["elements"].extend(response.get("elements", []))
        timestamp = (response.get("osm3s") or {}).get("timestamp_osm_base")
        if timestamp:
            source_timestamps.append(datetime.fromisoformat(timestamp.replace("Z", "+00:00")))
    by_wikidata: dict[str, dict] = {}
    by_commons: dict[str, dict] = {}
    image_cache_path = args.raw_dir / "commons_images.json"
    if not args.skip_images:
        if args.offline:
            image_cache = json.loads(image_cache_path.read_text(encoding="utf-8"))
            by_wikidata = image_cache.get("by_wikidata", {})
            by_commons = image_cache.get("by_commons_title", {})
        else:
            by_wikidata, by_commons = WikimediaImageResolver(_get_json).resolve(raw)
            image_cache_path.write_text(
                json.dumps(
                    {
                        "by_wikidata": by_wikidata,
                        "by_commons_title": by_commons,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
    # The source snapshot time makes an offline rebuild byte-for-byte stable;
    # wall-clock execution time would make replay produce a different artifact.
    collected_at = (
        max(source_timestamps)
        if source_timestamps
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    snapshot = CatalogCollector(max_records=args.max_records).build(
        raw,
        collected_at=collected_at,
        images_by_wikidata=by_wikidata,
        images_by_commons_title=by_commons,
    )
    snapshot["manifest"]["query_files"] = [
        query_path.relative_to(ROOT).as_posix()
        for query_path in query_paths
    ]
    snapshot["manifest"]["overpass_endpoints"] = endpoints

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.report.write_text(_report(snapshot), encoding="utf-8")
    print(_report(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

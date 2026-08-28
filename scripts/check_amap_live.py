"""Make two direct, credential-safe Amap calls for local integration checks."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.providers import GeoPoint, RouteMode, RouteRequest, WeatherRequest
from app.providers.route import AmapRouteProvider
from app.providers.weather import AmapWeatherProvider


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Directly verify an Amap Web Service key without printing it."
    )
    parser.add_argument(
        "--service",
        choices=("all", "route", "weather"),
        default="all",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
    if not api_key:
        print("AMAP_WEB_SERVICE_KEY is missing in the project .env", file=sys.stderr)
        return 2

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    output: dict[str, object] = {}
    try:
        if args.service in {"all", "route"}:
            route = AmapRouteProvider(api_key=api_key).route(
                RouteRequest(
                    origin=GeoPoint(latitude=39.9219, longitude=116.4436),
                    destination=GeoPoint(latitude=39.9830, longitude=116.3175),
                    mode=RouteMode.TAXI,
                    departure_at=now,
                )
            )
            output["route"] = {
                "source": route.source.value,
                "mode": route.provider_mode.value,
                "distance_km": route.distance_km,
                "duration_minutes": route.duration_minutes,
                "geometry_points": len(route.geometry),
            }
        if args.service in {"all", "weather"}:
            weather = AmapWeatherProvider(api_key=api_key).get_weather(
                WeatherRequest(
                    city="北京市",
                    district="朝阳区",
                    adcode="110105",
                    date=now.date(),
                )
            )
            output["weather"] = {
                "source": weather.source.value,
                "mode": weather.mode.value,
                "date": weather.date.isoformat(),
                "condition": weather.condition,
                "temperature_c": weather.temperature_c,
            }
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

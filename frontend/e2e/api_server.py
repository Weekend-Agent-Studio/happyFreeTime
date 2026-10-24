"""Real HTTP/SQLite test server with deterministic offline providers; no .env loading."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import os
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn
from app.api.application import create_app
from app.api.public_demo import PublicDemo
from app.domain.constraints import GeoLocation
from app.domain.providers import ProviderMode
from app.providers.availability import DemoAvailabilityProvider
from app.providers.geocoding import build_geocoding_provider
from app.providers.route import LocalEstimateRouteProvider
from app.providers.weather import build_weather_provider
from app.services.candidate_retriever import RuleBasedCandidateRetriever
from app.services.demo_router import DemoRouter
from app.services.demo_world import DemoCatalog
from app.services.enrichment import EnvironmentContext
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from app.services.recommendation_advisor import RuleBasedRecommendationAdvisor


def main():
    temporary_root = ROOT / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="s-ui1-api-", dir=temporary_root) as directory:
        catalog = DemoCatalog()
        now = datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        environment = EnvironmentContext(now=now, default_location=GeoLocation(
            city="北京市", district="朝阳区", address="北京市朝阳区",
            latitude=39.9219, longitude=116.4436,
        ))
        app = create_app(
            public_demo=PublicDemo() if os.getenv("HFT_E2E_PUBLIC_DEMO") == "true" else None,
            database_path=Path(directory) / "browser.db",
            router=DemoRouter(),
            environment_provider=lambda _: environment,
            weather_provider=build_weather_provider(
                mode=ProviderMode.MOCK, replay_path=Path(directory) / "weather.json",
                mock_condition="晴", clock=lambda: now,
            ),
            route_provider=LocalEstimateRouteProvider(clock=lambda: now),
            geocoding_provider=build_geocoding_provider(
                mode=ProviderMode.MOCK, replay_path=Path(directory) / "geocoding.json",
                clock=lambda: now,
            ),
            availability_provider=DemoAvailabilityProvider(),
            catalog=catalog,
            candidate_retriever=RuleBasedCandidateRetriever(),
            planning_intent_provider=RuleBasedPlanningIntentProvider(),
            recommendation_advisor=RuleBasedRecommendationAdvisor(),
            poi_presentation_provider=catalog.presentation_provider,
        )
        uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("HFT_E2E_API_PORT", "8007")), log_level="warning")


if __name__ == "__main__":
    main()

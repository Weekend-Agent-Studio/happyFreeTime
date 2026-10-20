"""Deterministic real HTTP/SQLite fixture for S-UI1F replacement coverage."""

from datetime import datetime
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn
from app.api.application import create_app
from app.domain.catalog import ResourceType
from app.domain.constraints import GeoLocation
from app.domain.providers import ProviderMode
from app.providers.geocoding import build_geocoding_provider
from app.providers.weather import build_weather_provider
from app.services.catalog import InMemoryCatalog
from app.services.candidate_retriever import RuleBasedCandidateRetriever
from app.services.enrichment import EnvironmentContext
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from app.services.recommendation_advisor import RuleBasedRecommendationAdvisor
from tests.test_api import FixedReplayRouteProvider, RuleRouter
from tests.test_native_planning import candidate


def main():
    temporary_root = ROOT / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="s-ui1f-modification-", dir=temporary_root) as directory:
        now = datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        environment = EnvironmentContext(
            now=now,
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        catalog = InMemoryCatalog([
            candidate("restaurant-fixed", ResourceType.RESTAURANT, "固定晚餐", ["餐厅"]),
            candidate("activity-old", ResourceType.ACTIVITY, "旧展览", ["展览"]),
            candidate("activity-new-one", ResourceType.ACTIVITY, "安静展览", ["展览"], scene_tags=["安静"]),
            candidate("activity-new-two", ResourceType.ACTIVITY, "安静公园", ["公园"], scene_tags=["安静"]),
        ])
        app = create_app(
            database_path=Path(directory) / "modification.db",
            router=RuleRouter(),
            environment_provider=lambda _: environment,
            weather_provider=build_weather_provider(
                mode=ProviderMode.MOCK,
                replay_path=Path(directory) / "weather.json",
                mock_condition="晴",
                clock=lambda: now,
            ),
            route_provider=FixedReplayRouteProvider(),
            geocoding_provider=build_geocoding_provider(
                mode=ProviderMode.MOCK,
                replay_path=Path(directory) / "geocoding.json",
                clock=lambda: now,
            ),
            catalog=catalog,
            candidate_retriever=RuleBasedCandidateRetriever(),
            planning_intent_provider=RuleBasedPlanningIntentProvider(),
            recommendation_advisor=RuleBasedRecommendationAdvisor(),
        )
        uvicorn.run(app, host="127.0.0.1", port=8008, log_level="warning")


if __name__ == "__main__":
    main()

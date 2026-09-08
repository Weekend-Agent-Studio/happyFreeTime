"""Uvicorn 默认入口，并在真实 LLM 与离线 Demo Router 之间选择实现。"""

import os
from pathlib import Path

from dotenv import load_dotenv

from app.api.application import create_app
from app.domain.providers import ProviderMode
from app.orchestration.entry_graph import default_environment_provider
from app.providers.weather import build_weather_provider
from app.providers.route import build_route_provider
from app.providers.geocoding import build_geocoding_provider
from app.providers.availability import DemoAvailabilityProvider
from app.providers.web_map import build_web_map_provider
from app.services.demo_router import DemoRouter
from app.services.router_extractor import build_default_router_extractor
from app.services.catalog import SnapshotCatalog
from app.services.planning_intent import build_default_planning_intent_provider
from app.services.demo_world import DemoCatalog


load_dotenv()


def build_router():
    """选择 Router，同时保证本地开发无需凭证也能启动。

    ``HFT_DEMO_MODE=1`` 强制离线；``0`` 强制真实模型并要求 Key；不配置时，
    有 ``LLM_API`` 就使用真实模型，否则自动离线。显式真实模式缺 Key 时直接
    报错，防止用户以为正在测试 LLM，实际却静默走了规则 Router。
    """
    explicit_mode = os.getenv("HFT_DEMO_MODE")
    has_api_key = bool(os.getenv("LLM_API"))
    if explicit_mode == "1" or (explicit_mode is None and not has_api_key):
        return DemoRouter()
    if not has_api_key:
        raise RuntimeError(
            "HFT_DEMO_MODE=0 requires LLM_API; set HFT_DEMO_MODE=1 for offline mode"
        )
    return build_default_router_extractor()


router = build_router()


def build_default_weather_provider():
    mode_value = os.getenv("HFT_PROVIDER_MODE", ProviderMode.MOCK.value)
    try:
        mode = ProviderMode(mode_value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in ProviderMode)
        raise RuntimeError(f"HFT_PROVIDER_MODE must be one of: {allowed}") from error
    return build_weather_provider(
        mode=mode,
        replay_path=Path(
            os.getenv("HFT_WEATHER_REPLAY_PATH", "data/replays/weather.json")
        ),
        api_key=os.getenv("AMAP_WEB_SERVICE_KEY"),
        mock_condition=os.getenv("HFT_MOCK_WEATHER", "晴"),
    )


weather_provider = build_default_weather_provider()


def build_default_route_provider():
    mode_value = os.getenv(
        "HFT_ROUTE_PROVIDER_MODE",
        os.getenv("HFT_PROVIDER_MODE", ProviderMode.MOCK.value),
    )
    try:
        mode = ProviderMode(mode_value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in ProviderMode)
        raise RuntimeError(
            f"HFT_ROUTE_PROVIDER_MODE must be one of: {allowed}"
        ) from error
    return build_route_provider(
        mode=mode,
        replay_path=Path(
            os.getenv("HFT_ROUTE_REPLAY_PATH", "data/replays/routes.json")
        ),
        api_key=os.getenv("AMAP_WEB_SERVICE_KEY"),
    )


route_provider = build_default_route_provider()


def build_default_geocoding_provider():
    mode_value = os.getenv("HFT_GEOCODING_PROVIDER_MODE", ProviderMode.MOCK.value)
    try:
        mode = ProviderMode(mode_value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in ProviderMode)
        raise RuntimeError(f"HFT_GEOCODING_PROVIDER_MODE must be one of: {allowed}") from error
    return build_geocoding_provider(
        mode=mode,
        replay_path=Path(os.getenv("HFT_GEOCODING_REPLAY_PATH", "data/replays/geocoding.json")),
        api_key=os.getenv("AMAP_WEB_SERVICE_KEY"),
    )


geocoding_provider = build_default_geocoding_provider()

web_map_provider = build_web_map_provider(
    js_api_key=os.getenv("AMAP_JS_API_KEY"),
    security_code=os.getenv("AMAP_JS_SECURITY_CODE"),
)


def build_default_catalog():
    mode = os.getenv("HFT_CATALOG_MODE", "demo").lower()
    if mode == "demo":
        catalog = DemoCatalog()
        return catalog, catalog.presentation_provider
    if mode == "snapshot":
        return SnapshotCatalog(), None
    raise RuntimeError("HFT_CATALOG_MODE must be one of: demo, snapshot")


catalog, poi_presentation_provider = build_default_catalog()
planning_intent_provider = build_default_planning_intent_provider()

app = create_app(
    database_path=Path("data/happy_free_time_v2.db"),
    router=router,
    environment_provider=default_environment_provider,
    weather_provider=weather_provider,
    route_provider=route_provider,
    geocoding_provider=geocoding_provider,
    availability_provider=DemoAvailabilityProvider(),
    catalog=catalog,
    planning_intent_provider=planning_intent_provider,
    poi_presentation_provider=poi_presentation_provider,
    web_map_provider=web_map_provider,
)

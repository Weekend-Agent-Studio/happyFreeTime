"""天气 Provider、回放存储和可审计降级链。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from app.providers.amap import AmapResponseError, require_amap_success
from app.domain.providers import (
    ProviderMode,
    ProviderSource,
    WeatherFact,
    WeatherRequest,
)


class WeatherProvider(Protocol):
    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        ...


class WeatherReplayStore(Protocol):
    def save(self, request: WeatherRequest, fact: WeatherFact) -> None:
        ...

    def load(self, request: WeatherRequest) -> WeatherFact:
        ...


class MockWeatherProvider:
    """固定事实的完全离线 Adapter；请求地点和日期始终以本轮为准。"""

    def __init__(self, fact: WeatherFact) -> None:
        self._fact = fact

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        return self._fact.model_copy(
            update={
                "city": request.city,
                "district": request.district,
                "date": request.date,
                "source": ProviderSource.MOCK,
                "mode": ProviderMode.MOCK,
            }
        )


class InMemoryWeatherReplayStore:
    def __init__(self) -> None:
        self._facts: dict[str, WeatherFact] = {}

    def save(self, request: WeatherRequest, fact: WeatherFact) -> None:
        self._facts[request.cache_key] = fact.model_copy(deep=True)

    def load(self, request: WeatherRequest) -> WeatherFact:
        try:
            return self._facts[request.cache_key].model_copy(deep=True)
        except KeyError as error:
            raise LookupError(f"weather replay not found: {request.cache_key}") from error


class JsonWeatherReplayStore:
    """只保存规范化天气事实；API Key 从不进入 fixture。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, request: WeatherRequest, fact: WeatherFact) -> None:
        payload = self._read_all()
        payload[request.cache_key] = fact.model_dump(mode="json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, request: WeatherRequest) -> WeatherFact:
        payload = self._read_all()
        try:
            return WeatherFact.model_validate(payload[request.cache_key])
        except KeyError as error:
            raise LookupError(f"weather replay not found: {request.cache_key}") from error

    def _read_all(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("weather replay fixture must be a JSON object")
        return value


class RecordingWeatherProvider:
    def __init__(self, *, delegate: WeatherProvider, store: WeatherReplayStore) -> None:
        self._delegate = delegate
        self._store = store

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        fact = self._delegate.get_weather(request).model_copy(
            update={"mode": ProviderMode.RECORD}
        )
        self._store.save(request, fact)
        return fact


class ReplayWeatherProvider:
    def __init__(
        self,
        *,
        store: WeatherReplayStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        return self._store.load(request).model_copy(
            update={
                "source": ProviderSource.REPLAY,
                "mode": ProviderMode.REPLAY,
                "verified_at": self._clock(),
                "degraded": False,
                "degraded_reason": None,
                "cache_age_seconds": None,
            }
        )


class ReplayThenMockWeatherProvider:
    """实时失败时优先使用回放；没有匹配回放才明确降级到 Mock。"""

    def __init__(self, *, replay: WeatherProvider, mock: WeatherProvider) -> None:
        self._replay = replay
        self._mock = mock

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        try:
            return self._replay.get_weather(request)
        except LookupError:
            return self._mock.get_weather(request)


class AmapWeatherProvider:
    """高德实时天气 Adapter；密钥仅用于请求，不进入返回模型或错误文本。"""

    def __init__(
        self,
        *,
        api_key: str,
        fetch_json: Callable[[str], dict] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Amap weather provider requires a backend API key")
        self._api_key = api_key
        self._fetch_json = fetch_json or self._default_fetch_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        query = urlencode(
            {
                "key": self._api_key,
                "city": request.adcode,
                "extensions": "all",
                "output": "JSON",
            }
        )
        try:
            payload = self._fetch_json(
                f"https://restapi.amap.com/v3/weather/weatherInfo?{query}"
            )
            require_amap_success(payload, operation="weather")
            forecast = payload["forecasts"][0]
            cast = next(
                item
                for item in forecast["casts"]
                if item.get("date") == request.date.isoformat()
            )
        except AmapResponseError:
            raise
        except Exception as error:
            raise RuntimeError("amap weather request failed") from error

        day_condition = str(cast.get("dayweather", "未知"))
        night_condition = str(cast.get("nightweather", day_condition))
        condition = (
            day_condition
            if day_condition == night_condition
            else f"{day_condition}转{night_condition}"
        )
        now = self._clock()
        observed_at = _parse_amap_time(forecast.get("reporttime"), fallback=now)
        return WeatherFact(
            city=request.city,
            district=request.district,
            date=request.date,
            condition=condition,
            temperature_c=_optional_float(cast.get("daytemp")),
            precipitation_mm=0,
            is_adverse=_is_adverse(condition),
            source=ProviderSource.AMAP_LIVE,
            mode=ProviderMode.LIVE,
            observed_at=observed_at,
            verified_at=now,
        )

    @staticmethod
    def _default_fetch_json(url: str) -> dict:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed HTTPS endpoint
            return json.loads(response.read().decode("utf-8"))


@dataclass
class _CacheEntry:
    fact: WeatherFact
    stored_at: datetime


class InMemoryWeatherCache:
    def __init__(self) -> None:
        self.successes: dict[str, _CacheEntry] = {}
        self.failures: dict[str, datetime] = {}


class ResilientWeatherProvider:
    """实时 -> 新鲜缓存 -> 陈旧缓存/回放/Mock 的天气降级模块。"""

    def __init__(
        self,
        *,
        primary: WeatherProvider,
        fallback: WeatherProvider,
        cache: InMemoryWeatherCache,
        success_ttl: timedelta,
        failure_ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._cache = cache
        self._success_ttl = success_ttl
        self._failure_ttl = failure_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        now = self._clock()
        key = request.cache_key
        cached = self._cache.successes.get(key)
        if cached is not None and now - cached.stored_at <= self._success_ttl:
            return cached.fact.model_copy(
                update={
                    "source": ProviderSource.CACHE,
                    "verified_at": now,
                    "cache_age_seconds": int((now - cached.stored_at).total_seconds()),
                }
            )

        failed_at = self._cache.failures.get(key)
        if failed_at is not None and now - failed_at <= self._failure_ttl:
            return self._fallback_fact(request, now, "failure_cache_active")

        try:
            fact = self._primary.get_weather(request)
        except Exception:
            self._cache.failures[key] = now
            if cached is not None:
                return cached.fact.model_copy(
                    update={
                        "source": ProviderSource.CACHE,
                        "verified_at": now,
                        "degraded": True,
                        "degraded_reason": "upstream_failed_using_stale_cache",
                        "cache_age_seconds": int((now - cached.stored_at).total_seconds()),
                    }
                )
            return self._fallback_fact(request, now, "upstream_failed_using_fallback")

        self._cache.successes[key] = _CacheEntry(fact=fact.model_copy(deep=True), stored_at=now)
        self._cache.failures.pop(key, None)
        return fact

    def _fallback_fact(
        self,
        request: WeatherRequest,
        now: datetime,
        reason: str,
    ) -> WeatherFact:
        return self._fallback.get_weather(request).model_copy(
            update={
                "verified_at": now,
                "degraded": True,
                "degraded_reason": reason,
                "cache_age_seconds": None,
            }
        )


def clear_mock_weather(*, now: datetime | None = None) -> MockWeatherProvider:
    timestamp = now or datetime.now(timezone.utc)
    return MockWeatherProvider(
        WeatherFact(
            city="北京市",
            district="朝阳区",
            date=timestamp.date(),
            condition="晴",
            temperature_c=25,
            precipitation_mm=0,
            is_adverse=False,
            source=ProviderSource.MOCK,
            mode=ProviderMode.MOCK,
            observed_at=timestamp,
            verified_at=timestamp,
        )
    )


def build_weather_provider(
    *,
    mode: ProviderMode,
    replay_path: Path,
    api_key: str | None = None,
    mock_condition: str = "晴",
    clock: Callable[[], datetime] | None = None,
) -> WeatherProvider:
    """按后端配置组装天气 Adapter；默认路径完全离线。"""
    current_time = (clock or (lambda: datetime.now(timezone.utc)))()
    mock = MockWeatherProvider(
        WeatherFact(
            city="北京市",
            district="朝阳区",
            date=current_time.date(),
            condition=mock_condition,
            temperature_c=25,
            precipitation_mm=3 if _is_adverse(mock_condition) else 0,
            is_adverse=_is_adverse(mock_condition),
            source=ProviderSource.MOCK,
            mode=ProviderMode.MOCK,
            observed_at=current_time,
            verified_at=current_time,
        )
    )
    if mode == ProviderMode.MOCK:
        return mock

    store = JsonWeatherReplayStore(replay_path)
    replay = ReplayWeatherProvider(store=store, clock=clock)
    if mode == ProviderMode.REPLAY:
        return replay

    if not api_key:
        raise RuntimeError(
            f"weather provider mode {mode.value!r} requires AMAP_WEB_SERVICE_KEY"
        )
    live: WeatherProvider = AmapWeatherProvider(api_key=api_key, clock=clock)
    if mode == ProviderMode.RECORD:
        live = RecordingWeatherProvider(delegate=live, store=store)
    fallback = ReplayThenMockWeatherProvider(replay=replay, mock=mock)
    return ResilientWeatherProvider(
        primary=live,
        fallback=fallback,
        cache=InMemoryWeatherCache(),
        success_ttl=timedelta(minutes=45),
        failure_ttl=timedelta(seconds=45),
        clock=clock,
    )


def _is_adverse(condition: str) -> bool:
    return any(marker in condition for marker in ("雨", "雪", "冰雹", "沙尘", "雷"))


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_amap_time(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            return parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            pass
    return fallback

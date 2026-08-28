"""Route providers, replay storage, cache and auditable fallback behavior."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from app.providers.amap import AmapResponseError, require_amap_success
from app.domain.providers import (
    GeoPoint,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteRequest,
)
from services.feasibility_service import estimate_route


class RouteProvider(Protocol):
    def route(self, request: RouteRequest) -> RouteFact:
        ...


class RouteReplayStore(Protocol):
    def save(self, request: RouteRequest, fact: RouteFact) -> None:
        ...

    def load(self, request: RouteRequest) -> RouteFact:
        ...


class LocalEstimateRouteProvider:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def route(self, request: RouteRequest) -> RouteFact:
        estimate = estimate_route(
            {
                "lat": request.origin.latitude,
                "lng": request.origin.longitude,
                "address": "",
            },
            {
                "lat": request.destination.latitude,
                "lng": request.destination.longitude,
                "address": "",
            },
            mode=request.mode.value,
        )
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            distance_km=estimate["distance_km"],
            duration_minutes=max(1, estimate["duration_minutes"]),
            geometry=[request.origin, request.destination],
            source=ProviderSource.LOCAL_ESTIMATE,
            provider_mode=ProviderMode.MOCK,
            verified_at=self._clock(),
            degraded=True,
            degraded_reason="configured_local_estimate",
        )


class InMemoryRouteReplayStore:
    def __init__(self) -> None:
        self._facts: dict[str, RouteFact] = {}

    def save(self, request: RouteRequest, fact: RouteFact) -> None:
        self._facts[request.cache_key] = fact.model_copy(deep=True)

    def load(self, request: RouteRequest) -> RouteFact:
        try:
            return self._facts[request.cache_key].model_copy(deep=True)
        except KeyError as error:
            raise LookupError(f"route replay not found: {request.cache_key}") from error


class JsonRouteReplayStore:
    """Persist normalized route facts only; credentials never enter fixtures."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, request: RouteRequest, fact: RouteFact) -> None:
        payload = self._read_all()
        payload[request.cache_key] = fact.model_dump(mode="json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, request: RouteRequest) -> RouteFact:
        payload = self._read_all()
        try:
            return RouteFact.model_validate(payload[request.cache_key])
        except KeyError as error:
            raise LookupError(f"route replay not found: {request.cache_key}") from error

    def _read_all(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("route replay fixture must be a JSON object")
        return value


class RecordingRouteProvider:
    def __init__(self, *, delegate: RouteProvider, store: RouteReplayStore) -> None:
        self._delegate = delegate
        self._store = store

    def route(self, request: RouteRequest) -> RouteFact:
        fact = self._delegate.route(request).model_copy(
            update={"provider_mode": ProviderMode.RECORD}
        )
        self._store.save(request, fact)
        return fact


class ReplayRouteProvider:
    def __init__(
        self,
        *,
        store: RouteReplayStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def route(self, request: RouteRequest) -> RouteFact:
        return self._store.load(request).model_copy(
            update={
                "source": ProviderSource.REPLAY,
                "provider_mode": ProviderMode.REPLAY,
                "verified_at": self._clock(),
                "degraded": False,
                "degraded_reason": None,
                "cache_age_seconds": None,
            }
        )


class ReplayThenLocalRouteProvider:
    def __init__(self, *, replay: RouteProvider, local: RouteProvider) -> None:
        self._replay = replay
        self._local = local

    def route(self, request: RouteRequest) -> RouteFact:
        try:
            return self._replay.route(request)
        except LookupError:
            return self._local.route(request)


class AmapRouteProvider:
    """Amap Route Planning 2.0 driving adapter for finalist verification."""

    def __init__(
        self,
        *,
        api_key: str,
        fetch_json: Callable[[str], dict] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Amap route provider requires a backend API key")
        self._api_key = api_key
        self._fetch_json = fetch_json or self._default_fetch_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def route(self, request: RouteRequest) -> RouteFact:
        if request.mode.value != "taxi":
            raise RuntimeError("amap route mode is not supported")
        query = urlencode(
            {
                "key": self._api_key,
                "origin": _amap_point(request.origin),
                "destination": _amap_point(request.destination),
                "strategy": "32",
                "show_fields": "cost,polyline",
            }
        )
        try:
            payload = self._fetch_json(
                f"https://restapi.amap.com/v5/direction/driving?{query}"
            )
            require_amap_success(payload, operation="route")
            path = payload["route"]["paths"][0]
            distance_km = float(path["distance"]) / 1000
            duration_minutes = max(
                1,
                math.ceil(float(path["cost"]["duration"]) / 60),
            )
            geometry = _parse_geometry(path.get("steps", []))
        except AmapResponseError:
            raise
        except Exception as error:
            raise RuntimeError("amap route request failed") from error
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            geometry=geometry or [request.origin, request.destination],
            source=ProviderSource.AMAP_LIVE,
            provider_mode=ProviderMode.LIVE,
            verified_at=self._clock(),
        )

    @staticmethod
    def _default_fetch_json(url: str) -> dict:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed HTTPS endpoint
            return json.loads(response.read().decode("utf-8"))


@dataclass
class _CacheEntry:
    fact: RouteFact
    stored_at: datetime


class InMemoryRouteCache:
    def __init__(self) -> None:
        self.successes: dict[str, _CacheEntry] = {}
        self.failures: dict[str, datetime] = {}


class ResilientRouteProvider:
    def __init__(
        self,
        *,
        primary: RouteProvider,
        fallback: RouteProvider,
        cache: InMemoryRouteCache,
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

    def route(self, request: RouteRequest) -> RouteFact:
        now = self._clock()
        key = request.cache_key
        cached = self._cache.successes.get(key)
        if cached is not None and now - cached.stored_at <= self._success_ttl:
            return cached.fact.model_copy(
                update={
                    "source": ProviderSource.CACHE,
                    "verified_at": now,
                    "cache_age_seconds": int(
                        (now - cached.stored_at).total_seconds()
                    ),
                }
            )
        failed_at = self._cache.failures.get(key)
        if failed_at is not None and now - failed_at <= self._failure_ttl:
            return self._fallback_fact(request, now, "failure_cache_active")
        try:
            fact = self._primary.route(request)
        except Exception:
            self._cache.failures[key] = now
            if cached is not None:
                return cached.fact.model_copy(
                    update={
                        "source": ProviderSource.CACHE,
                        "verified_at": now,
                        "degraded": True,
                        "degraded_reason": "upstream_failed_using_stale_cache",
                        "cache_age_seconds": int(
                            (now - cached.stored_at).total_seconds()
                        ),
                    }
                )
            return self._fallback_fact(
                request,
                now,
                "upstream_failed_using_local_estimate",
            )
        self._cache.successes[key] = _CacheEntry(
            fact=fact.model_copy(deep=True),
            stored_at=now,
        )
        self._cache.failures.pop(key, None)
        return fact

    def _fallback_fact(
        self,
        request: RouteRequest,
        now: datetime,
        reason: str,
    ) -> RouteFact:
        fact = self._fallback.route(request)
        if (
            reason == "upstream_failed_using_local_estimate"
            and fact.source == ProviderSource.REPLAY
        ):
            reason = "upstream_failed_using_replay"
        return fact.model_copy(
            update={
                "verified_at": now,
                "degraded": True,
                "degraded_reason": reason,
                "cache_age_seconds": None,
            }
        )


def build_route_provider(
    *,
    mode: ProviderMode,
    replay_path: Path,
    api_key: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RouteProvider:
    local = LocalEstimateRouteProvider(clock=clock)
    if mode == ProviderMode.MOCK:
        return local
    store = JsonRouteReplayStore(replay_path)
    replay = ReplayRouteProvider(store=store, clock=clock)
    if mode == ProviderMode.REPLAY:
        return replay
    if not api_key:
        raise RuntimeError(
            f"route provider mode {mode.value!r} requires AMAP_WEB_SERVICE_KEY"
        )
    live: RouteProvider = AmapRouteProvider(api_key=api_key, clock=clock)
    if mode == ProviderMode.RECORD:
        live = RecordingRouteProvider(delegate=live, store=store)
    fallback = ReplayThenLocalRouteProvider(replay=replay, local=local)
    return ResilientRouteProvider(
        primary=live,
        fallback=fallback,
        cache=InMemoryRouteCache(),
        success_ttl=timedelta(minutes=20),
        failure_ttl=timedelta(seconds=45),
        clock=clock,
    )


def _amap_point(point: GeoPoint) -> str:
    return f"{point.longitude},{point.latitude}"


def _parse_geometry(steps: list[dict]) -> list[GeoPoint]:
    points: list[GeoPoint] = []
    for step in steps:
        for value in str(step.get("polyline", "")).split(";"):
            if not value:
                continue
            longitude, latitude = value.split(",")
            points.append(
                GeoPoint(latitude=float(latitude), longitude=float(longitude))
            )
    return points

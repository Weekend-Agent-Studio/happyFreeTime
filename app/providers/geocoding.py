"""Geocoding adapters with a small normalized fact interface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from app.domain.providers import (
    GeoPoint,
    GeocodingPlace,
    GeocodeRequest,
    GeocodeResolution,
    GeocodingFact,
    PlaceSearchRequest,
    PlaceSearchResult,
    ProviderMode,
    ProviderSource,
)
from app.providers.amap import AmapResponseError, require_amap_success


class GeocodingProvider(Protocol):
    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        ...

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        ...

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        ...


class GeocodeReplayStore(Protocol):
    def save(self, request: GeocodeRequest, fact: GeocodingFact) -> None:
        ...

    def load(self, request: GeocodeRequest) -> GeocodingFact:
        ...


class MockGeocodingProvider:
    def __init__(
        self,
        locations: dict[tuple[str, str], list[tuple[float, float, str, str, str]]],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._locations = locations
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._places: dict[str, GeocodingPlace] = {}
        for (city, name), matches in locations.items():
            for index, (latitude, longitude, district, adcode, address) in enumerate(matches):
                identity = "\0".join((city, name, str(index), str(latitude), str(longitude), address))
                provider_id = f"mock:{sha256(identity.encode('utf-8')).hexdigest()[:24]}"
                self._places[provider_id] = GeocodingPlace(
                    provider_id=provider_id,
                    name=name,
                    city=city,
                    district=district,
                    address=address,
                    point=GeoPoint(latitude=latitude, longitude=longitude),
                    adcode=adcode or None,
                )

    @classmethod
    def from_locations(
        cls,
        locations: dict[tuple[str, str], tuple[float, float, str, str, str] | list[tuple[float, float, str, str, str]]],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> "MockGeocodingProvider":
        normalized = {
            key: value if isinstance(value, list) else [value]
            for key, value in locations.items()
        }
        return cls(normalized, clock=clock)

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        now = self._clock()
        matches = self._locations.get(((request.city or "").strip(), request.location_text.strip()), [])
        if not matches:
            return _unresolved_fact(request, GeocodeResolution.NOT_FOUND, now, ProviderSource.MOCK, ProviderMode.MOCK, "no_mock_match")
        if len(matches) != 1:
            return _unresolved_fact(request, GeocodeResolution.AMBIGUOUS, now, ProviderSource.MOCK, ProviderMode.MOCK, "multiple_mock_matches")
        latitude, longitude, district, adcode, address = matches[0]
        return _resolved_fact(request, GeoPoint(latitude=latitude, longitude=longitude), request.city or "", district, adcode, address, now, ProviderSource.MOCK, ProviderMode.MOCK)

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        query = request.query.strip().casefold()
        places = tuple(
            place.model_copy(deep=True)
            for place in self._places.values()
            if place.city == request.city
            and any(query in text.casefold() for text in (place.name, place.district, place.address or ""))
        )
        return PlaceSearchResult(source=ProviderSource.MOCK, mode=ProviderMode.MOCK, places=places[:8])

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        place = self._places.get(provider_id)
        if place is None or place.city != "北京市":
            return None
        return place.model_copy(deep=True)


class InMemoryGeocodeReplayStore:
    def __init__(self) -> None:
        self._facts: dict[str, GeocodingFact] = {}

    def save(self, request: GeocodeRequest, fact: GeocodingFact) -> None:
        self._facts[request.cache_key] = fact.model_copy(deep=True)

    def load(self, request: GeocodeRequest) -> GeocodingFact:
        try:
            return self._facts[request.cache_key].model_copy(deep=True)
        except KeyError as error:
            raise LookupError(f"geocoding replay not found: {request.cache_key}") from error


class JsonGeocodeReplayStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, request: GeocodeRequest, fact: GeocodingFact) -> None:
        payload = self._read_all()
        payload[request.cache_key] = fact.model_dump(mode="json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, request: GeocodeRequest) -> GeocodingFact:
        try:
            return GeocodingFact.model_validate(self._read_all()[request.cache_key])
        except KeyError as error:
            raise LookupError(f"geocoding replay not found: {request.cache_key}") from error

    def _read_all(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("geocoding replay fixture must be a JSON object")
        return value


class RecordingGeocodingProvider:
    def __init__(self, *, delegate: GeocodingProvider, store: GeocodeReplayStore) -> None:
        self._delegate = delegate
        self._store = store

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        fact = self._delegate.geocode(request).model_copy(update={"mode": ProviderMode.RECORD})
        self._store.save(request, fact)
        return fact

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        return self._delegate.search_places(request).model_copy(update={"mode": ProviderMode.RECORD})

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        return self._delegate.resolve_place(provider_id)


class ReplayGeocodingProvider:
    def __init__(self, *, store: GeocodeReplayStore, clock: Callable[[], datetime] | None = None, place_provider: GeocodingProvider | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._place_provider = place_provider

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        return self._store.load(request).model_copy(update={"source": ProviderSource.REPLAY, "mode": ProviderMode.REPLAY, "verified_at": self._clock(), "degraded": False, "degraded_reason": None})

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        if self._place_provider is None:
            raise RuntimeError("replay place search is unavailable")
        return self._place_provider.search_places(request)

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        return self._place_provider.resolve_place(provider_id) if self._place_provider is not None else None


class ReplayThenMockGeocodingProvider:
    def __init__(self, *, replay: GeocodingProvider, mock: GeocodingProvider) -> None:
        self._replay, self._mock = replay, mock

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        try:
            return self._replay.geocode(request)
        except LookupError:
            return self._mock.geocode(request)

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        return self._mock.search_places(request)

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        if not provider_id.startswith("mock:"):
            return None
        return self._mock.resolve_place(provider_id)


class AmapGeocodingProvider:
    def __init__(self, *, api_key: str, fetch_json: Callable[[str], dict] | None = None, clock: Callable[[], datetime] | None = None) -> None:
        if not api_key:
            raise ValueError("Amap geocoding provider requires a backend API key")
        self._api_key = api_key
        self._fetch_json = fetch_json or self._default_fetch_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        query = urlencode({"key": self._api_key, "address": request.location_text, "city": request.city or ""})
        try:
            payload = self._fetch_json(f"https://restapi.amap.com/v3/geocode/geo?{query}")
            require_amap_success(payload, operation="geocoding")
            geocodes = payload.get("geocodes", [])
        except AmapResponseError:
            raise
        except Exception as error:
            raise RuntimeError("amap geocoding request failed") from error
        now = self._clock()
        if not geocodes:
            return _unresolved_fact(request, GeocodeResolution.NOT_FOUND, now, ProviderSource.AMAP_LIVE, ProviderMode.LIVE, "amap_empty_result")
        if len(geocodes) != 1:
            return _unresolved_fact(request, GeocodeResolution.AMBIGUOUS, now, ProviderSource.AMAP_LIVE, ProviderMode.LIVE, "amap_multiple_results")
        item = geocodes[0]
        try:
            longitude, latitude = str(item["location"]).split(",")
            city = request.city or str(item.get("city") or "")
            address = str(item.get("formatted_address") or "")
            if not city or not address:
                raise ValueError("missing normalized location fields")
            return _resolved_fact(request, GeoPoint(latitude=float(latitude), longitude=float(longitude)), city, str(item.get("district") or ""), str(item.get("adcode") or "") or None, address, now, ProviderSource.AMAP_LIVE, ProviderMode.LIVE)
        except Exception as error:
            raise RuntimeError("amap geocoding response was incomplete") from error

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        query = urlencode({
            "key": self._api_key,
            "keywords": request.query.strip(),
            "city": request.city,
            "citylimit": "true",
            "offset": 8,
            "page": 1,
            "extensions": "all",
        })
        try:
            payload = self._fetch_json(f"https://restapi.amap.com/v3/place/text?{query}")
            require_amap_success(payload, operation="place search")
            records = payload.get("pois", [])
            places = _amap_places(records if isinstance(records, list) else [])
        except AmapResponseError:
            raise
        except Exception as error:
            raise RuntimeError("amap place search failed") from error
        return PlaceSearchResult(
            source=ProviderSource.AMAP_LIVE,
            mode=ProviderMode.LIVE,
            places=tuple(places[:8]),
        )

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        if not provider_id.startswith("amap:"):
            return None
        amap_id = provider_id.removeprefix("amap:")
        if not amap_id or len(amap_id) > 100:
            return None
        query = urlencode({"key": self._api_key, "id": amap_id, "extensions": "all"})
        try:
            payload = self._fetch_json(f"https://restapi.amap.com/v3/place/detail?{query}")
            require_amap_success(payload, operation="place detail")
            records = payload.get("pois", [])
            if not isinstance(records, list) or len(records) != 1:
                return None
            place = _amap_place(records[0])
            return place if place is not None and place.provider_id == provider_id else None
        except AmapResponseError:
            raise
        except Exception as error:
            raise RuntimeError("amap place detail failed") from error

    @staticmethod
    def _default_fetch_json(url: str) -> dict:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed HTTPS endpoint
            return json.loads(response.read().decode("utf-8"))


def _amap_places(records: list[object]) -> list[GeocodingPlace]:
    places: list[GeocodingPlace] = []
    seen: set[str] = set()
    for record in records:
        place = _amap_place(record)
        if place is not None and place.provider_id not in seen:
            seen.add(place.provider_id)
            places.append(place)
    return places


def _amap_place(record: object) -> GeocodingPlace | None:
    if not isinstance(record, dict):
        return None
    provider_place_id = str(record.get("id") or "").strip()
    name = str(record.get("name") or "").strip()
    location = str(record.get("location") or "").strip()
    if not provider_place_id or not name or not location:
        return None
    city = str(record.get("cityname") or record.get("city") or record.get("pname") or "").strip()
    if city == "北京":
        city = "北京市"
    adcode = str(record.get("adcode") or "").strip() or None
    if city != "北京市" or (adcode is not None and not adcode.startswith("11")):
        return None
    try:
        longitude, latitude = location.split(",", maxsplit=1)
        point = GeoPoint(latitude=float(latitude), longitude=float(longitude))
    except (ValueError, TypeError):
        return None
    district = str(record.get("adname") or record.get("district") or "").strip()
    address = str(record.get("address") or "").strip() or None
    if not district and not address:
        return None
    return GeocodingPlace(
        provider_id=f"amap:{provider_place_id}",
        name=name,
        city=city,
        district=district,
        address=address,
        point=point,
        adcode=adcode,
    )


@dataclass
class _CacheEntry:
    fact: GeocodingFact
    stored_at: datetime


class InMemoryGeocodeCache:
    def __init__(self) -> None:
        self.successes: dict[str, _CacheEntry] = {}
        self.failures: dict[str, datetime] = {}


class ResilientGeocodingProvider:
    def __init__(self, *, primary: GeocodingProvider, fallback: GeocodingProvider, cache: InMemoryGeocodeCache, success_ttl: timedelta, failure_ttl: timedelta, clock: Callable[[], datetime] | None = None) -> None:
        self._primary, self._fallback, self._cache = primary, fallback, cache
        self._success_ttl, self._failure_ttl = success_ttl, failure_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        now, key = self._clock(), request.cache_key
        cached = self._cache.successes.get(key)
        if cached and now - cached.stored_at <= self._success_ttl:
            return cached.fact.model_copy(update={"source": ProviderSource.CACHE, "verified_at": now, "cache_age_seconds": int((now - cached.stored_at).total_seconds())})
        failed_at = self._cache.failures.get(key)
        if failed_at and now - failed_at <= self._failure_ttl:
            return self._fallback_fact(request, now, "failure_cache_active")
        try:
            fact = self._primary.geocode(request)
        except Exception:
            self._cache.failures[key] = now
            if cached:
                return cached.fact.model_copy(update={"source": ProviderSource.CACHE, "verified_at": now, "degraded": True, "degraded_reason": "upstream_failed_using_stale_cache", "cache_age_seconds": int((now - cached.stored_at).total_seconds())})
            return self._fallback_fact(request, now, "upstream_failed_using_fallback")
        if fact.resolution == GeocodeResolution.RESOLVED:
            self._cache.successes[key] = _CacheEntry(fact=fact.model_copy(deep=True), stored_at=now)
        self._cache.failures.pop(key, None)
        return fact

    def search_places(self, request: PlaceSearchRequest) -> PlaceSearchResult:
        try:
            return self._primary.search_places(request)
        except Exception:
            fallback = self._fallback.search_places(request)
            return fallback.model_copy(update={
                "degraded": True,
                "degraded_reason": "upstream_failed_using_demo_places",
            })

    def resolve_place(self, provider_id: str) -> GeocodingPlace | None:
        if provider_id.startswith("mock:"):
            return self._fallback.resolve_place(provider_id)
        if not provider_id.startswith("amap:"):
            return None
        return self._primary.resolve_place(provider_id)

    def _fallback_fact(self, request: GeocodeRequest, now: datetime, reason: str) -> GeocodingFact:
        return self._fallback.geocode(request).model_copy(update={"verified_at": now, "degraded": True, "degraded_reason": reason, "cache_age_seconds": None})


def _resolved_fact(request: GeocodeRequest, point: GeoPoint, city: str, district: str, adcode: str | None, address: str, now: datetime, source: ProviderSource, mode: ProviderMode) -> GeocodingFact:
    return GeocodingFact(request=request, resolution=GeocodeResolution.RESOLVED, point=point, city=city, district=district, adcode=adcode, address=address, source=source, mode=mode, observed_at=now, verified_at=now, verified=True)


def _unresolved_fact(request: GeocodeRequest, resolution: GeocodeResolution, now: datetime, source: ProviderSource, mode: ProviderMode, reason: str) -> GeocodingFact:
    return GeocodingFact(request=request, resolution=resolution, source=source, mode=mode, observed_at=now, verified_at=now, verified=False, degraded=resolution == GeocodeResolution.UNAVAILABLE, degraded_reason=reason if resolution == GeocodeResolution.UNAVAILABLE else None)


def build_geocoding_provider(
    *,
    mode: ProviderMode,
    replay_path: Path,
    api_key: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GeocodingProvider:
    """Build an offline-first provider; real calls are opt-in and replayable."""
    mock = MockGeocodingProvider.from_locations(
        {
            ("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸"),
            ("北京市", "海淀黄庄"): (39.9756, 116.3176, "海淀区", "110108", "北京市海淀区黄庄"),
        },
        clock=clock,
    )
    if mode == ProviderMode.MOCK:
        return mock
    store = JsonGeocodeReplayStore(replay_path)
    replay = ReplayGeocodingProvider(store=store, clock=clock, place_provider=mock)
    if mode == ProviderMode.REPLAY:
        return replay
    if not api_key:
        raise RuntimeError(f"geocoding provider mode {mode.value!r} requires AMAP_WEB_SERVICE_KEY")
    live: GeocodingProvider = AmapGeocodingProvider(api_key=api_key, clock=clock)
    if mode == ProviderMode.RECORD:
        live = RecordingGeocodingProvider(delegate=live, store=store)
    return ResilientGeocodingProvider(
        primary=live,
        fallback=ReplayThenMockGeocodingProvider(replay=replay, mock=mock),
        cache=InMemoryGeocodeCache(),
        success_ttl=timedelta(days=7),
        failure_ttl=timedelta(seconds=45),
        clock=clock,
    )

import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from app.domain.providers import (
    GeocodeRequest,
    GeocodeResolution,
    PlaceSearchRequest,
    ProviderMode,
    ProviderSource,
)
from app.providers.geocoding import (
    AmapGeocodingProvider,
    InMemoryGeocodeCache,
    InMemoryGeocodeReplayStore,
    MockGeocodingProvider,
    RecordingGeocodingProvider,
    ReplayGeocodingProvider,
    ResilientGeocodingProvider,
)


NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
REQUEST = GeocodeRequest(location_text="国贸", city="北京市")


class StubGeocodingProvider:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = 0

    def geocode(self, request):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FailingPlaceProvider(StubGeocodingProvider):
    def search_places(self, request):
        raise RuntimeError("upstream unavailable")

    def resolve_place(self, provider_id):
        raise RuntimeError("upstream unavailable")


class GeocodingProviderContractTest(unittest.TestCase):
    def test_mock_replay_and_record_keep_normalized_fact_offline(self) -> None:
        mock = MockGeocodingProvider.from_locations(
            {("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸")},
            clock=lambda: NOW,
        )
        store = InMemoryGeocodeReplayStore()
        recorded = RecordingGeocodingProvider(delegate=mock, store=store).geocode(REQUEST)
        replayed = ReplayGeocodingProvider(store=store, clock=lambda: NOW).geocode(REQUEST)

        self.assertEqual(recorded.resolution, GeocodeResolution.RESOLVED)
        self.assertEqual(replayed.point, recorded.point)
        self.assertEqual(replayed.source, ProviderSource.REPLAY)
        self.assertEqual(replayed.mode, ProviderMode.REPLAY)
        self.assertTrue(replayed.verified)

    def test_explicit_unknown_and_ambiguous_location_have_deterministic_results(self) -> None:
        provider = MockGeocodingProvider.from_locations(
            {("北京市", "万达"): [
                (39.9, 116.4, "朝阳区", "110105", "万达 A"),
                (39.8, 116.3, "海淀区", "110108", "万达 B"),
            ]},
            clock=lambda: NOW,
        )

        unknown = provider.geocode(GeocodeRequest(location_text="不存在地标", city="北京市"))
        ambiguous = provider.geocode(GeocodeRequest(location_text="万达", city="北京市"))

        self.assertEqual(unknown.resolution, GeocodeResolution.NOT_FOUND)
        self.assertIsNone(unknown.point)
        self.assertEqual(ambiguous.resolution, GeocodeResolution.AMBIGUOUS)
        self.assertIsNone(ambiguous.point)

    def test_live_adapter_maps_amap_response_without_exposing_key(self) -> None:
        urls = []
        provider = AmapGeocodingProvider(
            api_key="backend-secret",
            fetch_json=lambda url: urls.append(url) or {
                "status": "1",
                "geocodes": [{
                    "location": "116.4615,39.9087",
                    "district": "朝阳区",
                    "adcode": "110105",
                    "formatted_address": "北京市朝阳区国贸",
                }],
            },
            clock=lambda: NOW,
        )

        fact = provider.geocode(REQUEST)

        self.assertEqual(fact.resolution, GeocodeResolution.RESOLVED)
        self.assertEqual(fact.adcode, "110105")
        self.assertIn("address=%E5%9B%BD%E8%B4%B8", urls[0])
        self.assertNotIn("backend-secret", fact.model_dump_json())

    def test_live_missing_key_empty_and_ambiguous_results_are_deterministic(self) -> None:
        with self.assertRaises(ValueError):
            AmapGeocodingProvider(api_key="")
        for geocodes, expected in (([], GeocodeResolution.NOT_FOUND), ([{"location": "116.4,39.9"}, {"location": "116.5,39.8"}], GeocodeResolution.AMBIGUOUS)):
            provider = AmapGeocodingProvider(
                api_key="backend-secret",
                fetch_json=lambda _, values=geocodes: {"status": "1", "geocodes": values},
                clock=lambda: NOW,
            )
            self.assertEqual(provider.geocode(REQUEST).resolution, expected)

    def test_resilient_provider_uses_degraded_fallback_and_short_failure_cache(self) -> None:
        fallback = MockGeocodingProvider.from_locations(
            {("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸")},
            clock=lambda: NOW,
        )
        primary = StubGeocodingProvider([RuntimeError("network")])
        provider = ResilientGeocodingProvider(
            primary=primary,
            fallback=fallback,
            cache=InMemoryGeocodeCache(),
            success_ttl=timedelta(minutes=30),
            failure_ttl=timedelta(seconds=30),
            clock=lambda: NOW,
        )

        first = provider.geocode(REQUEST)
        second = provider.geocode(REQUEST)

        self.assertTrue(first.degraded)
        self.assertEqual(first.degraded_reason, "upstream_failed_using_fallback")
        self.assertEqual(second.degraded_reason, "failure_cache_active")
        self.assertEqual(primary.calls, 1)

    def test_mock_search_has_stable_identity_and_only_returns_configured_beijing_places(self) -> None:
        locations = {
            ("北京市", "万达广场"): [
                (39.9, 116.4, "朝阳区", "110105", "朝阳区一号路 1 号"),
                (39.8, 116.3, "海淀区", "110108", "海淀区二号路 2 号"),
            ],
            ("上海市", "万达广场"): (31.2, 121.4, "黄浦区", "310101", "黄浦区一号路"),
        }
        first = MockGeocodingProvider.from_locations(locations)
        second = MockGeocodingProvider.from_locations(locations)

        result = first.search_places(PlaceSearchRequest(query="  万达  ", city="北京市"))

        self.assertEqual(result.mode, ProviderMode.MOCK)
        self.assertEqual(len(result.places), 2)
        self.assertEqual([place.provider_id for place in result.places], [
            place.provider_id for place in second.search_places(PlaceSearchRequest(query="万达", city="北京市")).places
        ])
        self.assertEqual({place.district for place in result.places}, {"朝阳区", "海淀区"})
        self.assertNotEqual(result.places[0].point, result.places[1].point)
        self.assertIsNone(first.resolve_place("mock:unknown"))

    def test_amap_search_normalizes_only_resolvable_beijing_pois_and_detail_resolves_by_id(self) -> None:
        urls = []
        records = [
            {"id": "station-a", "name": "同名地铁站", "cityname": "北京市", "adname": "朝阳区", "address": "建国门外大街 1 号", "location": "116.4615,39.9087", "adcode": "110105"},
            {"id": "station-b", "name": "同名地铁站", "cityname": "北京市", "adname": "海淀区", "address": "中关村大街 1 号", "location": "116.3176,39.9756", "adcode": "110108"},
            {"id": "no-coordinate", "name": "没有坐标", "cityname": "北京市", "adname": "朝阳区", "address": "某路"},
            {"id": "outside", "name": "外地地点", "cityname": "上海市", "adname": "黄浦区", "address": "某路", "location": "121.4,31.2", "adcode": "310101"},
        ]

        def fetch(url):
            urls.append(url)
            query = parse_qs(urlparse(url).query)
            if urlparse(url).path.endswith("/text"):
                return {"status": "1", "pois": records}
            return {"status": "1", "pois": [records[0]] if query.get("id") == ["station-a"] else []}

        provider = AmapGeocodingProvider(api_key="backend-secret", fetch_json=fetch, clock=lambda: NOW)
        result = provider.search_places(PlaceSearchRequest(query="同名地铁站", city="北京市"))

        self.assertEqual(len(result.places), 2)
        self.assertEqual([place.provider_id for place in result.places], ["amap:station-a", "amap:station-b"])
        self.assertEqual(result.places[0].point.latitude, 39.9087)
        self.assertEqual(parse_qs(urlparse(urls[0]).query)["citylimit"], ["true"])
        self.assertEqual(parse_qs(urlparse(urls[0]).query)["city"], ["北京市"])
        self.assertEqual(provider.resolve_place("amap:station-a"), result.places[0])
        self.assertIsNone(provider.resolve_place("amap:unknown"))
        self.assertNotIn("backend-secret", str(result.model_dump(mode="json")))

    def test_resilient_place_search_marks_demo_fallback_and_never_resolves_unknown_live_ids_through_it(self) -> None:
        fallback = MockGeocodingProvider.from_locations(
            {("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸")},
            clock=lambda: NOW,
        )
        provider = ResilientGeocodingProvider(
            primary=FailingPlaceProvider([RuntimeError("unused")]),
            fallback=fallback,
            cache=InMemoryGeocodeCache(),
            success_ttl=timedelta(minutes=1),
            failure_ttl=timedelta(seconds=30),
            clock=lambda: NOW,
        )

        result = provider.search_places(PlaceSearchRequest(query="国贸", city="北京市"))

        self.assertTrue(result.degraded)
        self.assertEqual(result.degraded_reason, "upstream_failed_using_demo_places")
        self.assertTrue(result.places[0].provider_id.startswith("mock:"))
        self.assertEqual(provider.resolve_place(result.places[0].provider_id), result.places[0])
        with self.assertRaises(RuntimeError):
            provider.resolve_place("amap:unavailable-live-place")


if __name__ == "__main__":
    unittest.main()

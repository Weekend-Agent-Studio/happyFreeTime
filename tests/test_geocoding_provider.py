import unittest
from datetime import datetime, timedelta, timezone

from app.domain.providers import (
    GeocodeRequest,
    GeocodeResolution,
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


if __name__ == "__main__":
    unittest.main()

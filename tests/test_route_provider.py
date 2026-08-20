import unittest
from datetime import datetime, timedelta, timezone

from app.domain.providers import (
    GeoPoint,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteMode,
    RouteRequest,
)
from app.providers.route import (
    AmapRouteProvider,
    InMemoryRouteCache,
    InMemoryRouteReplayStore,
    LocalEstimateRouteProvider,
    RecordingRouteProvider,
    ReplayRouteProvider,
    ReplayThenLocalRouteProvider,
    ResilientRouteProvider,
)


NOW = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
REQUEST = RouteRequest(
    origin=GeoPoint(latitude=39.9219, longitude=116.4436),
    destination=GeoPoint(latitude=39.9830, longitude=116.3175),
    mode=RouteMode.TAXI,
    departure_at=datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc),
)


def route_fact(
    *,
    source: ProviderSource = ProviderSource.AMAP_LIVE,
    mode: ProviderMode = ProviderMode.LIVE,
) -> RouteFact:
    return RouteFact(
        origin=REQUEST.origin,
        destination=REQUEST.destination,
        mode=REQUEST.mode,
        distance_km=4.2,
        duration_minutes=20,
        geometry=[REQUEST.origin, REQUEST.destination],
        source=source,
        provider_mode=mode,
        verified_at=NOW,
    )


class StubRouteProvider:
    def __init__(self, outcomes: list[RouteFact | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def route(self, request: RouteRequest) -> RouteFact:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class RouteProviderContractTest(unittest.TestCase):
    def test_live_adapter_parses_amap_v5_route_without_exposing_key(self) -> None:
        urls: list[str] = []

        def fetch_json(url: str) -> dict:
            urls.append(url)
            return {
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "4200",
                            "cost": {"duration": "1200"},
                            "steps": [
                                {"polyline": "116.4436,39.9219;116.3175,39.9830"}
                            ],
                        }
                    ]
                },
            }

        fact = AmapRouteProvider(
            api_key="backend-secret",
            fetch_json=fetch_json,
            clock=lambda: NOW,
        ).route(REQUEST)

        self.assertEqual(fact.distance_km, 4.2)
        self.assertEqual(fact.duration_minutes, 20)
        self.assertEqual(fact.source, ProviderSource.AMAP_LIVE)
        self.assertEqual(fact.provider_mode, ProviderMode.LIVE)
        self.assertEqual(len(fact.geometry), 2)
        self.assertIn("/v5/direction/driving", urls[0])
        self.assertIn("origin=116.4436%2C39.9219", urls[0])
        self.assertIn("show_fields=cost%2Cpolyline", urls[0])
        self.assertNotIn("backend-secret", fact.model_dump_json())

    def test_record_and_replay_preserve_route_observation(self) -> None:
        store = InMemoryRouteReplayStore()
        recorded = RecordingRouteProvider(
            delegate=StubRouteProvider([route_fact()]),
            store=store,
        ).route(REQUEST)
        replayed = ReplayRouteProvider(store=store, clock=lambda: NOW).route(REQUEST)

        self.assertEqual(recorded.distance_km, replayed.distance_km)
        self.assertEqual(recorded.duration_minutes, replayed.duration_minutes)
        self.assertEqual(recorded.geometry, replayed.geometry)
        self.assertEqual(recorded.provider_mode, ProviderMode.RECORD)
        self.assertEqual(replayed.provider_mode, ProviderMode.REPLAY)
        self.assertEqual(replayed.source, ProviderSource.REPLAY)
        self.assertFalse(replayed.degraded)

    def test_success_cache_honors_ttl_and_exposes_age(self) -> None:
        clock = MutableClock()
        primary = StubRouteProvider([route_fact()])
        provider = ResilientRouteProvider(
            primary=primary,
            fallback=LocalEstimateRouteProvider(clock=clock),
            cache=InMemoryRouteCache(),
            success_ttl=timedelta(minutes=20),
            failure_ttl=timedelta(seconds=30),
            clock=clock,
        )

        first = provider.route(REQUEST)
        clock.now += timedelta(minutes=10)
        second = provider.route(REQUEST)

        self.assertEqual(first.source, ProviderSource.AMAP_LIVE)
        self.assertEqual(second.source, ProviderSource.CACHE)
        self.assertEqual(second.cache_age_seconds, 600)
        self.assertEqual(primary.calls, 1)

        clock.now += timedelta(minutes=11)
        provider.route(REQUEST)
        self.assertEqual(primary.calls, 2)

    def test_failure_is_short_cached_and_uses_local_estimate_with_reason(self) -> None:
        clock = MutableClock()
        primary = StubRouteProvider([RuntimeError("upstream timeout")])
        provider = ResilientRouteProvider(
            primary=primary,
            fallback=LocalEstimateRouteProvider(clock=clock),
            cache=InMemoryRouteCache(),
            success_ttl=timedelta(minutes=20),
            failure_ttl=timedelta(seconds=30),
            clock=clock,
        )

        first = provider.route(REQUEST)
        second = provider.route(REQUEST)

        self.assertTrue(first.degraded)
        self.assertEqual(first.source, ProviderSource.LOCAL_ESTIMATE)
        self.assertEqual(first.degraded_reason, "upstream_failed_using_local_estimate")
        self.assertEqual(second.degraded_reason, "failure_cache_active")
        self.assertEqual(primary.calls, 1)

        clock.now += timedelta(seconds=31)
        provider.route(REQUEST)
        self.assertEqual(primary.calls, 2)

    def test_live_failure_uses_stale_cache_before_other_fallbacks(self) -> None:
        clock = MutableClock()
        primary = StubRouteProvider([route_fact(), RuntimeError("upstream timeout")])
        provider = ResilientRouteProvider(
            primary=primary,
            fallback=LocalEstimateRouteProvider(clock=clock),
            cache=InMemoryRouteCache(),
            success_ttl=timedelta(minutes=20),
            failure_ttl=timedelta(seconds=30),
            clock=clock,
        )
        provider.route(REQUEST)
        clock.now += timedelta(minutes=21)

        fact = provider.route(REQUEST)

        self.assertEqual(fact.source, ProviderSource.CACHE)
        self.assertTrue(fact.degraded)
        self.assertEqual(
            fact.degraded_reason,
            "upstream_failed_using_stale_cache",
        )
        self.assertEqual(fact.cache_age_seconds, 1260)

    def test_live_failure_prefers_matching_replay_and_names_the_fallback(self) -> None:
        store = InMemoryRouteReplayStore()
        store.save(REQUEST, route_fact())
        replay = ReplayRouteProvider(store=store, clock=lambda: NOW)
        provider = ResilientRouteProvider(
            primary=StubRouteProvider([RuntimeError("upstream timeout")]),
            fallback=ReplayThenLocalRouteProvider(
                replay=replay,
                local=LocalEstimateRouteProvider(clock=lambda: NOW),
            ),
            cache=InMemoryRouteCache(),
            success_ttl=timedelta(minutes=20),
            failure_ttl=timedelta(seconds=30),
            clock=lambda: NOW,
        )

        fact = provider.route(REQUEST)

        self.assertEqual(fact.source, ProviderSource.REPLAY)
        self.assertTrue(fact.degraded)
        self.assertEqual(fact.degraded_reason, "upstream_failed_using_replay")


if __name__ == "__main__":
    unittest.main()

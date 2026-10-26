from datetime import timedelta
from types import SimpleNamespace

import app.providers.route as route_module
from app.domain.providers import GeoPoint
from app.providers.route import AmapRouteProvider, InMemoryRouteCache, LocalEstimateRouteProvider, ResilientRouteProvider
from tests.test_route_provider import NOW, REQUEST


def test_route_burst_does_not_wait_and_rate_limit_falls_back_honestly(monkeypatch, caplog):
    clock = SimpleNamespace(now=0.0)
    waits, starts = [], []

    def sleep(seconds):
        waits.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(route_module, "time", SimpleNamespace(
        perf_counter=lambda: clock.now, monotonic=lambda: clock.now, sleep=sleep))
    # Stale private deployment configuration must not restore the removed wait.
    monkeypatch.setenv("HFT_AMAP_ROUTE_QPS", "1")

    def upstream(_):
        recent = sum(clock.now - start < 1.0 for start in starts)
        starts.append(clock.now)
        clock.now += 0.035
        if recent >= 3:
            return {"status": "0", "infocode": "10021", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT"}
        return {"status": "1", "route": {"paths": [{"distance": "4200", "cost": {"duration": "1200"}, "steps": []}]}}

    provider = ResilientRouteProvider(
        primary=AmapRouteProvider(api_key="fixture", fetch_json=upstream, clock=lambda: NOW),
        fallback=LocalEstimateRouteProvider(clock=lambda: NOW), cache=InMemoryRouteCache(),
        success_ttl=timedelta(minutes=20), failure_ttl=timedelta(seconds=45), clock=lambda: NOW)
    requests = [REQUEST.model_copy(update={"destination": GeoPoint(latitude=39.983, longitude=116.3175 + i * 0.001)}) for i in range(6)]
    facts = [provider.route(request) for request in requests]
    assert waits == []
    assert len(starts) == 6  # No retry on rejection.
    assert clock.now < 0.22
    assert all(not fact.degraded for fact in facts[:3])
    assert all(fact.degraded and fact.degraded_reason == "upstream_failed_using_local_estimate" for fact in facts[3:])
    assert all(fact.source.value == "local_estimate" for fact in facts[3:])
    assert "code=10021" in caplog.text
    assert not provider.route(requests[0]).degraded
    assert provider.route(requests[-1]).degraded_reason == "failure_cache_active"
    assert len(starts) == 6  # Both cache paths bypass the upstream.

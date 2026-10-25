from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
import time

import pytest

import app.providers.route as route_module
from app.providers.route import AmapRouteProvider, ResilientRouteProvider, InMemoryRouteCache, LocalEstimateRouteProvider
from app.domain.providers import GeoPoint
from tests.test_route_provider import REQUEST, NOW


class SimulatedClock:
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def paced_scenario(monkeypatch, *, concurrent=False):
    clock = SimulatedClock()
    monkeypatch.setattr(route_module, "time", SimpleNamespace(monotonic=clock.monotonic, perf_counter=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setenv("HFT_AMAP_ROUTE_QPS", "1")
    starts = []
    def upstream(_):
        now = clock.now
        accepted = not starts or now - starts[-1] >= 1.0 - 1e-9
        starts.append(now)
        clock.now += 0.035
        if concurrent:
            time.sleep(0.005)  # Let another caller contend while HTTP is active.
        if not accepted:
            return {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10021"}
        return {"status": "1", "route": {"paths": [{"distance": "4200", "cost": {"duration": "1200"}, "steps": []}]}}
    provider = ResilientRouteProvider(
        primary=AmapRouteProvider(api_key="fixture", fetch_json=upstream, clock=lambda: NOW),
        fallback=LocalEstimateRouteProvider(clock=lambda: NOW), cache=InMemoryRouteCache(),
        success_ttl=timedelta(minutes=20), failure_ttl=timedelta(seconds=45), clock=lambda: NOW)
    requests = [REQUEST.model_copy(update={"destination": GeoPoint(latitude=39.983, longitude=116.3175 + index * 0.001)}) for index in range(6)]
    return provider, requests, starts, clock


def test_fast_route_burst_does_not_trigger_simulated_account_qps_rejection(monkeypatch):
    provider, requests, starts, _ = paced_scenario(monkeypatch)
    facts = [provider.route(request) for request in requests]
    assert all(not fact.degraded for fact in facts), "Fast burst caused 10021 and local route fallback"
    assert len(starts) == 6
    assert all(right - left >= 1.0 for left, right in zip(starts, starts[1:]))


def test_two_concurrent_callers_share_the_same_outbound_route_budget(monkeypatch):
    provider, requests, starts, _ = paced_scenario(monkeypatch, concurrent=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        facts = list(pool.map(provider.route, requests))
    assert all(not fact.degraded for fact in facts)
    assert len(starts) == 6
    assert all(right - left >= 1.0 for left, right in zip(starts, starts[1:]))


def test_success_cache_needs_no_outbound_slot_or_wait(monkeypatch):
    provider, requests, starts, clock = paced_scenario(monkeypatch)
    first = provider.route(requests[0])
    before = clock.now
    cached = provider.route(requests[0])
    assert not first.degraded and not cached.degraded
    assert cached.source.value == "cache"
    assert len(starts) == 1 and clock.now == before


def test_failed_outbound_request_still_leaves_a_full_interval(monkeypatch):
    provider, requests, starts, clock = paced_scenario(monkeypatch)
    # Simulate an account already busy outside this process for its first call.
    starts.append(-0.5)
    failed = provider.route(requests[0])
    assert failed.degraded
    before = clock.now
    cached_failure = provider.route(requests[0])
    assert cached_failure.degraded_reason == "failure_cache_active" and clock.now == before
    recovered = provider.route(requests[1])
    assert not recovered.degraded
    assert starts[-1] - starts[-2] >= 1.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number", "1e-320"])
def test_invalid_qps_is_rejected_before_any_request(monkeypatch, value):
    monkeypatch.setenv("HFT_AMAP_ROUTE_QPS", value)
    with pytest.raises(ValueError, match="finite positive"):
        AmapRouteProvider(api_key="fixture")


def test_configured_qps_controls_the_shared_interval(monkeypatch):
    clock = SimulatedClock()
    monkeypatch.setattr(route_module, "time", SimpleNamespace(monotonic=clock.monotonic, perf_counter=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setenv("HFT_AMAP_ROUTE_QPS", "2")
    starts = []
    def upstream(_):
        starts.append(clock.now)
        return {"status": "1", "route": {"paths": [{"distance": "4200", "cost": {"duration": "1200"}, "steps": []}]}}
    provider = AmapRouteProvider(api_key="fixture", fetch_json=upstream, clock=lambda: NOW)
    provider.route(REQUEST)
    provider.route(REQUEST)
    assert starts == [0.0, 0.5]

import json
import ssl
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import yaml

from app.providers.route import AmapRouteProvider, ResilientRouteProvider, LocalEstimateRouteProvider, InMemoryRouteCache
from app.providers.amap import require_amap_success, AmapResponseError
from tests.test_route_provider import REQUEST, NOW


@pytest.mark.parametrize("failure,category,code", [
    ({"status": "0", "info": "private-data", "infocode": "10021"}, "api_rejected", "10021"),
    (TimeoutError("https://example.invalid?key=private-key"), "timeout", "unknown"),
    (URLError(TimeoutError("private-key")), "timeout", "unknown"),
    (ConnectionResetError("private-key"), "network_error", "unknown"),
    (ssl.SSLError("private-key"), "tls_error", "unknown"),
    (URLError("https://example.invalid?key=private-key"), "network_error", "unknown"),
    (HTTPError("https://example.invalid?key=private-key", 429, "private-data", {}, None), "http_error", "429"),
    (json.JSONDecodeError("private-data", "private-key", 0), "invalid_json", "unknown"),
    ({"status": "1", "route": {"paths": []}}, "invalid_response", "unknown"),
    ({"status": "0", "info": "private-data", "infocode": "private-key"}, "api_rejected", "unknown"),
])
def test_failure_logs_fixed_metadata_without_sensitive_exception_text(caplog, failure, category, code):
    def fetch(_):
        if isinstance(failure, Exception):
            raise failure
        return failure
    provider = AmapRouteProvider(api_key="private-key", fetch_json=fetch, clock=lambda: NOW)
    with caplog.at_level("WARNING", logger="app.providers.route"):
        with pytest.raises(RuntimeError):
            provider.route(REQUEST)
    records = [item.getMessage() for item in caplog.records if item.name == "app.providers.route"]
    assert len(records) == 1
    assert f"category={category} code={code} elapsed_ms=" in records[0]
    assert "private-key" not in records[0] and "private-data" not in records[0]
    assert "https://" not in records[0] and "116.4436" not in records[0]


def test_error_keeps_safe_numeric_code_and_legacy_message():
    with pytest.raises(AmapResponseError) as error:
        require_amap_success({"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}, operation="route")
    assert error.value.code == "10001"
    assert str(error.value) == "Amap route failed: INVALID_USER_KEY (10001)"


def test_fallback_and_failure_cache_keep_original_behavior_with_one_safe_log(caplog):
    calls = []
    def fetch(url):
        calls.append(url)
        return {"status": "0", "info": "private-data", "infocode": "10021"}
    provider = ResilientRouteProvider(
        primary=AmapRouteProvider(api_key="private-key", fetch_json=fetch, clock=lambda: NOW),
        fallback=LocalEstimateRouteProvider(clock=lambda: NOW), cache=InMemoryRouteCache(),
        success_ttl=timedelta(minutes=20), failure_ttl=timedelta(seconds=45), clock=lambda: NOW)
    with caplog.at_level("WARNING", logger="app.providers.route"):
        first, second = provider.route(REQUEST), provider.route(REQUEST)
    assert first.degraded_reason == "upstream_failed_using_local_estimate"
    assert second.degraded_reason == "failure_cache_active"
    assert first.degraded and second.degraded
    assert len(calls) == 1
    assert len([r for r in caplog.records if "amap_route_failure" in r.message]) == 1


def test_diagnostic_overlay_only_mounts_existing_provider_sources_read_only():
    root = Path(__file__).resolve().parents[1]
    overlay = yaml.safe_load((root / "deploy/route-diagnostics.compose.yaml").read_text())
    api = overlay["services"]["api"]
    assert set(api) == {"volumes"}
    assert {item["target"] for item in api["volumes"]} == {"/app/app/providers/route.py", "/app/app/providers/amap.py"}
    assert all(item["read_only"] and not item["bind"]["create_host_path"] for item in api["volumes"])

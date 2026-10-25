import os
import socket

import pytest

from scripts.start_local import ensure_free_ports, environments


def test_public_amap_opt_in_keeps_owner_llm_and_route_weather_disabled(monkeypatch):
    keys = {"AMAP_JS_API_KEY": "fixture-js", "AMAP_JS_SECURITY_CODE": "fixture-security", "AMAP_WEB_SERVICE_KEY": "fixture-web", "LLM_API": "owner-fixture"}
    monkeypatch.setattr("scripts.start_local.dotenv_values", lambda path: keys)
    for name in keys:
        monkeypatch.delenv(name, raising=False)
    backend, _ = environments("PublicDemo", 8001, with_amap=True)
    assert all(backend[name] == keys[name] for name in keys if name.startswith("AMAP_"))
    assert backend["LLM_API"] == ""
    assert backend["HFT_GEOCODING_PROVIDER_MODE"] == "live"
    assert backend["HFT_ROUTE_PROVIDER_MODE"] == backend["HFT_PROVIDER_MODE"] == "mock"
    assert "AMAP_JS_API_KEY" not in os.environ


def test_amap_map_only_and_incomplete_configuration(monkeypatch):
    for name in ("AMAP_JS_API_KEY", "AMAP_JS_SECURITY_CODE", "AMAP_WEB_SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("scripts.start_local.dotenv_values", lambda path: {"AMAP_JS_API_KEY": "fixture-js", "AMAP_JS_SECURITY_CODE": "fixture-security"})
    backend, _ = environments("PublicDemo", 8001, with_amap=True)
    assert backend["HFT_GEOCODING_PROVIDER_MODE"] == "mock"
    monkeypatch.setattr("scripts.start_local.dotenv_values", lambda path: {})
    with pytest.raises(ValueError, match="requires AMAP_JS_API_KEY"):
        environments("PublicDemo", 8001, with_amap=True)


def test_live_amap_requires_explicit_opt_in_and_all_keys(monkeypatch):
    keys = {"AMAP_JS_API_KEY": "fixture-js", "AMAP_JS_SECURITY_CODE": "fixture-security", "AMAP_WEB_SERVICE_KEY": "fixture-web"}
    for name in keys:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("scripts.start_local.dotenv_values", lambda path: keys)
    backend, _ = environments("PublicDemo", 8001, live_amap=True)
    assert backend["LLM_API"] == ""
    assert backend["HFT_PROVIDER_MODE"] == backend["HFT_ROUTE_PROVIDER_MODE"] == backend["HFT_GEOCODING_PROVIDER_MODE"] == "live"
    keys["AMAP_WEB_SERVICE_KEY"] = ""
    with pytest.raises(ValueError, match="also requires"):
        environments("PublicDemo", 8001, live_amap=True)


def test_hybrid_launcher_points_to_prepared_assets_and_does_not_change_parent(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.start_local.ROOT", tmp_path)
    with pytest.raises(ValueError, match="prepare_retrieval"):
        environments("PublicDemo", 8001, hybrid_retrieval=True)
    assets = tmp_path / "deploy/retrieval"
    assets.mkdir(parents=True)
    (assets / "asset_manifest.json").write_text("{}")
    monkeypatch.setenv("HFT_RETRIEVAL_PREWARM", "false")
    backend, _ = environments("PublicDemo", 8001, hybrid_retrieval=True)
    assert backend["HFT_CANDIDATE_RETRIEVER_MODE"] == "hybrid"
    assert backend["HFT_RETRIEVAL_PREWARM"] == "true"
    assert backend["HFT_EMBEDDING_MODEL_PATH"] == str(assets / "model")
    assert backend["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HFT_RETRIEVAL_PREWARM"] == "false"


def test_public_preset_overrides_paid_site_settings_without_mutating_parent(monkeypatch):
    monkeypatch.setenv("LLM_API", "owner-fixture")
    monkeypatch.setenv("HFT_PROVIDER_MODE", "live")
    monkeypatch.setenv("VITE_PUBLIC_DEMO", "false")
    backend, frontend = environments("PublicDemo", 8123)
    assert backend["HFT_PUBLIC_DEMO"] == frontend["VITE_PUBLIC_DEMO"] == "true"
    assert backend["LLM_API"] == backend["AMAP_WEB_SERVICE_KEY"] == ""
    assert backend["HFT_PROVIDER_MODE"] == backend["HFT_ROUTE_PROVIDER_MODE"] == backend["HFT_GEOCODING_PROVIDER_MODE"] == "mock"
    assert backend["HFT_DATABASE_PATH"].endswith("local_public_demo.db")
    assert frontend["HFT_API_PROXY_TARGET"] == "http://127.0.0.1:8123"
    assert frontend["VITE_SHOW_DEVELOPER_DETAILS"] == "false"
    assert os.environ["LLM_API"] == "owner-fixture"
    assert os.environ["HFT_PROVIDER_MODE"] == "live"
    assert os.environ["VITE_PUBLIC_DEMO"] == "false"


def test_development_is_explicit_and_does_not_inherit_public_identity_or_database(monkeypatch):
    monkeypatch.setenv("HFT_PUBLIC_DEMO", "true")
    monkeypatch.setenv("HFT_DATABASE_PATH", "public.db")
    backend, frontend = environments("Development", 8000)
    assert backend["HFT_PUBLIC_DEMO"] == frontend["VITE_PUBLIC_DEMO"] == "false"
    assert backend["HFT_DATABASE_PATH"].endswith("happy_free_time_v2.db")
    assert backend["HFT_DEMO_MODE"] == "1"
    assert backend["HFT_RECOMMENDATION_ADVISOR_MODE"] == "rule"
    real_backend, _ = environments("Development", 8000, True)
    assert real_backend["HFT_DEMO_MODE"] == "0"
    assert real_backend["HFT_PLANNING_INTENT_MODE"] == real_backend["HFT_RECOMMENDATION_ADVISOR_MODE"] == "llm"
    with pytest.raises(ValueError, match="PublicDemo accepts a Key"):
        environments("PublicDemo", 8001, True)


def test_occupied_port_is_rejected_and_existing_listener_is_not_closed():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match="occupied or reserved"):
            ensure_free_ports((port,))
        assert listener.getsockname()[1] == port
    ensure_free_ports((port,))


@pytest.mark.parametrize("ports", [(8000, 8000), (-1, 5173), (8000, 65536)])
def test_invalid_port_pairs_fail_before_starting_services(ports):
    with pytest.raises(ValueError):
        ensure_free_ports(ports)

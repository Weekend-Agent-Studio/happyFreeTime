import os
import socket

import pytest

from scripts.start_local import ensure_free_ports, environments


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

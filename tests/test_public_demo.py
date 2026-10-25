import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient as HttpTestClient
from functools import partial

TestClient = partial(HttpTestClient, base_url="https://testserver")

from app.api.application import create_app
from app.api.public_demo import (DemoAccessError, ModelBundle, PublicDemo, RULE_MODELS, VisitorRouter)
from app.orchestration.entry_graph import default_environment_provider
from app.services.demo_router import DemoRouter
from app.services.demo_world import DemoCatalog


def make_app(path, demo):
    catalog = DemoCatalog()
    return create_app(database_path=path, router=DemoRouter(), environment_provider=default_environment_provider,
                      catalog=catalog, poi_presentation_provider=catalog.presentation_provider, public_demo=demo)


def test_follow_up_party_count_does_not_become_the_budget(tmp_path):
    with TestClient(make_app(tmp_path / "followup.db", PublicDemo())) as client:
        headers = visitor(client)
        session = client.post("/api/sessions", headers=headers).json()["data"]["session_id"]
        for request_id, content in [("initial-turn", "今天下午出去玩"), ("update-turn", "改成两个人，人均150元，严格控制预算")]:
            response = client.post(f"/api/sessions/{session}/messages", headers=headers, json={"request_id": request_id, "content": content})
            assert response.status_code == 200, response.text
        fields = {item["field"]: item for item in response.json()["data"]["constraint_summary"]}
        assert fields["party"]["value"]["adults"] == 2
        assert fields["budget_per_person"]["value"] == 150


def visitor(client):
    response = client.post("/api/demo/visitors")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"]
    return {"X-Demo-Token": response.json()["data"]["token"]}


def test_public_identity_cannot_be_forged_and_all_owned_routes_are_scoped(tmp_path):
    with TestClient(make_app(tmp_path / "public.db", PublicDemo())) as client:
        assert client.post("/api/sessions", headers={"X-User-Id": "demo-user"}).status_code == 401
        a, b = visitor(client), visitor(client)
        session = client.post("/api/sessions", headers=a).json()["data"]["session_id"]
        assert client.get(f"/api/sessions/{session}", headers=a).status_code == 200
        forged = {**b, "X-User-Id": "demo-user"}
        for method, route, body in [
            ("GET", f"/api/sessions/{session}", None),
            ("DELETE", f"/api/sessions/{session}", None),
            ("PATCH", f"/api/sessions/{session}", {"title": "stolen"}),
            ("POST", f"/api/sessions/{session}/plans/unknown/select", None),
            ("POST", f"/api/sessions/{session}/messages", {"request_id": "isolation-test", "content": "今天下午出去玩"}),
        ]:
            assert client.request(method, route, json=body, headers=forged).status_code == 404
        assert client.get("/api/sessions", headers=b).json()["data"]["sessions"] == []
        assert client.get("/api/config/map").status_code == 401
        # A map-only cookie must never authorize session/model access.
        assert client.get("/api/sessions", headers={"Cookie": "hft_map_access=" + a["X-Demo-Token"]}).status_code == 401


def test_configuration_is_private_and_used_in_normal_and_sse_workers(tmp_path):
    seen = []
    class RecordingRouter:
        def __init__(self, label): self.label = label
        def interpret_with_runtime(self, *args, **kwargs):
            seen.append(self.label)
            return RULE_MODELS.router.interpret_with_runtime(*args, **kwargs)
    def factory(key):
        return ModelBundle(RecordingRouter(key), RULE_MODELS.intent, RULE_MODELS.advisor)
    path = tmp_path / "private.db"
    demo = PublicDemo(model_factory=factory)
    with TestClient(make_app(path, demo)) as client:
        a, b = visitor(client), visitor(client)
        sentinel = "sk-private-never-persist-12345"
        response = client.put("/api/demo/model", headers=a, json={"api_key": sentinel})
        assert response.status_code == 200 and not response.json()["data"]["validated"]
        assert sentinel not in response.text and sentinel not in repr(demo._visitors)
        invalid = client.put("/api/demo/model", headers=a, json={"api_key": sentinel, "base_url": "http://localhost:1234"})
        assert invalid.status_code == 422 and sentinel not in invalid.text
        sessions = [client.post("/api/sessions", headers=h).json()["data"]["session_id"] for h in (a, b)]
        for headers, session, stream in [(a, sessions[0], True), (b, sessions[1], False)]:
            result = client.post(f"/api/sessions/{session}/messages" + ("/stream" if stream else ""), headers=headers,
                                 json={"request_id": "public-test-1", "content": "今天下午出去玩"})
            assert result.status_code == 200
            assert sentinel not in result.text
            if stream: assert "event: result" in result.text
        assert seen == [sentinel]
        assert client.put("/api/demo/model", headers=a, json={"api_key": None}).json()["data"]["mode"] == "demo"
    assert sentinel.encode() not in path.read_bytes()


def test_expiry_collects_empty_sessions_history_and_checkpoints(tmp_path):
    now = [0.0]
    demo = PublicDemo(ttl_seconds=10, clock=lambda: now[0])
    path = tmp_path / "expiry.db"
    app = make_app(path, demo)
    with TestClient(app) as client:
        a = visitor(client)
        empty = client.post("/api/sessions", headers=a).json()["data"]["session_id"]
        active = client.post("/api/sessions", headers=a).json()["data"]["session_id"]
        client.post(f"/api/sessions/{active}/messages", headers=a, json={"request_id": "expiry-request", "content": "今天下午出去玩"})
        now[0] = 11
        assert client.get(f"/api/sessions/{active}", headers=a).status_code == 401
        app.state.collect_expired_guests()
        assert not demo._visitors
        with sqlite3.connect(path) as database:
            for table in ["sessions", "users", "messages", "plans", "planning_runs", "checkpoints", "writes"]:
                assert database.execute(f"select count(*) from {table}").fetchone()[0] == 0, table
        fresh = visitor(client)
        assert client.get(f"/api/sessions/{empty}", headers=fresh).status_code == 404


def test_restart_invalidates_old_capabilities_and_removes_orphan_guests(tmp_path):
    path = tmp_path / "restart.db"
    with TestClient(make_app(path, PublicDemo())) as client:
        old = visitor(client)
        client.post("/api/sessions", headers=old)
    with TestClient(make_app(path, PublicDemo())) as client:
        assert client.get("/api/sessions", headers=old).status_code == 401
        with sqlite3.connect(path) as database:
            assert database.execute("select count(*) from sessions").fetchone()[0] == 0


def test_budgets_and_concurrency_hold_until_work_finishes():
    demo = PublicDemo(max_turns=1, max_concurrent=1)
    a, b = [demo.authenticate(demo.issue(str(i))["token"]) for i in range(2)]
    with demo.turn(a.user_id):
        with pytest.raises(DemoAccessError) as error: demo.configure(a, None)
        assert error.value.status == 409
        with pytest.raises(DemoAccessError):
            with demo.turn(b.user_id): pass
    with pytest.raises(DemoAccessError) as error:
        with demo.turn(a.user_id): pass
    assert error.value.status == 429
    with demo.turn(b.user_id): pass


def test_public_request_size_and_bootstrap_rate_limit(tmp_path):
    with TestClient(make_app(tmp_path / "limits.db", PublicDemo())) as client:
        a = visitor(client)
        assert client.put("/api/demo/model", headers=a, content=b"x" * 16385).status_code == 413
        for _ in range(9): assert client.post("/api/demo/visitors").status_code == 201
        assert client.post("/api/demo/visitors").status_code == 429


def test_thread_local_models_do_not_cross_visitors():
    class TaggedRouter:
        def __init__(self, tag): self.tag = tag
        def interpret_with_runtime(self): return self.tag
    demo = PublicDemo(model_factory=lambda key: ModelBundle(TaggedRouter(key), RULE_MODELS.intent, RULE_MODELS.advisor))
    a, b = [demo.authenticate(demo.issue(str(i))["token"]) for i in range(2)]
    demo.configure(a, "A"); demo.configure(b, "B")
    barrier = __import__("threading").Barrier(2)
    def execute(guest):
        with demo.turn(guest.user_id):
            barrier.wait(timeout=5)
            return VisitorRouter().interpret_with_runtime()
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(execute, (a, b))) == ["A", "B"]


def test_expiry_waits_for_running_worker_and_releases_credential_afterward():
    now = [0.0]
    demo = PublicDemo(ttl_seconds=10, clock=lambda: now[0])
    guest = demo.authenticate(demo.issue("one")["token"])
    with demo.turn(guest.user_id):
        now[0] = 11
        assert demo.expired_users() == []
    assert demo.expired_users() == [guest.user_id]
    assert not demo._visitors


def test_upstream_quota_is_shared_across_visitors():
    demo = PublicDemo()
    a, b, c = [demo.authenticate(demo.issue(str(i))["token"]) for i in range(3)]
    for _ in range(60):
        demo.consume_request(a, "/api/departure-locations/search", "GET")
        demo.consume_request(b, "/_AMapService/test", "GET")
    with pytest.raises(DemoAccessError) as error:
        demo.consume_request(c, "/api/departure-locations/search", "GET")
    assert error.value.status == 429


def test_samples_are_read_only_and_report_actual_recorded_transitions(tmp_path):
    with TestClient(make_app(tmp_path / "samples.db", PublicDemo())) as client:
        headers = visitor(client)
        assert client.get("/api/demo/showcases").status_code == 401
        examples = client.get("/api/demo/showcases", headers=headers).json()["data"]["examples"]
        assert len(examples) == 3
        by_id = {example["id"]: example for example in examples}
        assert by_id["plan"]["steps"][1]["response"]["planning_context"]["plan_stale"]
        assert by_id["plan"]["steps"][-2]["response"]["status"] == "completed"
        assert by_id["plan"]["steps"][-1]["selected_plan_id"]
        from app.api.schemas import AgentResponse
        for example in examples:
            assert len(example["steps"]) >= 2
            for step in example["steps"]:
                if step.get("response"):
                    AgentResponse.model_validate(step["response"])
        assert by_id["replace"]["steps"][-1]["response"]["plan_diffs"]
        assert by_id["recovery"]["steps"][0]["response"]["status"] == "needs_recovery"
        assert by_id["recovery"]["steps"][-1]["response"]["status"] == "completed"
        assert client.get("/api/sessions", headers=headers).json()["data"]["sessions"] == []


def test_production_http_model_configuration_is_rejected(tmp_path):
    with HttpTestClient(make_app(tmp_path / "http.db", PublicDemo()), base_url="http://public.example") as client:
        headers = visitor(client)
        response = client.put("/api/demo/model", headers=headers, json={"api_key": "sk-insecure-fixture-123456"})
        assert response.status_code == 400 and "HTTPS" in response.text


def test_byok_factory_uses_explicit_endpoint_and_never_server_credentials(monkeypatch):
    from app.api import public_demo
    calls = []
    monkeypatch.setenv("LLM_API", "server-key-must-not-be-used")
    monkeypatch.setenv("BASE_URL", "http://untrusted.example")
    def build(*args, **options):
        calls.append((args, options))
        return object()
    for name in ("build_default_turn_interpreter", "build_default_planning_intent_provider", "build_default_recommendation_advisor"):
        monkeypatch.setattr(public_demo, name, build)
    public_demo.deepseek_models("visitor-only-key")
    assert len(calls) == 3
    for _, options in calls:
        assert options == {"api_key": "visitor-only-key", "model_name": "deepseek-flash", "base_url": "https://api.deepseek.com", "timeout_seconds": 15.0}

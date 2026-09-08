import tempfile
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.domain.constraints import GeoLocation, Intent, Interpretation, RawConstraints, StopRole
from app.domain.providers import (
    AvailabilityStatus,
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteRequest,
    WeatherFact,
    WeatherRequest,
)
from app.providers.availability import MockAvailabilityProvider
from app.providers.geocoding import MockGeocodingProvider
from app.providers.weather import (
    InMemoryWeatherReplayStore,
    ReplayWeatherProvider,
    clear_mock_weather,
)
from app.services.enrichment import EnvironmentContext
from app.services.catalog import InMemoryCatalog
from app.services.router_extractor import RouterContext
from tests.test_native_planning import candidate
from app.domain.catalog import ResourceType


class RuleRouter:
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        if "只安排一家晚饭" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="明天",
                    time_text="晚上",
                    exact_stop_count=1,
                    required_stop_roles=(StopRole.DINNER,),
                    budget_text="人均150",
                    budget_per_person=150,
                    return_by_text="晚上十点前回家",
                    return_by="22:00",
                ),
                evidence_map={"exact_stop_count": "只安排一家", "required_stop_roles": "晚饭"},
            )
        if "两点半准时出发" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="明天",
                    time_text="下午",
                    departure_at_text="下午两点半准时出发",
                    return_by_text="18:00 前回家",
                ),
            )
        if "约会" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="今天",
                    time_text="下午",
                    adults=2,
                    preferences=["轻松", "甜品"],
                    scene_tags=["约会"],
                ),
                evidence_map={"party": "约会"},
                extraction_confidence={"party": 0.85},
                inferred_fields={"party"},
            )
        if "人均100" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="今天",
                    time_text="下午",
                    budget_text="人均100",
                    budget_per_person=100,
                    strict_budget=True,
                ),
            )
        if "人均200" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="今天",
                    time_text="下午",
                    budget_text="人均200",
                    budget_per_person=200,
                    strict_budget=True,
                ),
            )
        if "返程距离" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="今天",
                    time_text="下午",
                    return_by_text="最晚23:00到家",
                    return_by="23:00",
                    total_distance_text="全程不超过50公里",
                    total_distance_km=50,
                ),
            )
        if "别超预算" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_text="今天",
                    time_text="下午",
                    budget_text="别超预算",
                    strict_budget=True,
                ),
            )
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(date_text="今天", time_text="下午"),
        )


class LocationRuleRouter:
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_text="今天",
                time_text="下午",
                location_text="国贸",
            ),
        )


class FixedReplayRouteProvider:
    def route(self, request: RouteRequest) -> RouteFact:
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            distance_km=4.2,
            duration_minutes=20,
            geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY,
            provider_mode=ProviderMode.REPLAY,
            verified_at=datetime(2026, 8, 15, 8, 5, tzinfo=timezone.utc),
        )


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.environment = EnvironmentContext(
            now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        rainy_fact = WeatherFact(
            city="北京市",
            district="朝阳区",
            date=date(2026, 8, 15),
            condition="中雨",
            temperature_c=23,
            precipitation_mm=8.0,
            is_adverse=True,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc),
            verified_at=datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
        )
        self.replay_store = InMemoryWeatherReplayStore()
        self.replay_store.save(
            WeatherRequest(
                city="北京市",
                district="朝阳区",
                adcode="110105",
                date=date(2026, 8, 15),
            ),
            rainy_fact,
        )
        self.replay_store.save(
            WeatherRequest(
                city="北京市",
                district="朝阳区",
                adcode="110105",
                date=date(2026, 8, 16),
            ),
            rainy_fact.model_copy(update={"date": date(2026, 8, 16)}),
        )
        self.client_context, self.client = self._open_client()
        self.headers = {"X-User-Id": "demo-user"}

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _build_app(self):
        return create_app(
            database_path=Path(self.temp_dir.name) / "api.db",
            router=RuleRouter(),
            environment_provider=lambda _: self.environment,
            weather_provider=ReplayWeatherProvider(
                store=self.replay_store,
                clock=lambda: datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
            ),
            route_provider=FixedReplayRouteProvider(),
        )

    def _open_client(self):
        context = TestClient(self._build_app())
        return context, context.__enter__()

    def _create_session(self) -> str:
        response = self.client.post("/api/sessions", headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["session_id"]

    def _send_message(
        self,
        session_id: str,
        content: str,
        *,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        return self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=headers or self.headers,
            json={"request_id": request_id or uuid.uuid4().hex, "content": content},
        )

    def _session_view(self, session_id: str) -> dict:
        return self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        ).json()["data"]

    def test_selection_starts_empty_then_restores_after_select(self) -> None:
        session_id = self._create_session()
        result = self._send_message(session_id, "今天下午出去玩")
        self.assertEqual(result.status_code, 200, result.text)
        response_data = result.json()["data"]
        plans = response_data["plans"]
        self.assertGreaterEqual(len(plans), 2)
        self.assertEqual(
            response_data["planning_intent_decision"]["source"],
            "rule_based",
        )
        self.assertEqual(
            response_data["planning_intent_decision"]["intent"]["maximum_stops"],
            4,
        )
        second_plan_id = plans[1]["plan_id"]

        view = self._session_view(session_id)
        self.assertIsNone(view["selected_plan_id"])
        self.assertIsNotNone(view["active_plan_version_id"])
        self.assertIsNotNone(view["active_constraints"])
        self.assertEqual(len(view["plan_versions"]), 1)
        self.assertEqual(
            view["response_history"][-1]["planning_intent_decision"]["source"],
            "rule_based",
        )

        select = self.client.post(
            f"/api/sessions/{session_id}/plans/{second_plan_id}/select",
            headers=self.headers,
        )
        self.assertEqual(select.status_code, 200, select.text)
        self.assertEqual(select.json()["data"]["selected_plan_id"], second_plan_id)

        view = self._session_view(session_id)
        self.assertEqual(view["selected_plan_id"], second_plan_id)

    def test_selection_persists_across_app_restart(self) -> None:
        session_id = self._create_session()
        plans = self._send_message(session_id, "今天下午出去玩").json()["data"]["plans"]
        selected = plans[1]["plan_id"]
        self.client.post(
            f"/api/sessions/{session_id}/plans/{selected}/select", headers=self.headers
        )

        with TestClient(self._build_app()) as restarted:
            view = restarted.get(
                f"/api/sessions/{session_id}", headers=self.headers
            ).json()["data"]
            self.assertEqual(view["selected_plan_id"], selected)
            self.assertIsNotNone(view["active_plan_version_id"])

    def test_new_successful_planning_advances_version_and_clears_selection(self) -> None:
        session_id = self._create_session()
        first = self._send_message(session_id, "今天下午出去玩").json()["data"]["plans"]
        self.client.post(
            f"/api/sessions/{session_id}/plans/{first[0]['plan_id']}/select",
            headers=self.headers,
        )

        second = self._send_message(session_id, "今天下午出去玩")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["data"]["plans"])
        self.assertIsNotNone(second.json()["data"]["plan_version_id"])

        view = self._session_view(session_id)
        self.assertIsNone(view["selected_plan_id"])
        self.assertEqual(len(view["plan_versions"]), 2)
        self.assertEqual(
            view["active_plan_version_id"],
            view["plan_versions"][-1]["plan_version_id"],
        )

    def test_idempotent_replay_does_not_duplicate_plan_version(self) -> None:
        session_id = self._create_session()
        payload = {"request_id": "request-select-idem-001", "content": "今天下午出去玩"}
        first = self.client.post(
            f"/api/sessions/{session_id}/messages", headers=self.headers, json=payload
        )
        second = self.client.post(
            f"/api/sessions/{session_id}/messages", headers=self.headers, json=payload
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), first.json())

        view = self._session_view(session_id)
        self.assertEqual(len(view["plan_versions"]), 1)

    def test_select_rejects_invalid_plan_targets(self) -> None:
        session_id = self._create_session()
        first = self._send_message(session_id, "今天下午出去玩").json()["data"]["plans"]
        first_plan_id = first[0]["plan_id"]

        missing = self.client.post(
            f"/api/sessions/{session_id}/plans/nonexistent/select", headers=self.headers
        )
        self.assertEqual(missing.status_code, 404)

        # 第二轮规划使第一版成为历史版本，历史候选不可再选。
        self._send_message(session_id, "今天下午出去玩")
        historical = self.client.post(
            f"/api/sessions/{session_id}/plans/{first_plan_id}/select", headers=self.headers
        )
        self.assertEqual(historical.status_code, 404)

        # 跨会话：另一个会话的 plan 不可选。
        other_session = self._create_session()
        other_plan = self._send_message(other_session, "今天下午出去玩").json()["data"]["plans"][0]
        cross_session = self.client.post(
            f"/api/sessions/{session_id}/plans/{other_plan['plan_id']}/select",
            headers=self.headers,
        )
        self.assertEqual(cross_session.status_code, 404)

        # 跨用户：他人无法访问该会话。
        cross_user = self.client.post(
            f"/api/sessions/{session_id}/plans/{first_plan_id}/select",
            headers={"X-User-Id": "other-user"},
        )
        self.assertEqual(cross_user.status_code, 404)

    def test_map_config_reports_disabled_without_browser_credentials(self) -> None:
        response = self.client.get("/api/config/map")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["data"],
            {
                "enabled": False,
                "provider": "none",
                "js_api_key": None,
                "version": None,
                "service_host_path": None,
            },
        )

    def test_same_plan_composition_can_be_saved_in_two_sessions(self) -> None:
        first_session = self._create_session()
        second_session = self._create_session()

        first = self._send_message(first_session, "今天下午出去玩")
        second = self._send_message(second_session, "今天下午出去玩")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        first_plans = first.json()["data"]["plans"]
        second_plans = second.json()["data"]["plans"]
        self.assertEqual(
            [plan["composition_fingerprint"] for plan in first_plans],
            [plan["composition_fingerprint"] for plan in second_plans],
        )
        self.assertTrue(
            set(plan["plan_id"] for plan in first_plans).isdisjoint(
                plan["plan_id"] for plan in second_plans
            )
        )

    def test_duplicate_request_id_returns_the_stored_run_without_duplicate_messages(
        self,
    ) -> None:
        session_id = self._create_session()
        payload = {
            "request_id": "request-idempotency-001",
            "content": "今天下午出去玩",
        }

        first = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json=payload,
        )
        second = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json=payload,
        )
        restored = self.client.get(
            f"/api/sessions/{session_id}",
            headers=self.headers,
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(restored.json()["data"]["messages"]), 2)

    def test_request_id_cannot_be_reused_for_different_content(self) -> None:
        session_id = self._create_session()
        request_id = "request-idempotency-conflict"

        first = self._send_message(
            session_id,
            "今天下午出去玩",
            request_id=request_id,
        )
        conflict = self._send_message(
            session_id,
            "换一个完全不同的请求",
            request_id=request_id,
        )
        restored = self.client.get(f"/api/sessions/{session_id}", headers=self.headers)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(restored.json()["data"]["messages"]), 2)

    def test_recent_sessions_list_is_bounded_and_orders_non_empty_sessions(self) -> None:
        hidden_empty_session = self._create_session()
        first_session = self._create_session()
        second_session = self._create_session()
        for session_id, request_id, content in (
            (first_session, "request-history-001", "今天下午出去玩"),
            (second_session, "request-history-002", "周六和朋友聚一下，人均150"),
        ):
            response = self._send_message(
                session_id,
                content,
                request_id=request_id,
            )
            self.assertEqual(response.status_code, 200, response.text)

        response = self.client.get(
            "/api/sessions",
            headers=self.headers,
            params={"limit": 1},
        )

        self.assertEqual(response.status_code, 200, response.text)
        sessions = response.json()["data"]["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], second_session)
        self.assertEqual(sessions[0]["title"], "周六和朋友聚一下，人均150")
        self.assertIn("已筛出 3 个可行方案", sessions[0]["last_message_preview"])
        self.assertNotEqual(sessions[0]["session_id"], hidden_empty_session)

    def test_message_exposes_route_source_and_verified_timeline(self) -> None:
        session_id = self._create_session()

        response = self._send_message(session_id, "今天下午出去玩")

        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()["data"]["plans"][0]
        first_leg = plan["route_legs"][0]
        self.assertEqual(first_leg["source"], "replay")
        self.assertEqual(first_leg["provider_mode"], "replay")
        self.assertEqual(first_leg["verified_at"], "2026-08-15T08:05:00Z")
        self.assertFalse(first_leg["degraded"])
        self.assertEqual(first_leg["end"], plan["stops"][0]["start"])
        self.assertEqual(plan["skeleton_id"], "activity-meal-v1")
        self.assertEqual(
            [stop["role"] for stop in plan["stops"]],
            ["activity", "meal"],
        )
        self.assertTrue(plan["score_breakdown"])
        self.assertAlmostEqual(
            sum(item["points"] for item in plan["score_breakdown"]),
            plan["total_score"],
        )
        self.assertTrue(
            all(item["rule_id"] and item["evidence"] for item in plan["score_breakdown"])
        )

    def test_message_exposes_catalog_source_and_pruning_violations(self) -> None:
        session_id = self._create_session()

        response = self._send_message(session_id, "今天下午出去玩，人均100")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertTrue(body["catalog_violations"])
        self.assertIn(
            "single_resource_price_unverified",
            {item["code"] for item in body["catalog_violations"]},
        )
        self.assertEqual(body["plans"], [])
        self.assertEqual(body["conflict"]["code"], "NO_PLAN_WITHIN_STRICT_BUDGET")

        regular_session = self._create_session()
        regular = self._send_message(regular_session, "今天下午出去玩")
        self.assertEqual(regular.status_code, 200, regular.text)
        stop = regular.json()["data"]["plans"][0]["stops"][0]
        self.assertEqual(
            regular.json()["data"]["plans"][0]["price_status"],
            "incomplete",
        )
        stop_source = stop["source"]
        self.assertEqual(stop_source["verification_status"], "unverified")
        self.assertTrue(stop_source["source_uri"].startswith("https://www.openstreetmap.org/"))
        self.assertEqual(stop_source["source_license"], "ODbL-1.0")
        self.assertEqual(stop["price_kind"], "unknown")
        self.assertIn("image", stop)
        self.assertTrue(stop["category_tags"])
        self.assertIn("catalog_warnings", regular.json()["data"])

    def test_get_session_restores_stable_conversation_and_plan_snapshot(self) -> None:
        session_id = self._create_session()
        content = "今天下午出去玩"

        response = self._send_message(session_id, content)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertEqual(body["status"], "completed")
        self.assertGreaterEqual(len(body["plans"]), 1)

        first_read = self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        )
        second_read = self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        )

        self.assertEqual(first_read.status_code, 200, first_read.text)
        self.assertEqual(second_read.status_code, 200, second_read.text)
        restored = first_read.json()["data"]
        self.assertEqual(restored["session_id"], session_id)
        self.assertEqual(
            [(message["role"], message["content"]) for message in restored["messages"]],
            [("user", content), ("assistant", body["reply"])],
        )
        self.assertEqual(restored["plans"], body["plans"])
        self.assertEqual(restored["latest_response"], body)
        self.assertIn("created_at", restored)
        self.assertIn("updated_at", restored)
        self.assertTrue(all("created_at" in message for message in restored["messages"]))
        self.assertEqual(second_read.json()["data"], restored)

    def test_exact_departure_is_planned_and_persisted(self) -> None:
        app = create_app(
            database_path=Path(self.temp_dir.name) / "exact-departure-e2e.db",
            router=RuleRouter(),
            environment_provider=lambda _: EnvironmentContext(
                now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                default_location=GeoLocation(
                    city="北京市",
                    district="朝阳区",
                    address="北京市朝阳区",
                    latitude=39.9219,
                    longitude=116.4436,
                ),
            ),
            weather_provider=clear_mock_weather(
                now=datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)
            ),
            route_provider=FixedReplayRouteProvider(),
            catalog=InMemoryCatalog(
                [
                    candidate("activity", ResourceType.ACTIVITY, "展览", ["展览"], duration_minutes=30),
                    candidate("meal", ResourceType.RESTAURANT, "晚餐", ["餐厅"], duration_minutes=30),
                ]
            ),
        )
        with TestClient(app) as client:
            session = client.post("/api/sessions", headers=self.headers).json()["data"]["session_id"]
            response = client.post(
                f"/api/sessions/{session}/messages",
                headers=self.headers,
                json={
                    "request_id": "exact-departure-e2e",
                    "content": "明天下午两点半准时出发，18:00 前回家",
                },
            )
            restored = client.get(f"/api/sessions/{session}", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        fields = {item["field"]: item for item in body["constraint_summary"]}
        self.assertEqual(fields["departure_at"]["value"], "14:30")
        self.assertTrue(body["plans"])
        first_plan = body["plans"][0]
        self.assertEqual(first_plan["route_legs"][0]["start"], "14:30")
        self.assertLessEqual(first_plan["route_legs"][-1]["end"], "18:00")

        self.assertEqual(restored.status_code, 200, restored.text)
        snapshot = restored.json()["data"]
        restored_fields = {
            item["field"]: item
            for item in snapshot["latest_response"]["constraint_summary"]
        }
        self.assertEqual(restored_fields["departure_at"]["value"], "14:30")
        self.assertEqual(snapshot["plans"], body["plans"])

    def test_dinner_only_http_sqlite_plan_is_restored_without_extra_stops(self) -> None:
        app = create_app(
            database_path=Path(self.temp_dir.name) / "dinner-only-e2e.db",
            router=RuleRouter(),
            environment_provider=lambda _: EnvironmentContext(
                now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
            ),
            weather_provider=clear_mock_weather(now=datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)),
            route_provider=FixedReplayRouteProvider(),
            catalog=InMemoryCatalog([
                candidate("dinner-a", ResourceType.RESTAURANT, "晚餐 A", ["餐厅"], duration_minutes=60, avg_price=120),
                candidate("dinner-b", ResourceType.RESTAURANT, "晚餐 B", ["餐厅"], duration_minutes=60, avg_price=140),
                candidate("activity", ResourceType.ACTIVITY, "展览", ["展览"], duration_minutes=60),
            ]),
        )
        with TestClient(app) as client:
            session = client.post("/api/sessions", headers=self.headers).json()["data"]["session_id"]
            response = client.post(
                f"/api/sessions/{session}/messages",
                headers=self.headers,
                json={"request_id": "dinner-only-e2e", "content": "明天只安排一家晚饭，人均150，晚上十点前回家"},
            )
            restored = client.get(f"/api/sessions/{session}", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertEqual(body["status"], "completed")
        self.assertTrue(body["plans"])
        self.assertTrue(all(len(plan["stops"]) == 1 for plan in body["plans"]))
        self.assertTrue(all(plan["stops"][0]["role"] == "dinner" for plan in body["plans"]))
        self.assertTrue(all(plan["stops"][0]["type"] == "restaurant" for plan in body["plans"]))
        self.assertTrue(all(plan["route_legs"][-1]["destination_name"] == "出发地" for plan in body["plans"]))
        self.assertTrue(all(plan["route_legs"][-1]["end"] <= "22:00" for plan in body["plans"]))
        fields = {item["field"]: item for item in body["constraint_summary"]}
        self.assertEqual(fields["exact_stop_count"]["value"], 1)
        self.assertEqual(fields["required_stop_roles"]["value"], ["dinner"])
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["data"]["latest_response"], body)

    def test_get_session_restores_every_completed_planning_response(self) -> None:
        session_id = self._create_session()
        first = self._send_message(session_id, "今天下午出去玩")
        second = self._send_message(session_id, "人均100")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        restored = self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        )

        self.assertEqual(restored.status_code, 200, restored.text)
        history = restored.json()["data"]["response_history"]
        self.assertEqual(history, [first.json()["data"], second.json()["data"]])

    def test_message_exposes_inferred_party_as_an_editable_constraint(self) -> None:
        session_id = self._create_session()

        response = self._send_message(session_id, "安排一个轻松的约会，想吃甜品")

        self.assertEqual(response.status_code, 200, response.text)
        summary = response.json()["data"]["constraint_summary"]
        party = next(item for item in summary if item["field"] == "party")
        self.assertEqual(party["value"]["adults"], 2)
        self.assertEqual(party["source"], "user_inferred")
        self.assertEqual(party["evidence"], "约会")
        self.assertEqual(party["confidence"], 0.85)
        self.assertTrue(party["user_editable"])

    def test_message_exposes_return_and_total_distance_constraints(self) -> None:
        session_id = self._create_session()

        response = self._send_message(session_id, "返程距离")

        self.assertEqual(response.status_code, 200, response.text)
        fields = {
            item["field"]: item
            for item in response.json()["data"]["constraint_summary"]
        }
        self.assertEqual(fields["return_by"]["value"], "23:00")
        self.assertEqual(fields["total_distance_km"]["value"], 50.0)
        self.assertIn("可行方案", response.json()["data"]["reply"])

    def test_message_exposes_weather_source_and_degradation_state(self) -> None:
        session_id = self._create_session()

        response = self._send_message(session_id, "今天下午出去玩")

        self.assertEqual(response.status_code, 200, response.text)
        facts = response.json()["data"]["provider_facts"]
        weather = next(fact for fact in facts if fact["kind"] == "weather")
        self.assertEqual(weather["condition"], "中雨")
        self.assertEqual(weather["source"], "replay")
        self.assertFalse(weather["degraded"])
        self.assertIsNone(weather["degraded_reason"])
        self.assertTrue(any(fact["kind"] == "availability" for fact in facts))

    def test_blocking_question_resumes_on_the_next_message(self) -> None:
        session_id = self._create_session()

        first = self._send_message(session_id, "今天下午出去玩，别超预算")

        self.assertEqual(first.json()["data"]["status"], "needs_input")
        self.assertEqual(first.json()["data"]["question"]["field"], "budget_per_person")

        second = self._send_message(session_id, "人均200")

        self.assertEqual(second.json()["data"]["status"], "completed")
        self.assertEqual(second.json()["data"]["plans"], [])
        self.assertEqual(
            second.json()["data"]["conflict"]["code"],
            "NO_PLAN_WITHIN_STRICT_BUDGET",
        )

    def test_other_user_cannot_read_or_write_the_session(self) -> None:
        session_id = self._create_session()

        read = self.client.get(
            f"/api/sessions/{session_id}", headers={"X-User-Id": "other-user"}
        )
        write = self._send_message(
            session_id,
            "测试",
            headers={"X-User-Id": "other-user"},
        )

        self.assertEqual(read.status_code, 404)
        self.assertEqual(write.status_code, 404)

    def test_offline_http_sqlite_path_carries_geocoding_weather_availability_and_warnings(self) -> None:
        weather_store = InMemoryWeatherReplayStore()
        weather_store.save(
            WeatherRequest(city="北京市", district="朝阳区", adcode="110105", date=date(2026, 8, 15)),
            WeatherFact(
                city="北京市", district="朝阳区", date=date(2026, 8, 15),
                condition="晴", source=ProviderSource.REPLAY, mode=ProviderMode.REPLAY,
                observed_at=datetime(2026, 8, 15, 8, tzinfo=timezone.utc),
                verified_at=datetime(2026, 8, 15, 8, tzinfo=timezone.utc),
            ),
        )
        app = create_app(
            database_path=Path(self.temp_dir.name) / "m2-e2e.db",
            router=LocationRuleRouter(),
            environment_provider=lambda _: EnvironmentContext(
                now=datetime(2026, 8, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
                default_location=GeoLocation(
                    city="北京市", district="朝阳区", address="北京市朝阳区",
                    latitude=39.9219, longitude=116.4436, adcode="110105",
                ),
            ),
            weather_provider=ReplayWeatherProvider(
                store=weather_store,
                clock=lambda: datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
            ),
            route_provider=FixedReplayRouteProvider(),
            geocoding_provider=MockGeocodingProvider.from_locations(
                {("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸")},
                clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc),
            ),
            availability_provider=MockAvailabilityProvider(
                statuses={}, clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc)
            ),
        )
        with TestClient(app) as client:
            session = client.post("/api/sessions", headers=self.headers).json()["data"]["session_id"]
            response = client.post(
                f"/api/sessions/{session}/messages",
                headers=self.headers,
                json={"request_id": "m2-offline-e2e-001", "content": "国贸附近今天下午出去玩"},
            )
            restored = client.get(f"/api/sessions/{session}", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertTrue(body["plans"])
        self.assertIn("已筛出", body["reply"])
        self.assertEqual(
            {fact["kind"] for fact in body["provider_facts"]},
            {"geocoding", "weather", "availability"},
        )
        self.assertEqual(
            next(item for item in body["constraint_summary"] if item["field"] == "location")["value"]["address"],
            "北京市朝阳区国贸",
        )
        self.assertTrue(any(item["code"] == "availability_unconfirmed" for item in body["warnings"]))
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["data"]["latest_response"]["provider_facts"], body["provider_facts"])

    def test_offline_http_path_replans_after_verified_unavailable_finalist(self) -> None:
        app = create_app(
            database_path=Path(self.temp_dir.name) / "m2-replan-e2e.db",
            router=LocationRuleRouter(),
            environment_provider=lambda _: EnvironmentContext(
                now=datetime(2026, 8, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
                default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436, adcode="110105"),
            ),
            route_provider=FixedReplayRouteProvider(),
            geocoding_provider=MockGeocodingProvider.from_locations(
                {("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸")}
            ),
            availability_provider=MockAvailabilityProvider(
                statuses={"meal-a": AvailabilityStatus.UNAVAILABLE, "meal-b": AvailabilityStatus.AVAILABLE}
            ),
            catalog=InMemoryCatalog([
                candidate("activity-a", ResourceType.ACTIVITY, "展览", ["展览"]),
                candidate("meal-a", ResourceType.RESTAURANT, "餐厅 A", ["餐厅"]),
                candidate("meal-b", ResourceType.RESTAURANT, "餐厅 B", ["餐厅"]),
            ]),
        )
        with TestClient(app) as client:
            session = client.post("/api/sessions", headers=self.headers).json()["data"]["session_id"]
            response = client.post(
                f"/api/sessions/{session}/messages",
                headers=self.headers,
                json={"request_id": "m2-local-replan-e2e", "content": "国贸附近今天下午出去玩"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["data"]["plans"]
        self.assertTrue(plans)
        self.assertTrue(all("meal-a" not in {stop["resource_id"] for stop in plan["stops"]} for plan in plans))
        self.assertTrue(any([stop["resource_id"] for stop in plan["stops"]] == ["activity-a", "meal-b"] for plan in plans))


if __name__ == "__main__":
    unittest.main()

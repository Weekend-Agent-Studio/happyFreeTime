import tempfile
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.domain.constraints import GeoLocation, Intent, Interpretation, RawConstraints
from app.domain.providers import (
    ProviderMode,
    ProviderSource,
    RouteFact,
    RouteRequest,
    WeatherFact,
    WeatherRequest,
)
from app.providers.weather import InMemoryWeatherReplayStore, ReplayWeatherProvider
from app.services.enrichment import EnvironmentContext
from app.services.router_extractor import RouterContext


class RuleRouter:
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
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
        environment = EnvironmentContext(
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
        replay_store = InMemoryWeatherReplayStore()
        replay_store.save(
            WeatherRequest(
                city="北京市",
                district="朝阳区",
                adcode="110105",
                date=date(2026, 8, 15),
            ),
            rainy_fact,
        )
        app = create_app(
            database_path=Path(self.temp_dir.name) / "api.db",
            router=RuleRouter(),
            environment_provider=lambda _: environment,
            weather_provider=ReplayWeatherProvider(
                store=replay_store,
                clock=lambda: datetime(2026, 8, 15, 8, 1, tzinfo=timezone.utc),
            ),
            route_provider=FixedReplayRouteProvider(),
        )
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = {"X-User-Id": "demo-user"}

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

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
        self.assertEqual(sessions[0]["last_message_preview"], "已生成 3 个候选方案。")
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

    def test_message_exposes_weather_source_and_degradation_state(self) -> None:
        session_id = self._create_session()

        response = self._send_message(session_id, "今天下午出去玩")

        self.assertEqual(response.status_code, 200, response.text)
        facts = response.json()["data"]["provider_facts"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["kind"], "weather")
        self.assertEqual(facts[0]["condition"], "中雨")
        self.assertEqual(facts[0]["source"], "replay")
        self.assertFalse(facts[0]["degraded"])
        self.assertIsNone(facts[0]["degraded_reason"])

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


if __name__ == "__main__":
    unittest.main()

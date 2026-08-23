import tempfile
import unittest
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

    def test_message_exposes_route_source_and_verified_timeline(self) -> None:
        session_id = self._create_session()

        response = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "今天下午出去玩"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()["data"]["plans"][0]
        first_leg = plan["route_legs"][0]
        self.assertEqual(first_leg["source"], "replay")
        self.assertEqual(first_leg["provider_mode"], "replay")
        self.assertEqual(first_leg["verified_at"], "2026-08-15T08:05:00Z")
        self.assertFalse(first_leg["degraded"])
        self.assertEqual(first_leg["end"], plan["stops"][0]["start"])

    def test_message_exposes_catalog_source_and_pruning_violations(self) -> None:
        session_id = self._create_session()

        response = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "今天下午出去玩，人均100"},
        )

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
        regular = self.client.post(
            f"/api/sessions/{regular_session}/messages",
            headers=self.headers,
            json={"content": "今天下午出去玩"},
        )
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

        response = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": content},
        )

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
        self.assertEqual(second_read.json()["data"], restored)

    def test_message_exposes_inferred_party_as_an_editable_constraint(self) -> None:
        session_id = self._create_session()

        response = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "安排一个轻松的约会，想吃甜品"},
        )

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

        response = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "今天下午出去玩"},
        )

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

        first = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "今天下午出去玩，别超预算"},
        )

        self.assertEqual(first.json()["data"]["status"], "needs_input")
        self.assertEqual(first.json()["data"]["question"]["field"], "budget_per_person")

        second = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "人均200"},
        )

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
        write = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers={"X-User-Id": "other-user"},
            json={"content": "测试"},
        )

        self.assertEqual(read.status_code, 404)
        self.assertEqual(write.status_code, 404)


if __name__ == "__main__":
    unittest.main()

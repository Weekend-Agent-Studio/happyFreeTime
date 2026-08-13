import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.domain.constraints import GeoLocation, Intent, Interpretation, RawConstraints
from app.services.enrichment import EnvironmentContext
from app.services.router_extractor import RouterContext


class RuleRouter:
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
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
        app = create_app(
            database_path=Path(self.temp_dir.name) / "api.db",
            router=RuleRouter(),
            environment_provider=lambda _: environment,
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

    def test_message_returns_structured_plans_and_persists_them(self) -> None:
        session_id = self._create_session()

        response = self.client.post(
            f"/api/sessions/{session_id}/messages",
            headers=self.headers,
            json={"content": "今天下午出去玩"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertEqual(body["status"], "completed")
        self.assertGreaterEqual(len(body["plans"]), 1)
        session_response = self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        )
        self.assertEqual(len(session_response.json()["data"]["plans"]), len(body["plans"]))

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
        self.assertGreaterEqual(len(second.json()["data"]["plans"]), 1)

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

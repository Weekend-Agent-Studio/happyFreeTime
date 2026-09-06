import tempfile
import unittest
import uuid
from pathlib import Path

from app.domain.planning import Plan
from app.persistence.database import Database
from app.persistence.repositories import SessionRepository


def make_plan(plan_id: str) -> Plan:
    return Plan(
        plan_id=plan_id,
        composition_fingerprint=f"fingerprint-{plan_id}",
        title=plan_id,
        strategy="balanced",
        total_score=10.0,
        total_price=0,
        total_duration_minutes=60,
        stops=[],
        route_legs=[],
    )


class SessionRepositoryTest(unittest.TestCase):
    def test_session_and_messages_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "app.db")
            try:
                database.create_schema()
                repository = SessionRepository(database.session_factory)

                session = repository.create_session(user_id="user-a", identity_type="demo")
                repository.add_message(
                    user_id="user-a",
                    session_id=session.id,
                    role="user",
                    content="今天下午出去玩",
                )

                self.assertIsNotNone(repository.get_session("user-a", session.id))
                self.assertIsNone(repository.get_session("user-b", session.id))
                self.assertEqual(len(repository.list_messages("user-a", session.id)), 1)
                self.assertEqual(repository.list_messages("user-b", session.id), [])
            finally:
                database.close()


class PlanVersionPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "app.db")
        self.database.create_schema()
        self.repository = SessionRepository(self.database.session_factory)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def _complete_success(
        self,
        *,
        user_id: str,
        session_id: str,
        request_id: str = "request-1",
        plan_ids: tuple[str, ...] = ("plan-a", "plan-b"),
        content: str = "今天下午出去玩",
    ) -> str:
        run = self.repository.begin_planning_run(
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            content=content,
        )
        self.repository.complete_planning_run(
            user_id=user_id,
            session_id=session_id,
            planning_run_id=run.planning_run_id,
            status="completed",
            response={"status": "completed", "plans": list(plan_ids)},
            assistant_content="已生成方案",
            plans=[make_plan(plan_id) for plan_id in plan_ids],
            plan_version_id=uuid.uuid4().hex,
            normalized_constraints_json='{"date": {"value": "2026-08-15", "source": "user_inferred"}}',
        )
        return run.planning_run_id

    def test_successful_run_creates_a_plan_version_and_snapshot(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        self._complete_success(user_id="user-a", session_id=session.id)

        versions = self.repository.list_plan_versions("user-a", session.id)
        self.assertEqual(len(versions), 1)
        snapshot = self.repository.get_session_snapshot("user-a", session.id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.active_plan_version_id, versions[0].id)
        self.assertIsNone(snapshot.selected_plan_id)

    def test_idempotent_replay_does_not_create_a_second_version(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        planning_run_id = self._complete_success(
            user_id="user-a", session_id=session.id, request_id="request-replay"
        )
        # 再次用同一个 planning_run_id 完成，应被幂等守卫短路，不新建版本。
        self.repository.complete_planning_run(
            user_id="user-a",
            session_id=session.id,
            planning_run_id=planning_run_id,
            status="completed",
            response={"status": "completed", "plans": []},
            assistant_content="已生成方案",
            plans=[make_plan("plan-a")],
            plan_version_id=uuid.uuid4().hex,
            normalized_constraints_json='{"date": "x"}',
        )

        versions = self.repository.list_plan_versions("user-a", session.id)
        self.assertEqual(len(versions), 1)

    def test_question_conflict_and_failed_do_not_create_versions(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        for request_id in ("request-question", "request-conflict", "request-failed"):
            run = self.repository.begin_planning_run(
                user_id="user-a", session_id=session.id, request_id=request_id, content="hi"
            )
            self.repository.complete_planning_run(
                user_id="user-a",
                session_id=session.id,
                planning_run_id=run.planning_run_id,
                status="completed",
                response={"status": "completed", "plans": []},
                assistant_content="no plans",
                plans=[],
            )

        self.assertEqual(self.repository.list_plan_versions("user-a", session.id), [])
        self.assertIsNone(self.repository.get_session_snapshot("user-a", session.id))

    def test_new_success_becomes_active_and_clears_old_selection(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        first_version = self._complete_success(user_id="user-a", session_id=session.id)
        snapshot = self.repository.get_session_snapshot("user-a", session.id)
        self.repository.select_plan(user_id="user-a", session_id=session.id, plan_id="plan-a")
        self.assertEqual(
            self.repository.get_session_snapshot("user-a", session.id).selected_plan_id,
            "plan-a",
        )

        self._complete_success(
            user_id="user-a", session_id=session.id, request_id="request-2", plan_ids=("plan-c",)
        )
        snapshot = self.repository.get_session_snapshot("user-a", session.id)
        self.assertIsNone(snapshot.selected_plan_id)
        versions = self.repository.list_plan_versions("user-a", session.id)
        self.assertEqual(len(versions), 2)
        self.assertEqual(snapshot.active_plan_version_id, versions[-1].id)
        # 旧版本仍保留。
        self.assertIn(first_version, [version.planning_run_id for version in versions])

    def test_old_versions_and_plans_are_preserved(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        self._complete_success(user_id="user-a", session_id=session.id, plan_ids=("old-a", "old-b"))
        self._complete_success(
            user_id="user-a", session_id=session.id, request_id="request-2", plan_ids=("new-a",)
        )

        versions = self.repository.list_plan_versions("user-a", session.id)
        self.assertEqual(len(versions), 2)
        active_plans = self.repository.list_plans("user-a", session.id)
        self.assertEqual([plan["plan_id"] for plan in active_plans], ["new-a"])

    def test_select_current_version_candidate_can_be_re_read(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        self._complete_success(user_id="user-a", session_id=session.id)

        snapshot = self.repository.select_plan(
            user_id="user-a", session_id=session.id, plan_id="plan-b"
        )
        self.assertEqual(snapshot.selected_plan_id, "plan-b")
        restored = self.repository.get_session_snapshot("user-a", session.id)
        self.assertEqual(restored.selected_plan_id, "plan-b")

    def test_cannot_select_historical_version_candidate(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        self._complete_success(user_id="user-a", session_id=session.id, plan_ids=("old-a",))
        self._complete_success(
            user_id="user-a", session_id=session.id, request_id="request-2", plan_ids=("new-a",)
        )

        with self.assertRaises(LookupError):
            self.repository.select_plan(user_id="user-a", session_id=session.id, plan_id="old-a")

    def test_cannot_select_across_user_or_session(self) -> None:
        session_a = self.repository.create_session(user_id="user-a", identity_type="demo")
        session_b = self.repository.create_session(user_id="user-a", identity_type="demo")
        self._complete_success(user_id="user-a", session_id=session_a.id, plan_ids=("plan-a",))
        self._complete_success(user_id="user-a", session_id=session_b.id, plan_ids=("plan-b",))

        with self.assertRaises(LookupError):
            self.repository.select_plan(user_id="user-a", session_id=session_a.id, plan_id="plan-b")
        with self.assertRaises(LookupError):
            self.repository.select_plan(user_id="user-b", session_id=session_a.id, plan_id="plan-a")

    def test_non_success_does_not_clear_active_version_or_selection(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        self._complete_success(user_id="user-a", session_id=session.id, plan_ids=("plan-a",))
        self.repository.select_plan(user_id="user-a", session_id=session.id, plan_id="plan-a")

        run = self.repository.begin_planning_run(
            user_id="user-a", session_id=session.id, request_id="request-question", content="hi"
        )
        self.repository.complete_planning_run(
            user_id="user-a",
            session_id=session.id,
            planning_run_id=run.planning_run_id,
            status="needs_input",
            response={"status": "needs_input", "plans": []},
            assistant_content="请问预算？",
            plans=[],
        )

        snapshot = self.repository.get_session_snapshot("user-a", session.id)
        self.assertEqual(snapshot.selected_plan_id, "plan-a")
        self.assertIsNotNone(snapshot.active_plan_version_id)

    def test_plan_version_stores_actual_normalized_constraints(self) -> None:
        session = self.repository.create_session(user_id="user-a", identity_type="demo")
        run = self.repository.begin_planning_run(
            user_id="user-a", session_id=session.id, request_id="request-1", content="hi"
        )
        constraints_json = (
            '{"date": {"value": "2026-08-15", "source": "user_inferred"},'
            ' "budget_per_person": {"value": 150, "source": "user_explicit"}}'
        )
        self.repository.complete_planning_run(
            user_id="user-a",
            session_id=session.id,
            planning_run_id=run.planning_run_id,
            status="completed",
            response={"status": "completed", "plans": ["plan-a"]},
            assistant_content="ok",
            plans=[make_plan("plan-a")],
            plan_version_id=uuid.uuid4().hex,
            normalized_constraints_json=constraints_json,
        )

        self.assertEqual(
            self.repository.active_constraints("user-a", session.id)["budget_per_person"]["value"],
            150,
        )


if __name__ == "__main__":
    unittest.main()

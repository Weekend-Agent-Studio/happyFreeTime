import tempfile
import unittest
from pathlib import Path

from app.persistence.database import Database
from app.persistence.repositories import SessionRepository


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


if __name__ == "__main__":
    unittest.main()

"""SQLAlchemy 引擎和会话工厂的最小封装。"""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    """管理业务 SQLite 连接；测试可通过临时路径创建完全隔离的数据库。"""
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def create_schema(self) -> None:
        """导入 ORM 模型并创建/补齐当前本地 SQLite schema.

        The project does not ship Alembic yet, so additive development-schema
        changes are applied here.  This keeps an existing local demo database
        usable after the request-snapshot column was introduced; destructive
        migrations remain out of scope.
        """
        from app.persistence import models  # noqa: F401

        Base.metadata.create_all(self.engine)
        columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("session_snapshots")
        }
        if "active_request_json" not in columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE session_snapshots "
                        "ADD COLUMN active_request_json TEXT"
                    )
                )

    def close(self) -> None:
        self.engine.dispose()

"""SQLAlchemy 引擎和会话工厂的最小封装。"""

from pathlib import Path

from sqlalchemy import create_engine
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
        """导入全部 ORM 模型并为 M1 创建表；正式部署后应迁移到 Alembic。"""
        from app.persistence import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

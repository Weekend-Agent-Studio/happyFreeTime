"""会话聚合的持久化接口，集中实现 user_id 数据隔离。"""

import json
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.domain.planning import Plan
from app.persistence.models import (
    MessageRecord,
    PlanRecord,
    SessionRecord,
    UserRecord,
    utc_now,
)


class SessionRepository:
    """封装用户、会话、消息和方案的常用数据库操作。

    所有读取和写入都同时使用 ``user_id + session_id`` 校验归属，避免仅知道
    session_id 的其他用户访问会话内容。
    """
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def create_session(self, *, user_id: str, identity_type: str) -> SessionRecord:
        with self._session_factory.begin() as database:
            user = database.get(UserRecord, user_id)
            if user is None:
                database.add(UserRecord(id=user_id, identity_type=identity_type))
            record = SessionRecord(id=uuid.uuid4().hex, user_id=user_id)
            database.add(record)
        return record

    def get_session(self, user_id: str, session_id: str) -> SessionRecord | None:
        with self._session_factory() as database:
            return database.scalar(
                select(SessionRecord).where(
                    SessionRecord.id == session_id,
                    SessionRecord.user_id == user_id,
                )
            )

    def add_message(
        self,
        *,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
    ) -> MessageRecord:
        if self.get_session(user_id, session_id) is None:
            raise LookupError("session not found")
        with self._session_factory.begin() as database:
            record = MessageRecord(
                id=uuid.uuid4().hex,
                user_id=user_id,
                session_id=session_id,
                role=role,
                content=content,
            )
            database.add(record)
        return record

    def list_messages(self, user_id: str, session_id: str) -> list[MessageRecord]:
        with self._session_factory() as database:
            return list(
                database.scalars(
                    select(MessageRecord)
                    .where(
                        MessageRecord.user_id == user_id,
                        MessageRecord.session_id == session_id,
                    )
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                )
            )

    def replace_plans(
        self,
        *,
        user_id: str,
        session_id: str,
        plans: list[Plan],
    ) -> None:
        """用本轮候选整体替换旧候选，避免不同规划轮次的结果混在一起。"""
        if self.get_session(user_id, session_id) is None:
            raise LookupError("session not found")
        snapshot_at = utc_now()
        with self._session_factory.begin() as database:
            database.execute(
                delete(PlanRecord).where(
                    PlanRecord.user_id == user_id,
                    PlanRecord.session_id == session_id,
                )
            )
            database.add_all(
                [
                    PlanRecord(
                        id=plan.plan_id,
                        user_id=user_id,
                        session_id=session_id,
                        payload_json=plan.model_dump_json(),
                        created_at=snapshot_at + timedelta(microseconds=index),
                    )
                    for index, plan in enumerate(plans)
                ]
            )

    def list_plans(self, user_id: str, session_id: str) -> list[dict]:
        with self._session_factory() as database:
            records = database.scalars(
                select(PlanRecord)
                .where(
                    PlanRecord.user_id == user_id,
                    PlanRecord.session_id == session_id,
                )
                .order_by(PlanRecord.created_at, PlanRecord.id)
            )
            return [json.loads(record.payload_json) for record in records]

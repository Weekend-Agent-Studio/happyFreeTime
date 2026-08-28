"""会话聚合的持久化接口，集中实现 user_id 数据隔离。"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.domain.planning import Plan
from app.persistence.models import (
    MessageRecord,
    PlanRecord,
    PlanningRunRecord,
    SessionRecord,
    UserRecord,
    utc_now,
)


@dataclass(frozen=True)
class PlanningRunStart:
    planning_run_id: str
    should_execute: bool
    stored_response: dict | None = None


@dataclass(frozen=True)
class SessionSummary:
    """A compact, user-scoped row for the recent-session navigation."""

    session_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str


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

    def list_recent_sessions(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[SessionSummary]:
        """List non-empty sessions by activity without leaking another user's data."""
        with self._session_factory() as database:
            records = list(
                database.scalars(
                    select(SessionRecord)
                    .where(
                        SessionRecord.user_id == user_id,
                        SessionRecord.id.in_(
                            select(MessageRecord.session_id).where(
                                MessageRecord.user_id == user_id
                            )
                        ),
                    )
                    .order_by(SessionRecord.updated_at.desc(), SessionRecord.id.desc())
                    .limit(limit)
                )
            )
            summaries: list[SessionSummary] = []
            for record in records:
                latest_message = database.scalar(
                    select(MessageRecord)
                    .where(
                        MessageRecord.user_id == user_id,
                        MessageRecord.session_id == record.id,
                    )
                    .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                    .limit(1)
                )
                summaries.append(
                    SessionSummary(
                        session_id=record.id,
                        title=record.title,
                        status=record.status,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        last_message_preview=(latest_message.content[:60] if latest_message else ""),
                    )
                )
            return summaries

    def begin_planning_run(
        self,
        *,
        user_id: str,
        session_id: str,
        request_id: str,
        content: str,
    ) -> PlanningRunStart:
        """Create one logical run and user message, or resume its stored outcome."""
        if self.get_session(user_id, session_id) is None:
            raise LookupError("session not found")
        now = utc_now()
        with self._session_factory.begin() as database:
            existing = database.scalar(
                select(PlanningRunRecord).where(
                    PlanningRunRecord.user_id == user_id,
                    PlanningRunRecord.session_id == session_id,
                    PlanningRunRecord.request_id == request_id,
                )
            )
            if existing is not None:
                if existing.request_content != content:
                    raise ValueError("request_id was already used for different content")
                if existing.response_json is not None:
                    return PlanningRunStart(
                        planning_run_id=existing.id,
                        should_execute=False,
                        stored_response=json.loads(existing.response_json),
                    )
                if existing.status == "running":
                    return PlanningRunStart(
                        planning_run_id=existing.id,
                        should_execute=False,
                    )
                existing.status = "running"
                existing.updated_at = now
                session = database.get(SessionRecord, session_id)
                if session is not None:
                    session.status = "running"
                    session.updated_at = now
                return PlanningRunStart(
                    planning_run_id=existing.id,
                    should_execute=True,
                )

            planning_run = PlanningRunRecord(
                id=uuid.uuid4().hex,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                request_content=content,
                status="running",
                created_at=now,
                updated_at=now,
            )
            database.add(planning_run)
            database.add(
                MessageRecord(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    session_id=session_id,
                    role="user",
                    content=content,
                    created_at=now,
                )
            )
            session = database.get(SessionRecord, session_id)
            if session is not None:
                if session.title == "新规划":
                    session.title = _session_title(content)
                session.status = "running"
                session.updated_at = now
            return PlanningRunStart(
                planning_run_id=planning_run.id,
                should_execute=True,
            )

    def complete_planning_run(
        self,
        *,
        user_id: str,
        session_id: str,
        planning_run_id: str,
        status: str,
        response: dict,
        assistant_content: str,
        plans: list[Plan],
    ) -> None:
        """Atomically persist one run response, assistant message, and Plan Version."""
        now = utc_now()
        with self._session_factory.begin() as database:
            planning_run = database.scalar(
                select(PlanningRunRecord).where(
                    PlanningRunRecord.id == planning_run_id,
                    PlanningRunRecord.user_id == user_id,
                    PlanningRunRecord.session_id == session_id,
                )
            )
            if planning_run is None:
                raise LookupError("planning run not found")
            if planning_run.response_json is not None:
                return
            database.add_all(
                [
                    PlanRecord(
                        id=plan.plan_id,
                        user_id=user_id,
                        session_id=session_id,
                        planning_run_id=planning_run_id,
                        composition_fingerprint=plan.composition_fingerprint,
                        payload_json=plan.model_dump_json(),
                        created_at=now + timedelta(microseconds=index),
                    )
                    for index, plan in enumerate(plans)
                ]
            )
            if assistant_content:
                database.add(
                    MessageRecord(
                        id=uuid.uuid4().hex,
                        user_id=user_id,
                        session_id=session_id,
                        role="assistant",
                        content=assistant_content,
                        created_at=now,
                    )
                )
            planning_run.status = status
            planning_run.response_json = json.dumps(response, ensure_ascii=False)
            planning_run.updated_at = now
            session = database.get(SessionRecord, session_id)
            if session is not None:
                session.status = status
                session.updated_at = now

    def fail_planning_run(
        self,
        *,
        user_id: str,
        session_id: str,
        planning_run_id: str,
    ) -> None:
        now = utc_now()
        with self._session_factory.begin() as database:
            planning_run = database.scalar(
                select(PlanningRunRecord).where(
                    PlanningRunRecord.id == planning_run_id,
                    PlanningRunRecord.user_id == user_id,
                    PlanningRunRecord.session_id == session_id,
                )
            )
            if planning_run is not None and planning_run.response_json is None:
                planning_run.status = "failed"
                planning_run.updated_at = now
            session = database.get(SessionRecord, session_id)
            if session is not None:
                session.status = "failed"
                session.updated_at = now

    def latest_response(self, user_id: str, session_id: str) -> dict | None:
        with self._session_factory() as database:
            planning_run = database.scalar(
                select(PlanningRunRecord)
                .where(
                    PlanningRunRecord.user_id == user_id,
                    PlanningRunRecord.session_id == session_id,
                    PlanningRunRecord.response_json.is_not(None),
                )
                .order_by(
                    PlanningRunRecord.updated_at.desc(),
                    PlanningRunRecord.id.desc(),
                )
            )
            if planning_run is None or planning_run.response_json is None:
                return None
            return json.loads(planning_run.response_json)

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
                        planning_run_id=None,
                        composition_fingerprint=plan.composition_fingerprint,
                        payload_json=plan.model_dump_json(),
                        created_at=snapshot_at + timedelta(microseconds=index),
                    )
                    for index, plan in enumerate(plans)
                ]
            )

    def list_plans(self, user_id: str, session_id: str) -> list[dict]:
        latest = self.latest_response(user_id, session_id)
        if latest is not None:
            return list(latest.get("plans", []))
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


def _session_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:30] or "新规划"

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
    PlanVersionRecord,
    PlanningRunRecord,
    SessionRecord,
    SessionSnapshotRecord,
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
        plan_version_id: str | None = None,
        normalized_constraints_json: str | None = None,
    ) -> None:
        """Atomically persist one run response, assistant message, and Plan Version.

        只有真正返回候选方案的成功 run（``plans`` 非空）才创建 Plan Version，
        并把它设为 active、清空旧选择；question/conflict/failed 不创建版本，
        也不切换 active 版本或清除已有选择。幂等重放由 ``response_json`` 已存在
        的早退和 ``plan_versions.planning_run_id`` 唯一约束共同保证。
        """
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
            if plans:
                if status != "completed":
                    raise ValueError("a planning run with plans must be completed")
                if not plan_version_id or normalized_constraints_json is None:
                    raise ValueError(
                        "a planning run with plans requires a plan version and constraint snapshot"
                    )
                if not isinstance(normalized_constraints_json, str) or not normalized_constraints_json.strip():
                    raise ValueError("a plan version requires a non-empty constraint snapshot")
                try:
                    normalized_constraints = json.loads(normalized_constraints_json)
                except (TypeError, json.JSONDecodeError) as error:
                    raise ValueError("constraint snapshot must be valid JSON") from error
                if not isinstance(normalized_constraints, dict) or not normalized_constraints:
                    raise ValueError("constraint snapshot must be a non-empty JSON object")
            elif plan_version_id is not None or normalized_constraints_json is not None:
                raise ValueError(
                    "plan version metadata is only valid when plans are present"
                )
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
            if plans:
                database.add(
                    PlanVersionRecord(
                        id=plan_version_id,
                        user_id=user_id,
                        session_id=session_id,
                        planning_run_id=planning_run_id,
                        normalized_constraints_json=normalized_constraints_json,
                        supersedes_version_id=None,
                        created_at=now,
                    )
                )
                snapshot = database.scalar(
                    select(SessionSnapshotRecord).where(
                        SessionSnapshotRecord.session_id == session_id,
                        SessionSnapshotRecord.user_id == user_id,
                    )
                )
                if snapshot is None:
                    database.add(
                        SessionSnapshotRecord(
                            session_id=session_id,
                            user_id=user_id,
                            active_plan_version_id=plan_version_id,
                            selected_plan_id=None,
                            updated_at=now,
                        )
                    )
                else:
                    snapshot.active_plan_version_id = plan_version_id
                    snapshot.selected_plan_id = None
                    snapshot.updated_at = now
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

    def response_history(self, user_id: str, session_id: str) -> list[dict]:
        """Return every completed response in conversation order.

        A rich planning response belongs to the assistant turn that completed the
        run. Keeping the immutable run payloads lets clients restore the complete
        conversation instead of reconstructing it from only the latest plan.
        """
        with self._session_factory() as database:
            planning_runs = database.scalars(
                select(PlanningRunRecord)
                .where(
                    PlanningRunRecord.user_id == user_id,
                    PlanningRunRecord.session_id == session_id,
                    PlanningRunRecord.response_json.is_not(None),
                )
                .order_by(
                    PlanningRunRecord.created_at,
                    PlanningRunRecord.id,
                )
            )
            return [
                json.loads(planning_run.response_json)
                for planning_run in planning_runs
                if planning_run.response_json is not None
            ]

    def get_session_snapshot(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionSnapshotRecord | None:
        with self._session_factory() as database:
            return database.scalar(
                select(SessionSnapshotRecord).where(
                    SessionSnapshotRecord.session_id == session_id,
                    SessionSnapshotRecord.user_id == user_id,
                )
            )

    def list_plan_versions(
        self,
        user_id: str,
        session_id: str,
    ) -> list[PlanVersionRecord]:
        with self._session_factory() as database:
            return list(
                database.scalars(
                    select(PlanVersionRecord)
                    .where(
                        PlanVersionRecord.user_id == user_id,
                        PlanVersionRecord.session_id == session_id,
                    )
                    .order_by(PlanVersionRecord.created_at, PlanVersionRecord.id)
                )
            )

    def active_constraints(
        self,
        user_id: str,
        session_id: str,
    ) -> dict | None:
        snapshot = self.get_session_snapshot(user_id, session_id)
        if snapshot is None or snapshot.active_plan_version_id is None:
            return None
        with self._session_factory() as database:
            version = database.scalar(
                select(PlanVersionRecord).where(
                    PlanVersionRecord.id == snapshot.active_plan_version_id,
                    PlanVersionRecord.user_id == user_id,
                    PlanVersionRecord.session_id == session_id,
                )
            )
            if version is None:
                return None
            return json.loads(version.normalized_constraints_json)

    def select_plan(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
    ) -> SessionSnapshotRecord:
        """把一次显式选择持久化为当前 active Plan Version 的 selected_plan。

        校验链：会话归属 -> 存在 active version -> Plan 存在且属于本会话 ->
        Plan 属于当前 active version。任一不满足都抛 LookupError。
        """
        if self.get_session(user_id, session_id) is None:
            raise LookupError("session not found")
        now = utc_now()
        with self._session_factory.begin() as database:
            snapshot = database.scalar(
                select(SessionSnapshotRecord).where(
                    SessionSnapshotRecord.session_id == session_id,
                    SessionSnapshotRecord.user_id == user_id,
                )
            )
            if snapshot is None or snapshot.active_plan_version_id is None:
                raise LookupError("no active plan version")
            plan = database.scalar(
                select(PlanRecord).where(
                    PlanRecord.id == plan_id,
                    PlanRecord.user_id == user_id,
                    PlanRecord.session_id == session_id,
                )
            )
            if plan is None:
                raise LookupError("plan not found")
            active_version = database.scalar(
                select(PlanVersionRecord).where(
                    PlanVersionRecord.id == snapshot.active_plan_version_id,
                    PlanVersionRecord.user_id == user_id,
                    PlanVersionRecord.session_id == session_id,
                )
            )
            if (
                active_version is None
                or plan.planning_run_id != active_version.planning_run_id
            ):
                raise LookupError("plan is not part of the active plan version")
            snapshot.selected_plan_id = plan_id
            snapshot.updated_at = now
        return snapshot

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
        """返回 active Plan Version 的候选方案，避免从响应快照反向拼装。

        active version 由 session_snapshots 决定；旧库无 snapshot 的会话回退到
        最近一次响应的 plans（历史兼容），而不是把不同轮次方案混在一起。
        """
        snapshot = self.get_session_snapshot(user_id, session_id)
        if snapshot is not None:
            if snapshot.active_plan_version_id is None:
                return []
            with self._session_factory() as database:
                version = database.scalar(
                    select(PlanVersionRecord).where(
                        PlanVersionRecord.id == snapshot.active_plan_version_id,
                        PlanVersionRecord.user_id == user_id,
                        PlanVersionRecord.session_id == session_id,
                    )
                )
                if version is None:
                    return []
                records = database.scalars(
                    select(PlanRecord)
                    .where(
                        PlanRecord.user_id == user_id,
                        PlanRecord.session_id == session_id,
                        PlanRecord.planning_run_id == version.planning_run_id,
                    )
                    .order_by(PlanRecord.created_at, PlanRecord.id)
                )
                return [json.loads(record.payload_json) for record in records]
        latest = self.latest_response(user_id, session_id)
        if latest is not None:
            return list(latest.get("plans", []))
        return []


def _session_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:30] or "新规划"

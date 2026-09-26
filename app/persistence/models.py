"""业务数据库 ORM 模型。

所有业务表从第一天携带 user_id，为匿名身份、多会话隔离和未来登录迁移留出
空间。LangGraph 自身的 checkpoint 表由 SqliteSaver 管理，不在这里重复定义。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.database import Base


def utc_now() -> datetime:
    """数据库统一写入带时区的 UTC 时间，展示时再转换到用户时区。"""
    return datetime.now(timezone.utc)


class UserRecord(Base):
    """最小用户主体；M1 主要保存 demo-user。"""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SessionRecord(Base):
    """一次独立规划会话，和 LangGraph thread_id 一一对应。"""
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(120), default="新规划")
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class MessageRecord(Base):
    """前后端可读取的用户/助手消息历史。"""
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PlanningRunRecord(Base):
    """一次逻辑消息提交的持久化生命周期和可恢复响应。"""
    __tablename__ = "planning_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "session_id",
            "request_id",
            name="uq_planning_run_request",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.id"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class PlanRecord(Base):
    """一个 Planning Run 产生的不可变候选方案快照。"""
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "composition_fingerprint",
            name="uq_run_plan_composition",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.id"),
        nullable=False,
        index=True,
    )
    planning_run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("planning_runs.id"),
        nullable=True,
        index=True,
    )
    composition_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PlanVersionRecord(Base):
    """一次成功 Planning Run 产生的不可变规划结果组，含本轮约束快照。

    一个 Plan Version 可以包含多个候选 Plan；候选通过 planning_run_id 关联
    到同一 Planning Run。只有真正返回候选方案的成功 run 才创建版本。
    """

    __tablename__ = "plan_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.id"),
        nullable=False,
        index=True,
    )
    planning_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("planning_runs.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    normalized_constraints_json: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SessionSnapshotRecord(Base):
    """会话当前 active Plan Version 与用户已选方案的业务持久化状态。

    这是业务事实，不是 React state 或 Graph checkpoint；读取和写入都按
    user_id + session_id 隔离。旧库中没有 snapshot 的会话按空状态读取。
    """

    __tablename__ = "session_snapshots"

    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.id"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    active_plan_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class TraceEventRecord(Base):
    """为后续可观测性预留的节点事件表；M1 尚未写入事件。"""
    __tablename__ = "trace_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

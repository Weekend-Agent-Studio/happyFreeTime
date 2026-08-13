"""业务数据库 ORM 模型。

所有业务表从第一天携带 user_id，为匿名身份、多会话隔离和未来登录迁移留出
空间。LangGraph 自身的 checkpoint 表由 SqliteSaver 管理，不在这里重复定义。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
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


class PlanRecord(Base):
    """当前会话最新候选方案的 JSON 快照。"""
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.id"),
        nullable=False,
        index=True,
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TraceEventRecord(Base):
    """为后续可观测性预留的节点事件表；M1 尚未写入事件。"""
    __tablename__ = "trace_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

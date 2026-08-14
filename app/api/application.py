"""FastAPI 组合根：连接 HTTP、LangGraph、业务数据库和 checkpoint 数据库。"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.api.schemas import (
    AgentResponse,
    ConstraintSummaryItem,
    MessageRequest,
    ResponseEnvelope,
)
from app.domain.constraints import ActorContext, IdentityType
from app.orchestration.entry_graph import (
    EnvironmentProvider,
    Router,
    build_entry_graph,
    checkpoint_serializer,
)
from app.persistence.database import Database
from app.persistence.repositories import SessionRepository


def create_app(
    *,
    database_path: Path,
    router: Router,
    environment_provider: EnvironmentProvider,
) -> FastAPI:
    """创建可注入依赖的应用实例。

    测试传入临时数据库、规则 Router 和固定环境；生产传入文件数据库、真实或
    Demo Router 和系统环境。这样 API 行为可以端到端测试而不访问外部模型。
    """
    database = Database(database_path)
    database.create_schema()
    repository = SessionRepository(database.session_factory)
    # 业务表和 checkpoint 可以位于同一 SQLite 文件，但职责不同：Repository
    # 保存产品可读的数据，SqliteSaver 保存 Graph 暂停位置和内部状态。
    checkpoint_connection = sqlite3.connect(database_path, check_same_thread=False)
    checkpointer = SqliteSaver(
        checkpoint_connection,
        serde=checkpoint_serializer(),
    )
    graph = build_entry_graph(
        router=router,
        environment_provider=environment_provider,
        checkpointer=checkpointer,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        checkpoint_connection.close()
        database.close()

    app = FastAPI(title="HappyFreeTime V2", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def actor_for(user_id: str, session_id: str) -> ActorContext:
        """把 HTTP 身份和会话标识转换成 Graph 统一使用的调用者上下文。"""
        return ActorContext(
            user_id=user_id,
            session_id=session_id,
            identity_type=IdentityType.DEMO,
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict[str, str]]:
        session = repository.create_session(
            user_id=x_user_id,
            identity_type=IdentityType.DEMO.value,
        )
        return ResponseEnvelope(
            data={"session_id": session.id, "title": session.title}
        )

    @app.get("/api/sessions/{session_id}")
    def get_session(
        session_id: str,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict]:
        session = repository.get_session(x_user_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return ResponseEnvelope(
            data={
                "session_id": session.id,
                "title": session.title,
                "status": session.status,
                "messages": [
                    {
                        "id": message.id,
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in repository.list_messages(x_user_id, session_id)
                ],
                "plans": repository.list_plans(x_user_id, session_id),
            }
        )

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(
        session_id: str,
        request: MessageRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[AgentResponse]:
        if repository.get_session(x_user_id, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        repository.add_message(
            user_id=x_user_id,
            session_id=session_id,
            role="user",
            content=request.content,
        )

        actor = actor_for(x_user_id, session_id)
        config = {"configurable": {"thread_id": session_id}}
        # checkpoint 中存在未完成 interrupt，说明本条消息是上一问题的答案；
        # 否则将它作为新一轮用户目标调用 Graph。前端无需理解 Graph 状态机。
        snapshot = graph.get_state(config)
        if snapshot.next and snapshot.interrupts:
            result = graph.invoke(Command(resume=request.content), config=config)
        else:
            result = graph.invoke(
                {"user_input": request.content, "actor": actor},
                config=config,
            )

        # invoke() 可能再次停在 interrupt。这里读取持久化后的最新状态，而不是
        # 根据 result 猜测，然后转换成前端只需理解的 needs_input 响应。
        current = graph.get_state(config)
        if current.next and current.interrupts:
            question = current.interrupts[0].value
            repository.add_message(
                user_id=x_user_id,
                session_id=session_id,
                role="assistant",
                content=question["question"],
            )
            return ResponseEnvelope(
                data=AgentResponse(
                    status="needs_input",
                    question=question,
                    assumptions=_dump_assumptions(result),
                    constraint_summary=_dump_constraint_summary(result),
                )
            )

        interpretation = result.get("interpretation")
        candidate_set = result.get("candidate_set")
        reply = interpretation.reply if interpretation else ""
        plans = candidate_set.plans if candidate_set else []
        conflict = candidate_set.conflict if candidate_set else None
        # 方案与冲突是互斥的 Planner 结果。只有新方案成功生成时才整体替换
        # 数据库快照；冲突不会抹去 API 之外可能需要审计的消息历史。
        if plans:
            repository.replace_plans(
                user_id=x_user_id,
                session_id=session_id,
                plans=plans,
            )
            reply = f"已生成 {len(plans)} 个候选方案。"
        elif conflict is not None:
            reply = conflict.message
        if reply:
            repository.add_message(
                user_id=x_user_id,
                session_id=session_id,
                role="assistant",
                content=reply,
            )

        return ResponseEnvelope(
            data=AgentResponse(
                status="completed",
                reply=reply,
                assumptions=_dump_assumptions(result),
                constraint_summary=_dump_constraint_summary(result),
                plans=[plan.model_dump(mode="json") for plan in plans],
                conflict=conflict.model_dump(mode="json") if conflict else None,
            )
        )

    return app


def _dump_assumptions(result: dict) -> list[dict]:
    enrichment = result.get("enrichment")
    if enrichment is None:
        return []
    return [item.model_dump(mode="json") for item in enrichment.assumptions]


def _dump_constraint_summary(result: dict) -> list[ConstraintSummaryItem]:
    """把规划实际使用的约束转换成稳定的前端摘要。"""
    enrichment = result.get("enrichment")
    if enrichment is None:
        return []
    summary = []
    for field in (
        "date",
        "time_window",
        "duration_minutes",
        "location",
        "party",
        "budget_per_person",
        "max_distance_km",
    ):
        constraint = getattr(enrichment.constraints, field)
        if constraint is None:
            continue
        summary.append(
            ConstraintSummaryItem(
                field=field,
                value=constraint.model_dump(mode="json")["value"],
                source=constraint.source,
                evidence=constraint.raw_text,
                confidence=constraint.confidence,
                rule_id=constraint.rule_id,
            )
        )
    return summary

"""FastAPI 组合根：连接 HTTP、LangGraph、业务数据库和 checkpoint 数据库。"""

from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite import SqliteSaver

from app.api.schemas import (
    AgentResponse,
    CurrentRequestReplanRequest,
    MessageRequest,
    PlanningContextUpdateRequest,
    PlanVersionSummary,
    ResponseEnvelope,
    SessionMessageResponse,
    SessionSummaryResponse,
    SessionTitleUpdateRequest,
)
from app.domain.constraints import (
    IdentityType,
    PlanRequest,
)
from app.providers.weather import WeatherProvider
from app.providers.route import RouteProvider
from app.providers.geocoding import GeocodingProvider
from app.providers.availability import AvailabilityProvider
from app.providers.web_map import DisabledWebMapProvider, WebMapProvider
from app.services.catalog import Catalog
from app.services.candidate_retriever import CandidateRetriever
from app.api.planning_context import PlanningContextApplication
from app.api.planning_turn import PlanningApplicationError, PlanningTurnApplication
from app.domain.run_trace import PlanningRunEvent
from app.services.planning_intent import PlanningIntentProvider
from app.services.recommendation_advisor import (
    RecommendationAdvisor,
    build_default_recommendation_advisor,
)
from app.services.poi_presentation import EmptyPoiPresentationProvider, PoiPresentationProvider
from app.orchestration.entry_graph import (
    EnvironmentProvider,
    TurnInterpreter,
    build_entry_graph,
    checkpoint_config,
    checkpoint_serializer,
)
from app.persistence.database import Database
from app.persistence.repositories import SessionRepository


def create_app(
    *,
    database_path: Path,
    router: TurnInterpreter,
    environment_provider: EnvironmentProvider,
    weather_provider: WeatherProvider | None = None,
    route_provider: RouteProvider | None = None,
    geocoding_provider: GeocodingProvider | None = None,
    availability_provider: AvailabilityProvider | None = None,
    catalog: Catalog | None = None,
    planning_intent_provider: PlanningIntentProvider | None = None,
    candidate_retriever: CandidateRetriever | None = None,
    recommendation_advisor: RecommendationAdvisor | None = None,
    poi_presentation_provider: PoiPresentationProvider | None = None,
    web_map_provider: WebMapProvider | None = None,
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
        weather_provider=weather_provider,
        route_provider=route_provider,
        geocoding_provider=geocoding_provider,
        availability_provider=availability_provider,
        catalog=catalog,
        planning_intent_provider=planning_intent_provider,
        candidate_retriever=candidate_retriever,
        checkpointer=checkpointer,
    )
    browser_map = web_map_provider or DisabledWebMapProvider()
    presentation_provider = poi_presentation_provider or EmptyPoiPresentationProvider()
    advisor = recommendation_advisor or build_default_recommendation_advisor()
    planning_context_application = PlanningContextApplication(
        geocoding_provider=geocoding_provider,
    )

    planning_turn_application = PlanningTurnApplication(
        repository=repository,
        graph=graph,
        environment_provider=environment_provider,
        planning_context_application=planning_context_application,
        recommendation_advisor=advisor,
        poi_presentation_provider=presentation_provider,
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
    app.state.planning_turn_application = planning_turn_application
    app.state.session_repository = repository

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config/map")
    def map_config() -> ResponseEnvelope[dict]:
        return ResponseEnvelope(data=browser_map.public_config().model_dump(mode="json"))

    @app.get("/_AMapService/{path:path}", include_in_schema=False)
    def proxy_amap_browser_request(path: str, request: Request) -> Response:
        try:
            upstream = browser_map.proxy(path, list(request.query_params.multi_items()))
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=502,
                detail="Amap browser map upstream request failed",
            ) from error
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.content_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

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

    @app.get("/api/sessions")
    def list_sessions(
        limit: int = Query(default=5, ge=1, le=20),
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict[str, list[SessionSummaryResponse]]]:
        sessions = repository.list_recent_sessions(user_id=x_user_id, limit=limit)
        return ResponseEnvelope(
            data={
                "sessions": [
                    SessionSummaryResponse.model_validate(summary, from_attributes=True)
                    for summary in sessions
                ]
            }
        )

    @app.patch("/api/sessions/{session_id}")
    def update_session_title(
        session_id: str,
        payload: SessionTitleUpdateRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict[str, str]]:
        session = repository.update_session_title(
            user_id=x_user_id,
            session_id=session_id,
            title=payload.title,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return ResponseEnvelope(data={"session_id": session.id, "title": session.title})

    @app.delete("/api/sessions/{session_id}")
    def delete_session(
        session_id: str,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict[str, bool]]:
        if repository.get_session(x_user_id, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not repository.delete_session(user_id=x_user_id, session_id=session_id):
            raise HTTPException(status_code=404, detail="session not found")
        configurable = checkpoint_config(session_id)["configurable"]
        checkpointer.delete_thread(configurable["thread_id"])
        return ResponseEnvelope(data={"deleted": True})

    @app.get("/api/sessions/{session_id}")
    def get_session(
        session_id: str,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict]:
        session = repository.get_session(x_user_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        snapshot = repository.get_session_snapshot(x_user_id, session_id)
        graph_snapshot = graph.get_state(checkpoint_config(session_id))
        graph_values = getattr(graph_snapshot, "values", None) or {}
        request_value = graph_values.get("active_request")
        if request_value is None:
            request_value = repository.active_constraints(x_user_id, session_id)
        active_request = (
            request_value if isinstance(request_value, PlanRequest)
            else PlanRequest.model_validate(request_value) if request_value is not None
            else PlanRequest()
        )
        pending_value = (
            graph_snapshot.interrupts[0].value
            if graph_snapshot.next and graph_snapshot.interrupts
            else graph_values.get("pending_issue")
        )
        pending_field = (
            pending_value.get("field") if isinstance(pending_value, dict)
            else getattr(pending_value, "field", None)
        )
        return ResponseEnvelope(
            data={
                "session_id": session.id,
                "title": session.title,
                "status": session.status,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "messages": [
                    SessionMessageResponse.model_validate(message, from_attributes=True)
                    for message in repository.list_messages(x_user_id, session_id)
                ],
                "plans": repository.list_plans(x_user_id, session_id),
                "latest_response": repository.latest_response(x_user_id, session_id),
                "response_history": repository.response_history(x_user_id, session_id),
                "active_plan_version_id": (
                    snapshot.active_plan_version_id if snapshot else None
                ),
                "selected_plan_id": snapshot.selected_plan_id if snapshot else None,
                "active_constraints": repository.active_constraints(x_user_id, session_id),
                "planning_context": planning_turn_application.project_context(
                    x_user_id,
                    session_id,
                    active_request,
                    pending_field=pending_field,
                ).model_dump(mode="json"),
                "plan_versions": [
                    PlanVersionSummary(
                        plan_version_id=version.id,
                        planning_run_id=version.planning_run_id,
                        supersedes_version_id=version.supersedes_version_id,
                        created_at=version.created_at,
                    ).model_dump(mode="json")
                    for version in repository.list_plan_versions(x_user_id, session_id)
                ],
            }
        )

    @app.post("/api/sessions/{session_id}/plans/{plan_id}/select")
    def select_plan(
        session_id: str,
        plan_id: str,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict]:
        if repository.get_session(x_user_id, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            snapshot = repository.select_plan(
                user_id=x_user_id,
                session_id=session_id,
                plan_id=plan_id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return ResponseEnvelope(
            data={
                "active_plan_version_id": snapshot.active_plan_version_id,
                "selected_plan_id": snapshot.selected_plan_id,
            }
        )

    def _run_planning_turn(
        session_id: str,
        request: MessageRequest,
        user_id: str,
    ) -> ResponseEnvelope[AgentResponse]:
        try:
            response = planning_turn_application.handle(
                session_id=session_id,
                request=request,
                user_id=user_id,
            )
        except PlanningApplicationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
            ) from error
        return ResponseEnvelope(data=response)

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(
        session_id: str,
        request: MessageRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[AgentResponse]:
        return _run_planning_turn(session_id, request, x_user_id)

    @app.post("/api/sessions/{session_id}/messages/stream")
    async def send_message_stream(
        session_id: str,
        request: MessageRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> StreamingResponse:
        """Stream safe lifecycle events while reusing the normal application service."""

        events: queue.Queue[tuple[str, object | None]] = queue.Queue()

        def emit_progress(event: PlanningRunEvent) -> None:
            events.put(("progress", event))

        def execute() -> None:
            try:
                response = planning_turn_application.handle(
                    session_id=session_id,
                    request=request,
                    user_id=x_user_id,
                    event_sink=emit_progress,
                )
                events.put(("result", response))
            except PlanningApplicationError as error:
                events.put(("error", {"detail": error.detail, "status_code": error.status_code}))
            except Exception:
                # Never expose stack traces or provider exception text through SSE.
                events.put(("error", {"detail": "规划执行失败，请稍后重试。", "status_code": 500}))
            finally:
                events.put(("done", None))

        worker = asyncio.create_task(asyncio.to_thread(execute))

        async def body():
            while True:
                kind, payload = await asyncio.to_thread(events.get)
                if kind == "progress" and isinstance(payload, PlanningRunEvent):
                    yield _sse_frame("progress", payload.model_dump(mode="json"))
                elif kind == "result" and isinstance(payload, AgentResponse):
                    yield _sse_frame("result", {"data": payload.model_dump(mode="json")})
                elif kind == "error" and isinstance(payload, dict):
                    yield _sse_frame("error", payload)
                elif kind == "done":
                    break
            await worker

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.patch("/api/sessions/{session_id}/planning-context")
    def update_planning_context(
        session_id: str,
        request: PlanningContextUpdateRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[AgentResponse]:
        return send_message(
            session_id,
            MessageRequest(
                request_id=request.request_id,
                content="顶部条件更新",
                planning_context_patch=request.patch,
                defer_planning=True,
            ),
            x_user_id,
        )

    @app.post("/api/sessions/{session_id}/planning-context/replan")
    def replan_saved_context(
        session_id: str,
        request: CurrentRequestReplanRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[AgentResponse]:
        return send_message(
            session_id,
            MessageRequest(
                request_id=request.request_id,
                content="按当前保存的规划条件重新规划",
                replan_current_request=True,
            ),
            x_user_id,
        )

    return app


def _sse_frame(event_name: str, payload: dict) -> str:
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )

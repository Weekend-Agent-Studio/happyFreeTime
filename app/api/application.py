"""FastAPI 组合根：连接 HTTP、LangGraph、业务数据库和 checkpoint 数据库。"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.api.schemas import (
    AgentResponse,
    ConstraintSummaryItem,
    MessageRequest,
    PlanVersionSummary,
    ResponseEnvelope,
    SessionMessageResponse,
    SessionSummaryResponse,
)
from app.domain.constraints import ActorContext, IdentityType
from app.providers.weather import WeatherProvider
from app.providers.route import RouteProvider
from app.providers.geocoding import GeocodingProvider
from app.providers.availability import AvailabilityProvider
from app.providers.web_map import DisabledWebMapProvider, WebMapProvider
from app.services.catalog import Catalog
from app.services.planning_intent import PlanningIntentProvider
from app.services.presenter import present_candidate_set
from app.services.poi_presentation import EmptyPoiPresentationProvider, PoiPresentationProvider
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
    weather_provider: WeatherProvider | None = None,
    route_provider: RouteProvider | None = None,
    geocoding_provider: GeocodingProvider | None = None,
    availability_provider: AvailabilityProvider | None = None,
    catalog: Catalog | None = None,
    planning_intent_provider: PlanningIntentProvider | None = None,
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
        checkpointer=checkpointer,
    )
    browser_map = web_map_provider or DisabledWebMapProvider()
    presentation_provider = poi_presentation_provider or EmptyPoiPresentationProvider()

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

    @app.get("/api/sessions/{session_id}")
    def get_session(
        session_id: str,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[dict]:
        session = repository.get_session(x_user_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        snapshot = repository.get_session_snapshot(x_user_id, session_id)
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

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(
        session_id: str,
        request: MessageRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[AgentResponse]:
        if repository.get_session(x_user_id, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")

        try:
            run = repository.begin_planning_run(
                user_id=x_user_id,
                session_id=session_id,
                request_id=request.request_id,
                content=request.content,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if run.stored_response is not None:
            return ResponseEnvelope(data=AgentResponse.model_validate(run.stored_response))
        if not run.should_execute:
            raise HTTPException(status_code=409, detail="planning run is already in progress")

        try:
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
                response = AgentResponse(
                    status="needs_input",
                    question=question,
                    assumptions=_dump_assumptions(result),
                    constraint_summary=_dump_constraint_summary(result),
                    provider_facts=[],
                    catalog_violations=[],
                    catalog_warnings=[],
                    warnings=[],
                    poi_presentations=[],
                )
                repository.complete_planning_run(
                    user_id=x_user_id,
                    session_id=session_id,
                    planning_run_id=run.planning_run_id,
                    status="needs_input",
                    response=response.model_dump(mode="json"),
                    assistant_content=question["question"],
                    plans=[],
                )
                return ResponseEnvelope(data=response)

            interpretation = result.get("interpretation")
            candidate_set = result.get("candidate_set")
            reply = interpretation.reply if interpretation else ""
            plans = candidate_set.plans if candidate_set else []
            conflict = candidate_set.conflict if candidate_set else None
            if plans:
                reply = present_candidate_set(
                    candidate_set,
                    result["enrichment"].constraints,
                )
            elif conflict is not None:
                reply = conflict.message

            plan_version_id = uuid.uuid4().hex if plans else None
            enrichment = result.get("enrichment")
            normalized_constraints_json = (
                enrichment.constraints.model_dump_json()
                if plans and enrichment is not None
                else None
            )

            response = AgentResponse(
                status="completed",
                reply=reply,
                assumptions=_dump_assumptions(result),
                constraint_summary=_dump_constraint_summary(result),
                plans=[plan.model_dump(mode="json") for plan in plans],
                conflict=conflict.model_dump(mode="json") if conflict else None,
                provider_facts=(
                    [fact.model_dump(mode="json") for fact in candidate_set.provider_facts]
                    if candidate_set
                    else []
                ),
                catalog_violations=(
                    [
                        item.model_dump(mode="json")
                        for item in candidate_set.catalog_violations
                    ]
                    if candidate_set
                    else []
                ),
                catalog_warnings=(
                    [
                        item.model_dump(mode="json")
                        for item in candidate_set.catalog_warnings
                    ]
                    if candidate_set
                    else []
                ),
                warnings=(
                    [item.model_dump(mode="json") for item in candidate_set.warnings]
                    if candidate_set
                    else []
                ),
                poi_presentations=[
                    item.model_dump(mode="json")
                    for item in presentation_provider.present_many(
                        [
                            stop.resource_id
                            for plan in plans
                            for stop in plan.stops
                        ]
                    )
                ],
                plan_version_id=plan_version_id,
                planning_intent_decision=(
                    candidate_set.planning_intent_decision.model_dump(mode="json")
                    if candidate_set and candidate_set.planning_intent_decision is not None
                    else None
                ),
            )
            repository.complete_planning_run(
                user_id=x_user_id,
                session_id=session_id,
                planning_run_id=run.planning_run_id,
                status="completed",
                response=response.model_dump(mode="json"),
                assistant_content=reply,
                plans=plans,
                plan_version_id=plan_version_id,
                normalized_constraints_json=normalized_constraints_json,
            )
            return ResponseEnvelope(data=response)
        except Exception:
            repository.fail_planning_run(
                user_id=x_user_id,
                session_id=session_id,
                planning_run_id=run.planning_run_id,
            )
            raise

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
        "departure_at",
        "exact_stop_count",
        "required_stop_roles",
        "duration_minutes",
        "location",
        "party",
        "budget_per_person",
        "max_distance_km",
        "return_by",
        "total_distance_km",
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

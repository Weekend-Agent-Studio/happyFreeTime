"""FastAPI 组合根：连接 HTTP、LangGraph、业务数据库和 checkpoint 数据库。"""

from __future__ import annotations

import sqlite3
import uuid
import json
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
    PlanningContextField,
    PlanningContextPatch,
    PlanningContextSummary,
    PlanningContextWhen,
    PlanningContextUpdateRequest,
    PlanVersionSummary,
    ResponseEnvelope,
    SessionMessageResponse,
    SessionSummaryResponse,
)
from app.domain.constraints import (
    ActorContext,
    ClarificationAction,
    ClarificationIssue,
    CommandOperation,
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    IdentityType,
    PartyProfile,
    PlanRequest,
    RequestPatch,
)
from app.domain.planning import Plan
from app.domain.recommendation import RecommendationAdvice, RecommendationAdviceRequest
from app.domain.providers import WeatherFact
from app.domain.providers import GeocodeRequest, GeocodeResolution
from app.domain.semantics import SemanticRequest
from app.domain.runtime import RuntimeDecision
from app.providers.weather import WeatherProvider
from app.providers.route import RouteProvider
from app.providers.geocoding import GeocodingProvider
from app.providers.availability import AvailabilityProvider
from app.providers.web_map import DisabledWebMapProvider, WebMapProvider
from app.services.catalog import Catalog
from app.services.candidate_retriever import CandidateRetriever
from app.services.planning_intent import PlanningIntentProvider
from app.services.recommendation_advisor import (
    RecommendationAdvisor,
    build_default_recommendation_advisor,
)
from app.services.presenter import present_candidate_set
from app.services.poi_presentation import EmptyPoiPresentationProvider, PoiPresentationProvider
from app.orchestration.entry_graph import (
    EnvironmentProvider,
    Router,
    build_entry_graph,
    checkpoint_config,
    checkpoint_serializer,
)
from app.persistence.database import Database
from app.persistence.repositories import SessionRepository


def _context_source(source: ConstraintSource | None) -> str:
    if source in {
        ConstraintSource.USER_EXPLICIT,
        ConstraintSource.USER_INFERRED,
        ConstraintSource.SESSION_CONFIRMED,
        ConstraintSource.MEMORY,
    }:
        return "user"
    if source in {ConstraintSource.DERIVED, ConstraintSource.REAL_TOOL}:
        return "derived"
    return "default"


def _context_field(
    value: object = None,
    *,
    source: ConstraintSource | None = None,
    display: str | None = None,
    pending: bool = False,
) -> PlanningContextField:
    present = value is not None
    mapped_source = _context_source(source)
    return PlanningContextField(
        value=value,
        display_value=display or (str(value) if present else "未设置"),
        source=mapped_source,
        status=("pending" if pending else "resolved" if present and mapped_source != "default" else "assumed"),
    )


def project_planning_context(
    request: PlanRequest | None,
    *,
    pending_field: str | None = None,
) -> PlanningContextSummary:
    """Build the UI summary only from the canonical request and current issue."""
    active = request or PlanRequest()
    window = active.planning_window
    location = active.location
    party = active.party
    budget = active.budget_per_person
    pending_where = pending_field == "location"
    pending_start = pending_field in {"time_window", "departure_at"}
    pending_end = pending_field in {"time_window", "return_by"}
    pending_date = pending_field == "date"

    date_value = window.date.value if window.date else None
    start_value = window.start_at.value if window.start_at else None
    end_value = window.end_at.value if window.end_at else None
    party_value = party.value if party else PartyProfile()
    preferences = {
        "preferences": list(active.preferences),
        "diet_tags": list(active.diet_tags),
        "scene_tags": list(active.scene_tags),
        "avoid": list(active.avoid),
    }
    preference_values = list(dict.fromkeys(
        value
        for values in preferences.values()
        for value in values
    ))
    return PlanningContextSummary(
        request_revision=active.revision,
        where=_context_field(
            location.value.model_dump(mode="json") if location else None,
            source=location.source if location else None,
            display=location.value.address if location else None,
            pending=pending_where,
        ),
        when=PlanningContextWhen(
            date=_context_field(
                date_value.isoformat() if date_value else None,
                source=window.date.source if window.date else None,
                display=date_value.isoformat() if date_value else None,
                pending=pending_date,
            ),
            start_at=_context_field(
                start_value,
                source=window.start_at.source if window.start_at else None,
                display=start_value,
                pending=pending_start,
            ),
            end_at=_context_field(
                end_value,
                source=window.end_at.source if window.end_at else None,
                display=end_value,
                pending=pending_end,
            ),
            start_kind=window.start_kind,
            end_kind=window.end_kind,
        ),
        who=_context_field(
            party_value.model_dump(mode="json"),
            source=party.source if party else None,
            display=(
                f"{party_value.adults} 位成人 · {party_value.children} 位儿童"
                + (f"（{party_value.child_age} 岁）" if party_value.child_age is not None else "")
            ),
            pending=pending_field in {"party", "child_age"},
        ),
        budget=_context_field(
            {
                "mode": "per_person" if budget else "unlimited",
                "amount": budget.value if budget else None,
                "strict": active.strict_budget,
            },
            source=budget.source if budget else None,
            display=(f"人均 ¥{budget.value}" if budget else "不限")
                + (" · 严格" if budget and active.strict_budget else ""),
            pending=pending_field == "budget_per_person",
        ),
        preferences=_context_field(
            preferences,
            source=(ConstraintSource.USER_EXPLICIT if preference_values else None),
            display="、".join(preference_values) if preference_values else "未设置",
            pending=pending_field in {"preferences", "diet_tags", "scene_tags", "avoid"},
        ),
        pending_field=pending_field,
    )


def compile_planning_context_patch(
    patch: PlanningContextPatch,
    *,
    request: PlanRequest,
    geocoding_provider: GeocodingProvider | None,
    environment: object,
    pending_field: str | None = None,
) -> tuple[RequestPatch, tuple[ClarificationIssue, ...]]:
    """Translate closed UI DTOs into the server-only atomic RequestPatch."""
    set_fields: dict[str, object] = {}
    clear_fields: list[str] = []
    add_to_fields: dict[str, tuple[str, ...]] = {}
    remove_from_fields: dict[str, tuple[str, ...]] = {}
    issues: list[ClarificationIssue] = []

    if patch.where is not None:
        if patch.where.operation == "clear":
            clear_fields.append("location")
        else:
            location_text = (patch.where.value or "").strip()
            if not location_text:
                issues.append(ClarificationIssue(
                    field="location", code="LOCATION_TEXT_REQUIRED",
                    reason="location_text_is_empty", expected_value_type="location",
                    request_revision=patch.base_revision,
                ))
            elif geocoding_provider is None:
                issues.append(ClarificationIssue(
                    field="location", code="LOCATION_GEOCODER_UNAVAILABLE",
                    reason="location_provider_unavailable", expected_value_type="location",
                    request_revision=patch.base_revision,
                ))
            else:
                city = (
                    request.location.value.city if request.location is not None
                    else environment.default_location.city if getattr(environment, "default_location", None)
                    else None
                )
                try:
                    fact = geocoding_provider.geocode(GeocodeRequest(location_text=location_text, city=city))
                except Exception:
                    fact = None
                if fact is None or fact.resolution != GeocodeResolution.RESOLVED or fact.point is None:
                    resolution = fact.resolution.value if fact is not None else "provider_error"
                    issues.append(ClarificationIssue(
                        field="location", code=f"LOCATION_{resolution.upper()}",
                        reason="location_must_resolve_to_one_place", expected_value_type="location",
                        request_revision=patch.base_revision,
                    ))
                else:
                    set_fields["location"] = ConstraintValue(
                        value=GeoLocation(
                            city=fact.city or city or "",
                            district=fact.district or "",
                            address=fact.address or location_text,
                            latitude=fact.point.latitude,
                            longitude=fact.point.longitude,
                            adcode=fact.adcode,
                        ),
                        source=ConstraintSource.USER_EXPLICIT,
                        raw_text=location_text,
                        rule_id="planning_context.where.v1",
                    )

    if patch.when is not None:
        for field_name, edit in (
            ("planning_window.date", patch.when.date),
            ("planning_window.start_at", patch.when.start_at),
            ("planning_window.end_at", patch.when.end_at),
        ):
            if edit is None:
                continue
            if edit.operation == "clear":
                clear_fields.append(field_name)
                if field_name.endswith("start_at"):
                    set_fields["planning_window.start_kind"] = "trip_start"
                if field_name.endswith("end_at"):
                    set_fields["planning_window.end_kind"] = "trip_end"
                continue
            raw_value = edit.value
            if field_name.endswith("date"):
                raw_value = raw_value
            kind_field = None
            if field_name.endswith("start_at"):
                kind_field = "planning_window.start_kind"
                kind_value = "departure" if pending_field == "departure_at" else request.planning_window.start_kind
            elif field_name.endswith("end_at"):
                kind_field = "planning_window.end_kind"
                kind_value = "return_deadline" if pending_field == "return_by" else request.planning_window.end_kind
            if kind_field is not None:
                set_fields[kind_field] = kind_value
            set_fields[field_name] = ConstraintValue(
                value=raw_value,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=str(raw_value),
                rule_id=f"planning_context.{field_name}.v1",
            )

    if patch.who is not None:
        current = request.party.value if request.party else PartyProfile()
        adults = patch.who.adults if patch.who.adults is not None else current.adults
        children = patch.who.children if patch.who.children is not None else current.children
        child_age = current.child_age
        members = list(current.members)
        if patch.who.child_age is not None:
            child_age = patch.who.child_age.value if patch.who.child_age.operation == "set" else None
        if patch.who.members is not None:
            members = list(patch.who.members.value or []) if patch.who.members.operation == "set" else []
        set_fields["party"] = ConstraintValue(
            value=PartyProfile(adults=adults, children=children, child_age=child_age, members=members),
            source=ConstraintSource.USER_EXPLICIT,
            raw_text="顶部同行人设置",
            rule_id="planning_context.who.v1",
        )

    if patch.budget is not None:
        if patch.budget.mode == "unlimited":
            clear_fields.append("budget_per_person")
            set_fields["strict_budget"] = False
        else:
            set_fields["budget_per_person"] = ConstraintValue(
                value=patch.budget.amount,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=f"人均预算 {patch.budget.amount}",
                rule_id="planning_context.budget.v1",
            )
            set_fields["strict_budget"] = patch.budget.strict

    if patch.preferences is not None:
        additions = tuple(patch.preferences.add)
        removals = tuple(patch.preferences.remove)
        if additions:
            add_to_fields["preferences"] = additions
        if removals:
            for field_name in ("preferences", "diet_tags", "scene_tags", "avoid"):
                remove_from_fields[field_name] = removals

    request_patch = RequestPatch(
        base_revision=patch.base_revision,
        set_fields=set_fields,
        clear_fields=tuple(clear_fields),
        add_to_fields=add_to_fields,
        remove_from_fields=remove_from_fields,
        source=ConstraintSource.USER_EXPLICIT,
    )
    return request_patch, tuple(issues)


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
                "planning_context": project_planning_context(
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

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(
        session_id: str,
        request: MessageRequest,
        x_user_id: str = Header(default="demo-user"),
    ) -> ResponseEnvelope[AgentResponse]:
        if repository.get_session(x_user_id, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")

        config = checkpoint_config(session_id)
        pending_snapshot = graph.get_state(config)
        pending_interrupt = (
            pending_snapshot.interrupts[0].value
            if pending_snapshot.next and pending_snapshot.interrupts
            else None
        )
        pending_values = getattr(pending_snapshot, "values", None) or {}
        active_value = pending_values.get("active_request")
        if active_value is None:
            active_value = repository.active_constraints(x_user_id, session_id)
        active_request = (
            active_value if isinstance(active_value, PlanRequest)
            else PlanRequest.model_validate(active_value) if active_value is not None
            else PlanRequest()
        )
        compiled_context_patch: RequestPatch | None = None
        context_patch_issues: tuple[ClarificationIssue, ...] = ()
        if request.planning_context_patch is not None:
            if request.planning_context_patch.base_revision != active_request.revision:
                raise HTTPException(
                    status_code=409,
                    detail="规划条件已更新，请刷新后再修改顶部条件。",
                )
            pending_revision = (
                pending_interrupt.get("request_revision")
                if isinstance(pending_interrupt, dict)
                else None
            )
            if pending_revision is not None and pending_revision != active_request.revision:
                raise HTTPException(
                    status_code=409,
                    detail="当前反问已过期，请先刷新会话后再修改条件。",
                )

        request_content = request.content
        if request.planning_context_patch is not None:
            request_content = "[planning-context]" + json.dumps(
                request.planning_context_patch.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        if request.clarification_reply is not None:
            if pending_interrupt is None:
                raise HTTPException(
                    status_code=409,
                    detail="当前会话没有待处理的反问",
                )
            pending_id = (
                pending_interrupt.get("clarification_id")
                if isinstance(pending_interrupt, dict)
                else None
            )
            if pending_id and request.clarification_reply.clarification_id != pending_id:
                raise HTTPException(
                    status_code=409,
                    detail="反问已更新，请使用当前问题的选项或重新输入",
                )
            pending_revision = (
                pending_interrupt.get("request_revision")
                if isinstance(pending_interrupt, dict)
                else None
            )
            if (
                pending_revision is not None
                and request.clarification_reply.request_revision is not None
                and request.clarification_reply.request_revision != pending_revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail="规划条件已更新，请基于当前条件重新回答",
                )

        # Validate structured replacement anchors before creating a planning
        # run or user message.  Invalid UI commands must be side-effect free;
        # natural-language commands are still interpreted by the Graph and do
        # not enter this boundary check.
        if (
            request.conversation_command is not None
            and request.conversation_command.operation in {
                CommandOperation.REPLACE,
                CommandOperation.PATCH_CONSTRAINTS,
            }
        ):
            command = request.conversation_command
            session_snapshot = repository.get_session_snapshot(
                x_user_id,
                session_id,
            )
            if command.base_plan_version_id is None or (
                command.operation == CommandOperation.REPLACE and command.base_plan_id is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "结构化替换请求必须同时提供 base_plan_version_id "
                        "和 base_plan_id"
                    ),
                )
            if (
                session_snapshot is None
                or command.base_plan_version_id
                != session_snapshot.active_plan_version_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="修改请求基于的方案版本已不是当前 active 版本",
                )
            if (
                command.operation == CommandOperation.REPLACE
                and command.base_plan_id != session_snapshot.selected_plan_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="修改请求基于的方案已不是当前 selected 方案",
                )

        try:
            run = repository.begin_planning_run(
                user_id=x_user_id,
                session_id=session_id,
                request_id=request.request_id,
                content=request_content,
                record_user_message=request.planning_context_patch is None,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if run.stored_response is not None:
            return ResponseEnvelope(data=AgentResponse.model_validate(run.stored_response))
        if not run.should_execute:
            raise HTTPException(status_code=409, detail="planning run is already in progress")

        try:
            actor = actor_for(x_user_id, session_id)
            if request.planning_context_patch is not None:
                compiled_context_patch, context_patch_issues = compile_planning_context_patch(
                    request.planning_context_patch,
                    request=active_request,
                    geocoding_provider=geocoding_provider,
                    environment=environment_provider(actor),
                    pending_field=(
                        pending_interrupt.get("field")
                        if isinstance(pending_interrupt, dict)
                        else None
                    ),
                )
            # checkpoint 中存在未完成 interrupt，说明本条消息是上一问题的答案；
            # 否则将它作为新一轮用户目标调用 Graph。前端无需理解 Graph 状态机。
            snapshot = graph.get_state(config)
            if snapshot.next and snapshot.interrupts:
                if compiled_context_patch is not None:
                    resume_value = {
                        "kind": "planning_context_patch",
                        "clarification_id": (
                            pending_interrupt.get("clarification_id", "legacy")
                            if isinstance(pending_interrupt, dict)
                            else "legacy"
                        ),
                        "request_revision": request.planning_context_patch.base_revision,
                        "request_patch": compiled_context_patch.model_dump(mode="json"),
                        "request_patch_issues": [
                            issue.model_dump(mode="json") for issue in context_patch_issues
                        ],
                    }
                elif request.clarification_reply is not None:
                    resume_value: object = request.clarification_reply.model_dump(mode="json")
                else:
                    # Compatibility for the old input box: plain text is an
                    # answer to the exact pending field, not a new full Router
                    # turn and not a concatenation with the old request.
                    resume_value = {
                        "clarification_id": (
                            pending_interrupt.get("clarification_id", "legacy")
                            if isinstance(pending_interrupt, dict)
                            else "legacy"
                        ),
                        "action": ClarificationAction.ANSWER.value,
                        "value": request.content,
                        "request_revision": (
                            pending_interrupt.get("request_revision")
                            if isinstance(pending_interrupt, dict)
                            else None
                        ),
                    }
                result = graph.invoke(Command(resume=resume_value), config=config)
            elif compiled_context_patch is not None:
                session_snapshot = repository.get_session_snapshot(x_user_id, session_id)
                active_plan_payloads = repository.list_plans(x_user_id, session_id)
                selected_plan = next(
                    (
                        Plan.model_validate(plan)
                        for plan in active_plan_payloads
                        if session_snapshot is not None
                        and plan.get("plan_id") == session_snapshot.selected_plan_id
                    ),
                    None,
                )
                result = graph.invoke(
                    {
                        "actor": actor,
                        "user_input": request.content,
                        "active_request": active_request,
                        "has_plans": bool(active_plan_payloads),
                        "active_plan_version_id": (
                            session_snapshot.active_plan_version_id
                            if session_snapshot is not None
                            else None
                        ),
                        "selected_plan": selected_plan,
                        "request_patch_override": compiled_context_patch,
                        "request_patch_issues": context_patch_issues,
                    },
                    config=config,
                )
            else:
                session_snapshot = repository.get_session_snapshot(
                    x_user_id,
                    session_id,
                )
                if request.conversation_command is not None:
                    command = request.conversation_command
                    if command.operation in {
                        CommandOperation.REPLACE,
                        CommandOperation.PATCH_CONSTRAINTS,
                    } and (
                        command.base_plan_version_id is None
                        or (
                            command.operation == CommandOperation.REPLACE
                            and command.base_plan_id is None
                        )
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "结构化替换请求必须同时提供 base_plan_version_id "
                                "和 base_plan_id"
                            ),
                        )
                    if (
                        command.base_plan_version_id is not None
                        and (
                            session_snapshot is None
                            or command.base_plan_version_id
                            != session_snapshot.active_plan_version_id
                        )
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail="修改请求基于的方案版本已不是当前 active 版本",
                        )
                    if (
                        command.base_plan_id is not None
                        and (
                            session_snapshot is None
                            or command.base_plan_id != session_snapshot.selected_plan_id
                        )
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail="修改请求基于的方案已不是当前 selected 方案",
                        )
                active_plan_payloads = repository.list_plans(x_user_id, session_id)
                selected_plan = next(
                    (
                        Plan.model_validate(plan)
                        for plan in active_plan_payloads
                        if session_snapshot is not None
                        and plan.get("plan_id") == session_snapshot.selected_plan_id
                    ),
                    None,
                )
                active_constraints_payload = repository.active_constraints(
                    x_user_id,
                    session_id,
                )
                result = graph.invoke(
                    {
                        "user_input": request.content,
                        "actor": actor,
                        "has_plans": bool(active_plan_payloads),
                        "active_plan_version_id": (
                            session_snapshot.active_plan_version_id
                            if session_snapshot is not None
                            else None
                        ),
                        "active_request": (
                            PlanRequest.model_validate(
                                active_constraints_payload
                            )
                            if active_constraints_payload is not None
                            else None
                        ),
                        "selected_plan": selected_plan,
                        # A structured UI command is already validated at the
                        # HTTP boundary.  Passing it as an explicit Graph input
                        # keeps the natural-language interpreter out of this
                        # deterministic action path.
                        "conversation_command_override": request.conversation_command,
                    },
                    config=config,
                )

            # invoke() 可能再次停在 interrupt。这里读取持久化后的最新状态，而不是
            # 根据 result 猜测，然后转换成前端只需理解的 needs_input 响应。
            current = graph.get_state(config)
            runtime_decisions = _dump_runtime_decisions(result, current)
            if current.next and current.interrupts:
                question = current.interrupts[0].value
                current_request_value = (getattr(current, "values", None) or {}).get("active_request")
                current_request = (
                    current_request_value
                    if isinstance(current_request_value, PlanRequest)
                    else PlanRequest.model_validate(current_request_value)
                    if current_request_value is not None
                    else active_request
                )
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
                    runtime_decisions=runtime_decisions,
                    planning_context=project_planning_context(
                        current_request,
                        pending_field=question.get("field") if isinstance(question, dict) else None,
                    ),
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
            effective_constraints = (
                result.get("active_request")
            )
            if effective_constraints is None:
                current_request_value = (getattr(current, "values", None) or {}).get("active_request")
                effective_constraints = (
                    current_request_value
                    if isinstance(current_request_value, PlanRequest)
                    else PlanRequest.model_validate(current_request_value)
                    if current_request_value is not None
                    else active_request
                )
            if plans:
                reply = present_candidate_set(
                    candidate_set,
                    effective_constraints,
                )
            elif conflict is not None:
                reply = conflict.message

            plan_version_id = uuid.uuid4().hex if plans else None
            raw_plan_diffs = result.get("plan_diffs") or ()
            # Checkpoints created before the candidate-level contract may only
            # expose one ``plan_diff``.  Read it for compatibility, but every
            # newly produced response is serialized through ``plan_diffs``.
            if not raw_plan_diffs and result.get("plan_diff") is not None:
                raw_plan_diffs = (result["plan_diff"],)
            plan_diffs = list(raw_plan_diffs)
            interpretation_command = (
                interpretation.conversation_command
                if interpretation is not None
                else None
            )
            supersedes_version_id = (
                result.get("active_plan_version_id")
                if plans and (plan_diffs or result.get("mutation_kind") == "constraint_patch")
                else None
            )
            if plan_diffs and plan_version_id is not None:
                plan_diffs = [
                    diff.model_copy(
                        update={
                            "from_plan_version_id": supersedes_version_id,
                            "to_plan_version_id": plan_version_id,
                        }
                    )
                    for diff in plan_diffs
                ]
                reply = _modification_reply(plan_diffs)
            enrichment = result.get("enrichment")
            normalized_constraints_json = (
                effective_constraints.model_dump_json()
                if plans and effective_constraints is not None
                else None
            )

            recommendation_advice: RecommendationAdvice | None = None
            if plans and effective_constraints is not None:
                weather_fact = next(
                    (
                        fact
                        for fact in (candidate_set.provider_facts if candidate_set else [])
                        if isinstance(fact, WeatherFact)
                    ),
                    None,
                )
                recommendation_advice = advisor.advise(
                    RecommendationAdviceRequest(
                        constraints=effective_constraints,
                        semantic_request=(
                            candidate_set.semantic_request
                            if candidate_set is not None
                            else SemanticRequest()
                        ),
                        verified_plans=tuple(plans),
                        retrieval_evidence=tuple(
                            candidate_set.retrieval_evidence
                            if candidate_set is not None
                            else ()
                        ),
                        plan_diffs=tuple(plan_diffs),
                        weather_fact=weather_fact,
                    )
                )
                runtime_decisions = [
                    *runtime_decisions,
                    _recommendation_runtime_decision(recommendation_advice),
                ]

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
                retrieval_evidence=(
                    [
                        item.model_dump(mode="json")
                        for item in candidate_set.retrieval_evidence
                    ]
                    if candidate_set
                    else []
                ),
                runtime_decisions=runtime_decisions,
                retrieval_mode=(candidate_set.retrieval_mode if candidate_set else None),
                retrieval_index_version=(
                    candidate_set.retrieval_index_version if candidate_set else None
                ),
                search_mode=(candidate_set.search_mode if candidate_set else None),
                search_beam_width=(
                    candidate_set.search_beam_width if candidate_set else None
                ),
                search_max_expansions=(
                    candidate_set.search_max_expansions if candidate_set else None
                ),
                search_theoretical_combinations=(
                    candidate_set.search_theoretical_combinations
                    if candidate_set
                    else None
                ),
                search_expansions=(
                    candidate_set.search_expansions if candidate_set else None
                ),
                search_finalist_count=(
                    candidate_set.search_finalist_count if candidate_set else None
                ),
                search_pruned_by=(
                    candidate_set.search_pruned_by if candidate_set else {}
                ),
                search_traces=(
                    [
                        item.model_dump(mode="json")
                        for item in candidate_set.search_traces
                    ]
                    if candidate_set
                    else []
                ),
                primary_search_mode=(
                    candidate_set.primary_search_mode if candidate_set else None
                ),
                legacy_fallback_used=(
                    candidate_set.legacy_fallback_used if candidate_set else False
                ),
                legacy_fallback_reason=(
                    candidate_set.legacy_fallback_reason if candidate_set else None
                ),
                beam_expansions=(
                    candidate_set.beam_expansions if candidate_set else None
                ),
                beam_finalist_count=(
                    candidate_set.beam_finalist_count if candidate_set else None
                ),
                legacy_expansions=(
                    candidate_set.legacy_expansions if candidate_set else None
                ),
                accepted_plan_spec_ids=(
                    candidate_set.accepted_plan_spec_ids if candidate_set else []
                ),
                planning_intent_proposal_schema_version=(
                    candidate_set.planning_intent_proposal_schema_version
                    if candidate_set
                    else None
                ),
                planning_intent_proposal_slots=(
                    candidate_set.planning_intent_proposal_slots
                    if candidate_set
                    else []
                ),
                planning_intent_proposal_rejected=(
                    candidate_set.planning_intent_proposal_rejected
                    if candidate_set
                    else False
                ),
                planning_intent_proposal_compiled=(
                    candidate_set.planning_intent_proposal_compiled
                    if candidate_set
                    else False
                ),
                planning_intent_preferred_spec_ids=(
                    candidate_set.planning_intent_preferred_spec_ids
                    if candidate_set
                    else []
                ),
                planning_intent_fallback_spec_ids=(
                    candidate_set.planning_intent_fallback_spec_ids
                    if candidate_set
                    else []
                ),
                planning_intent_structure_fallback_used=(
                    candidate_set.planning_intent_structure_fallback_used
                    if candidate_set
                    else False
                ),
                planning_intent_structure_fallback_attempted=(
                    candidate_set.planning_intent_structure_fallback_attempted
                    if candidate_set
                    else False
                ),
                planning_intent_structure_fallback_reason=(
                    candidate_set.planning_intent_structure_fallback_reason
                    if candidate_set
                    else None
                ),
                planning_intent_structure_fallback_stage=(
                    candidate_set.planning_intent_structure_fallback_stage
                    if candidate_set
                    else None
                ),
                planning_intent_preferred_failure_fields=(
                    candidate_set.planning_intent_preferred_failure_fields
                    if candidate_set
                    else []
                ),
                planning_intent_fallback_failure_fields=(
                    candidate_set.planning_intent_fallback_failure_fields
                    if candidate_set
                    else []
                ),
                conversation_command=(
                    interpretation.conversation_command.model_dump(mode="json")
                    if interpretation
                    and interpretation.conversation_command is not None
                    else None
                ),
                # ``plan_diff`` is intentionally left empty for new responses;
                # old persisted responses remain readable by AgentResponse.  A
                # legacy natural-language command that still uses the original
                # ConstraintPatch gets the old convenience field as well; the
                # new structured criterion path never aliases a candidate diff.
                plan_diff=(
                    plan_diffs[0].model_dump(mode="json")
                    if (
                        plan_diffs
                        and interpretation_command is not None
                        and interpretation_command.constraint_patch.prefer_shorter_travel
                        and not interpretation_command.replacement_criteria
                    )
                    else None
                ),
                plan_diffs=[diff.model_dump(mode="json") for diff in plan_diffs],
                recommendation_advice=(
                    recommendation_advice.model_dump(mode="json")
                    if recommendation_advice is not None
                    else None
                ),
                planning_context=project_planning_context(effective_constraints),
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
                supersedes_version_id=supersedes_version_id,
            )
            return ResponseEnvelope(data=response)
        except Exception:
            repository.fail_planning_run(
                user_id=x_user_id,
                session_id=session_id,
                planning_run_id=run.planning_run_id,
            )
            raise

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
            ),
            x_user_id,
        )

    return app


def _dump_assumptions(result: dict) -> list[dict]:
    enrichment = result.get("enrichment")
    if enrichment is None:
        return []
    return [item.model_dump(mode="json") for item in enrichment.assumptions]


def _dump_runtime_decisions(result: dict, snapshot: object | None = None) -> list[dict]:
    """Serialize only the safe runtime trace carried by the current checkpoint."""

    values = getattr(snapshot, "values", None) or {}
    decisions = values.get("runtime_decisions") or result.get("runtime_decisions") or ()
    return [
        item.model_dump(mode="json")
        if hasattr(item, "model_dump")
        else item
        for item in decisions
    ]


def _recommendation_runtime_decision(
    advice: RecommendationAdvice,
) -> RuntimeDecision:
    """Expose the advisor's bounded runtime metadata in the common trace."""

    diagnostic_code = None
    if advice.fallback_reason:
        prefix = "invalid_proposal_contract:"
        if advice.fallback_reason.startswith(prefix):
            diagnostic_code = advice.fallback_reason[len(prefix) :]
        elif advice.fallback_reason == "invalid_proposal_parse":
            diagnostic_code = "invalid_proposal_parse"
        elif advice.fallback_reason.startswith("invalid_proposal_parse:"):
            diagnostic_code = advice.fallback_reason.split(":", 1)[1]

    return RuntimeDecision(
        stage="recommendation_advisor",
        adapter=advice.adapter,
        model_invoked=advice.model_invoked,
        model_name=advice.model_name,
        attempts=advice.attempts,
        fallback_reason=advice.fallback_reason,
        diagnostic_code=diagnostic_code,
        latency_ms=advice.latency_ms,
        input_tokens=advice.input_tokens,
        output_tokens=advice.output_tokens,
    )


def _modification_reply(plan_diffs: list) -> str:
    """Describe candidate-level changes without assuming a restaurant target."""

    count = len(plan_diffs)
    locked_counts = {
        len(diff.locked_stops)
        for diff in plan_diffs
    }
    locked_text = (
        f"其他 {next(iter(locked_counts))} 站保持不变"
        if len(locked_counts) == 1
        else "其他站点保持各自原位置"
    )
    if count == 1:
        replacement = plan_diffs[0].replacements[0]
        return (
            f"找到 1 个替换方案：将第 {replacement.stop_index + 1} 站“{replacement.before_name}”"
            f"替换为“{replacement.after_name}”；{locked_text}。"
        )
    return f"找到 {count} 个单站替换方案；{locked_text}，每个候选都已重新核验。"


def _dump_constraint_summary(result: dict) -> list[ConstraintSummaryItem]:
    """把规划实际使用的约束转换成稳定的前端摘要。"""
    constraints = (
        result.get("active_request")
    )
    if constraints is None:
        return []
    summary = []
    planning_window = getattr(constraints, "planning_window", None)
    if planning_window is not None:
        for field, constraint, value in (
            ("date", planning_window.date, planning_window.date.value.isoformat() if planning_window.date else None),
            ("time_window", planning_window.start_at, (
                {
                    "start": planning_window.start_at.value,
                    "end": planning_window.end_at.value,
                }
                if planning_window.start_at and planning_window.end_at
                else None
            )),
            ("departure_at", planning_window.explicit_departure, (
                planning_window.explicit_departure.value
                if planning_window.explicit_departure
                else None
            )),
            ("return_by", planning_window.explicit_return_deadline, (
                planning_window.explicit_return_deadline.value
                if planning_window.explicit_return_deadline
                else None
            )),
        ):
            if constraint is not None and value is not None:
                summary.append(
                    ConstraintSummaryItem(
                        field=field,
                        value=value,
                        source=constraint.source,
                        evidence=constraint.raw_text,
                        confidence=constraint.confidence,
                        rule_id=constraint.rule_id,
                    )
                )
    for field in (
        "exact_stop_count",
        "required_stop_roles",
        "duration_minutes",
        "location",
        "party",
        "budget_per_person",
        "max_distance_km",
        "total_distance_km",
    ):
        constraint = getattr(constraints, field)
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

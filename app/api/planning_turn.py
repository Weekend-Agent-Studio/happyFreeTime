"""Application service for one stateful planning turn.

The service owns the planning use case and returns domain/API response models
without depending on FastAPI request handling. HTTP adapters translate the
small application error contract into status codes.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from langgraph.types import Command

from app.api.planning_context import PlanningContextApplication
from app.api.schemas import AgentResponse, ConstraintSummaryItem, MessageRequest, PlanningContextSummary
from app.domain.constraints import (
    ActorContext,
    ClarificationAction,
    ClarificationIssue,
    CommandOperation,
    IdentityType,
    PlanRequest,
    RequestPatch,
)
from app.domain.planning import Plan
from app.domain.providers import WeatherFact
from app.domain.recommendation import RecommendationAdvice, RecommendationAdviceRequest
from app.domain.runtime import RuntimeDecision
from app.domain.run_trace import (
    InMemoryRunObserver,
    PlanningRunEventDraft,
    PlanningRunEvent,
    RunEventStatus,
    RunStage,
)
from app.domain.semantics import SemanticRequest
from app.orchestration.entry_graph import EnvironmentProvider, checkpoint_config
from app.persistence.repositories import SessionRepository
from app.services.presenter import present_candidate_set
from app.services.recommendation_advisor import RecommendationAdvisor
from app.services.poi_presentation import PoiPresentationProvider
from app.services.request_readiness import RequestReadinessPolicy


class PlanningApplicationError(Exception):
    """Expected application failure mapped to an HTTP response by the adapter."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PlanningTurnApplication:
    """Execute one planning turn independent of FastAPI or a browser client."""

    def __init__(
        self,
        *,
        repository: SessionRepository,
        graph: Any,
        environment_provider: EnvironmentProvider,
        planning_context_application: PlanningContextApplication,
        recommendation_advisor: RecommendationAdvisor,
        poi_presentation_provider: PoiPresentationProvider,
    ) -> None:
        self._repository = repository
        self._graph = graph
        self._environment_provider = environment_provider
        self._planning_context_application = planning_context_application
        self._advisor = recommendation_advisor
        self._presentation_provider = poi_presentation_provider

    def _actor_for(self, user_id: str, session_id: str) -> ActorContext:
        return ActorContext(
            user_id=user_id,
            session_id=session_id,
            identity_type=IdentityType.DEMO,
        )

    def project_context(
        self,
        user_id: str,
        session_id: str,
        request: PlanRequest,
        *,
        pending_field: str | None = None,
        just_planned: bool = False,
    ) -> PlanningContextSummary:
        snapshot = self._repository.get_session_snapshot(user_id, session_id)
        has_active_plan = bool(snapshot and snapshot.active_plan_version_id)
        planned_request = (
            self._repository.planned_constraints(user_id, session_id)
            if has_active_plan
            else None
        )
        planned_revision = (
            request.revision
            if just_planned
            else planned_request.get("revision") if planned_request else None
        )
        return self._planning_context_application.project(
            request,
            pending_interaction=pending_field,
            planned_request_revision=planned_revision,
            has_active_plan=has_active_plan or just_planned,
        )

    def handle(
        self,
        *,
        session_id: str,
        request: MessageRequest,
        user_id: str,
        event_sink: Callable[[PlanningRunEvent], None] | None = None,
    ) -> AgentResponse:
        if self._repository.get_session(user_id, session_id) is None:
            raise PlanningApplicationError(status_code=404, detail="session not found")

        config = checkpoint_config(session_id)
        pending_snapshot = self._graph.get_state(config)
        pending_interrupt = (
            pending_snapshot.interrupts[0].value
            if pending_snapshot.next and pending_snapshot.interrupts
            else None
        )
        if pending_interrupt is not None and (
            not isinstance(pending_interrupt, dict)
            or not pending_interrupt.get("clarification_id")
            or pending_interrupt.get("request_revision") is None
        ):
            raise PlanningApplicationError(
                status_code=409,
                detail="当前会话的旧反问状态已失效，请新建规划会话后继续。",
            )

        pending_values = getattr(pending_snapshot, "values", None) or {}
        active_value = pending_values.get("active_request")
        if active_value is None:
            active_value = self._repository.active_constraints(user_id, session_id)
        active_request = (
            active_value if isinstance(active_value, PlanRequest)
            else PlanRequest.model_validate(active_value) if active_value is not None
            else PlanRequest()
        )
        if request.replan_current_request:
            if pending_interrupt is not None:
                raise PlanningApplicationError(
                    status_code=409,
                    detail="当前有待回答的问题，请先完成或取消反问再重新规划。",
                )
            if (
                RequestReadinessPolicy().first_issue(active_request) is not None
                or (active_request.strict_budget and active_request.budget_per_person is None)
            ):
                raise PlanningApplicationError(
                    status_code=409,
                    detail="规划条件还不完整，请先补齐必需条件。",
                )
        compiled_context_patch: RequestPatch | None = None
        context_patch_issues: tuple[ClarificationIssue, ...] = ()
        if request.planning_context_patch is not None:
            if request.planning_context_patch.base_revision != active_request.revision:
                raise PlanningApplicationError(
                    status_code=409,
                    detail="规划条件已更新，请刷新后再修改顶部条件。",
                )
            pending_revision = (
                pending_interrupt.get("request_revision")
                if isinstance(pending_interrupt, dict)
                else None
            )
            if pending_revision is not None and pending_revision != active_request.revision:
                raise PlanningApplicationError(
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
                raise PlanningApplicationError(
                    status_code=409,
                    detail="当前会话没有待处理的反问",
                )
            pending_id = (
                pending_interrupt.get("clarification_id")
                if isinstance(pending_interrupt, dict)
                else None
            )
            if pending_id and request.clarification_reply.clarification_id != pending_id:
                raise PlanningApplicationError(
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
                and request.clarification_reply.request_revision != pending_revision
            ):
                raise PlanningApplicationError(
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
            session_snapshot = self._repository.get_session_snapshot(
                user_id,
                session_id,
            )
            if command.base_plan_version_id is None or (
                command.operation == CommandOperation.REPLACE and command.base_plan_id is None
            ):
                raise PlanningApplicationError(
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
                raise PlanningApplicationError(
                    status_code=409,
                    detail="修改请求基于的方案版本已不是当前 active 版本",
                )
            if (
                command.operation == CommandOperation.REPLACE
                and command.base_plan_id != session_snapshot.selected_plan_id
            ):
                raise PlanningApplicationError(
                    status_code=409,
                    detail="修改请求基于的方案已不是当前 selected 方案",
                )

        try:
            run = self._repository.begin_planning_run(
                user_id=user_id,
                session_id=session_id,
                request_id=request.request_id,
                content=request_content,
                record_user_message=(
                    request.planning_context_patch is None
                    and not request.replan_current_request
                ),
            )
        except ValueError as error:
            raise PlanningApplicationError(status_code=409, detail=str(error)) from error
        if run.stored_response is not None:
            return AgentResponse.model_validate(run.stored_response)
        if not run.should_execute:
            raise PlanningApplicationError(status_code=409, detail="planning run is already in progress")

        run_observer = InMemoryRunObserver(run.planning_run_id, on_event=event_sink)
        invoke_config = {
            **config,
            "configurable": {
                **config.get("configurable", {}),
                "run_observer": run_observer,
            },
        }

        try:
            actor = self._actor_for(user_id, session_id)
            if request.planning_context_patch is not None:
                context_edit = self._planning_context_application.compile_edit(
                    request.planning_context_patch,
                    request=active_request,
                    environment=self._environment_provider(actor),
                    pending_field=(
                        pending_interrupt.get("field")
                        if isinstance(pending_interrupt, dict)
                        else None
                    ),
                )
                compiled_context_patch = context_edit.patch
                context_patch_issues = context_edit.issues
            # checkpoint 中存在未完成 interrupt，说明本条消息是上一问题的答案；
            # 否则将它作为新一轮用户目标调用 Graph。前端无需理解 Graph 状态机。
            snapshot = self._graph.get_state(config)
            if snapshot.next and snapshot.interrupts:
                if compiled_context_patch is not None:
                    resume_value = {
                        "kind": "planning_context_patch",
                        "clarification_id": pending_interrupt["clarification_id"],
                        "request_revision": request.planning_context_patch.base_revision,
                        "request_patch": compiled_context_patch.model_dump(mode="json"),
                        "request_patch_issues": [
                            issue.model_dump(mode="json") for issue in context_patch_issues
                        ],
                    }
                elif request.clarification_reply is not None:
                    resume_value: object = request.clarification_reply.model_dump(mode="json")
                else:
                    # Free-text answers are bound to the current field issue;
                    # they do not start another Router turn.
                    resume_value = {
                        "clarification_id": pending_interrupt["clarification_id"],
                        "action": ClarificationAction.ANSWER.value,
                        "value": request.content,
                        "request_revision": pending_interrupt["request_revision"],
                    }
                result = self._graph.invoke(Command(resume=resume_value), config=invoke_config)
            elif compiled_context_patch is not None:
                session_snapshot = self._repository.get_session_snapshot(user_id, session_id)
                active_plan_payloads = self._repository.list_plans(user_id, session_id)
                selected_plan = next(
                    (
                        Plan.model_validate(plan)
                        for plan in active_plan_payloads
                        if session_snapshot is not None
                        and plan.get("plan_id") == session_snapshot.selected_plan_id
                    ),
                    None,
                )
                result = self._graph.invoke(
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
                        "defer_planning": request.defer_planning,
                        "replan_current_request": False,
                    },
                    config=invoke_config,
                )
            else:
                session_snapshot = self._repository.get_session_snapshot(
                    user_id,
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
                        raise PlanningApplicationError(
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
                        raise PlanningApplicationError(
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
                        raise PlanningApplicationError(
                            status_code=409,
                            detail="修改请求基于的方案已不是当前 selected 方案",
                        )
                active_plan_payloads = self._repository.list_plans(user_id, session_id)
                selected_plan = next(
                    (
                        Plan.model_validate(plan)
                        for plan in active_plan_payloads
                        if session_snapshot is not None
                        and plan.get("plan_id") == session_snapshot.selected_plan_id
                    ),
                    None,
                )
                result = self._graph.invoke(
                    {
                        "user_input": request.content,
                        "actor": actor,
                        "has_plans": bool(active_plan_payloads),
                        "active_plan_version_id": (
                            session_snapshot.active_plan_version_id
                            if session_snapshot is not None
                            else None
                        ),
                        "active_request": active_request,
                        "selected_plan": selected_plan,
                        "defer_planning": False,
                        "replan_current_request": request.replan_current_request,
                        # A structured UI command is already validated at the
                        # HTTP boundary.  Passing it as an explicit Graph input
                        # keeps the natural-language interpreter out of this
                        # deterministic action path.
                        "conversation_command_override": request.conversation_command,
                    },
                    config=invoke_config,
                )

            # invoke() 可能再次停在 interrupt。这里读取持久化后的最新状态，而不是
            # 根据 result 猜测，然后转换成前端只需理解的 needs_input 响应。
            current = self._graph.get_state(config)
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
                    run_trace=run_observer.snapshot(),
                    planning_context=self.project_context(
                        user_id,
                        session_id,
                        current_request,
                        pending_field=question.get("field") if isinstance(question, dict) else None,
                    ),
                )
                self._repository.complete_planning_run(
                    user_id=user_id,
                    session_id=session_id,
                    planning_run_id=run.planning_run_id,
                    status="needs_input",
                    response=response.model_dump(mode="json"),
                    assistant_content=question["question"],
                    plans=[],
                    active_request_json=current_request.model_dump_json(),
                )
                return response

            interpretation = result.get("interpretation")
            candidate_set = result.get("candidate_set")
            reply = interpretation.reply if interpretation else ""
            plans = candidate_set.plans if candidate_set else []
            conflict = candidate_set.conflict if candidate_set else None
            context_saved = (
                request.planning_context_patch is not None
                and request.defer_planning
                and not plans
                and conflict is None
            )
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
            plan_diffs = list(raw_plan_diffs)
            interpretation_command = (
                interpretation.conversation_command
                if interpretation is not None
                else None
            )
            supersedes_version_id = (
                result.get("active_plan_version_id")
                if plans and (
                    plan_diffs or result.get("workflow_outcome") == "constraint_patch"
                )
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
            normalized_constraints_json = (
                effective_constraints.model_dump_json()
                if plans and effective_constraints is not None
                else None
            )

            recommendation_advice: RecommendationAdvice | None = None
            if plans and effective_constraints is not None:
                run_observer.record(
                    PlanningRunEventDraft(
                        stage=RunStage.ADVISE,
                        status=RunEventStatus.STARTED,
                        message_key="advise.started",
                        public_message="正在整理推荐说明",
                    )
                )
                weather_fact = next(
                    (
                        fact
                        for fact in (candidate_set.provider_facts if candidate_set else [])
                        if isinstance(fact, WeatherFact)
                    ),
                    None,
                )
                recommendation_advice = self._advisor.advise(
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
                run_observer.record(
                    PlanningRunEventDraft(
                        stage=RunStage.ADVISE,
                        status=(
                            RunEventStatus.COMPLETED
                            if recommendation_advice.adapter != "fallback"
                            else RunEventStatus.FALLBACK
                        ),
                        message_key=(
                            "advise.completed"
                            if recommendation_advice.adapter != "fallback"
                            else "advise.fallback"
                        ),
                        public_message=(
                            "推荐说明生成完成"
                            if recommendation_advice.adapter != "fallback"
                            else "推荐说明已回退到安全模板"
                        ),
                        public_details=(
                            {}
                            if recommendation_advice.adapter != "fallback"
                            else {"reason_code": "advisor_fallback"}
                        ),
                    )
                )

            response = AgentResponse(
                status="context_saved" if context_saved else "completed",
                reply="" if context_saved else reply,
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
                    for item in self._presentation_provider.present_many(
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
                # Keep the singular convenience view for a route-objective
                # replacement; candidate-aligned details remain in plan_diffs.
                plan_diff=(
                    plan_diffs[0].model_dump(mode="json")
                    if (
                        plan_diffs
                        and interpretation_command is not None
                        and any(
                            criterion.kind == "route_objective"
                            for criterion in interpretation_command.replacement_criteria
                        )
                    )
                    else None
                ),
                plan_diffs=[diff.model_dump(mode="json") for diff in plan_diffs],
                recommendation_advice=(
                    recommendation_advice.model_dump(mode="json")
                    if recommendation_advice is not None
                    else None
                ),
                run_trace=run_observer.snapshot(),
                planning_context=self.project_context(
                    user_id,
                    session_id,
                    effective_constraints,
                    just_planned=bool(plans),
                ),
            )
            self._repository.complete_planning_run(
                user_id=user_id,
                session_id=session_id,
                planning_run_id=run.planning_run_id,
                status="completed",
                response=response.model_dump(mode="json"),
                assistant_content="" if context_saved else reply,
                plans=plans,
                plan_version_id=plan_version_id,
                normalized_constraints_json=normalized_constraints_json,
                active_request_json=(
                    effective_constraints.model_dump_json()
                    if effective_constraints is not None
                    else None
                ),
                supersedes_version_id=supersedes_version_id,
            )
            return response
        except Exception:
            # Keep the public execution trace lifecycle closed even when an
            # unexpected provider or application error aborts the turn. The
            # observer only emits a bounded, generic failure event; raw error
            # text and stack traces never enter the public response.
            run_observer.fail_open_stages()
            self._repository.fail_planning_run(
                user_id=user_id,
                session_id=session_id,
                planning_run_id=run.planning_run_id,
            )
            raise



def _dump_assumptions(result: dict) -> list[dict]:
    assumptions = result.get("assumptions") or ()
    return [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        for item in assumptions
    ]


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
        "trip_time_scope",
        "exact_stop_count",
        "required_stop_roles",
        "activity_time_scope",
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

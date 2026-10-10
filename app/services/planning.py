"""Deterministic bounded planning over normalized Catalog candidates."""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from enum import Enum
from itertools import product
from time import perf_counter
from typing import Sequence
from zoneinfo import ZoneInfo

from app.domain.catalog import (
    CatalogResult,
    PriceKind,
    ResourceType,
    StopCandidate,
)
from app.domain.constraints import (
    CommandOperation,
    ConversationCommand,
    PlanRequest,
    QuestionDecision,
    RouteObjective,
    SemanticCriterion,
    StopRole,
    TargetReference,
    TimeScope,
    TimeWindow,
)
from app.domain.planning import (
    CandidateSet,
    ConstraintConflict,
    Plan,
    PlanPace,
    PlanPriceStatus,
    PlanModificationResult,
    PlanDiff,
    PlanWarning,
    PlanStrategy,
    PlanningIntent,
    SkeletonSearchTrace,
    RouteLeg,
    RouteMode,
    RouteSource,
    ScoreContribution,
    Stop,
    StopReplacement,
    StopType,
    LockedStop,
)
from app.domain.providers import (
    AvailabilityCheck,
    AvailabilityFact,
    AvailabilityRequest,
    GeoPoint,
    ProviderSource,
    RouteFact,
    RouteRequest,
    WeatherFact,
    WeatherRequest,
)
from app.domain.runtime import RuntimeDecision
from app.domain.recovery import RecoveryStage
from app.domain.run_trace import PlanningRunEventDraft, RunEventStatus, RunObserver, RunStage
from app.domain.semantics import TimeCoverageObjective, compile_replacement_semantics
from app.providers.route import LocalEstimateRouteProvider, RouteProvider
from app.providers.availability import AvailabilityProvider, MockAvailabilityProvider
from app.providers.weather import WeatherProvider, clear_mock_weather
from app.services.catalog import Catalog, SnapshotCatalog
from app.services.planning_intent import (
    PlanningIntentProvider,
    RuleBasedPlanningIntentProvider,
    build_rule_based_planning_intent,
)
from app.services.plan_verifier import (
    PlanVerifier,
    VerificationFinding,
)
from app.services.itinerary_scheduler import (
    DEFAULT_TEMPORAL_POLICY,
    TimelineScheduler,
)
from app.services.plan_spec import PlanSpec
from app.services.plan_spec_compiler import (
    PlanSpecChoices,
    PlanSpecCompiler,
)
from app.services.recovery_reason_adapter import RecoveryReasonAdapter
from app.services.candidate_retriever import (
    CandidateRetriever,
    RetrievalRequest,
    RetrievedCandidateSet,
    build_default_candidate_retriever,
)
from app.services.itinerary_search import (
    BeamSearchConfig,
    BeamSearchStats,
    bounded_beam_search,
)


_PREFERENCE_ALIASES: dict[str, frozenset[str]] = {
    "甜品": frozenset({"甜品", "甜点", "cake", "donut", "ice_cream", "bubble_tea"}),
    "安静": frozenset({"安静", "博物馆", "美术馆", "图书馆", "书店", "tea", "cafe", "coffee_shop"}),
    "亲子": frozenset({"亲子", "主题乐园", "动物园", "博物馆", "公园"}),
    "户外": frozenset({"户外", "公园", "运动"}),
}
_MAX_RETURNED_PLANS = 3
_MAX_FEASIBLE_PLANS = 12
_MAX_ROUTE_LEG_VERIFICATIONS = 24
_MAX_AVAILABILITY_BATCHES = 6
_RULE_FALLBACK_MAX_CANDIDATES = 2
_MAX_LOCAL_REPLAN_ROUNDS = 2
_MAX_CANDIDATES_PER_ROLE_FOR_MULTI_STOP = 8
_MIN_PLAN_DIVERSITY = 0.35
_ROLE_RESOURCE_TYPES: dict[StopRole, frozenset[ResourceType]] = {
    StopRole.ACTIVITY: frozenset({ResourceType.ACTIVITY}),
    StopRole.MEAL: frozenset(
        {ResourceType.RESTAURANT, ResourceType.CAFE, ResourceType.DESSERT}
    ),
    StopRole.LUNCH: frozenset({ResourceType.RESTAURANT}),
    StopRole.DINNER: frozenset({ResourceType.RESTAURANT}),
    StopRole.BREAK: frozenset({ResourceType.CAFE, ResourceType.DESSERT}),
}

_TIME_COVERAGE_WINDOWS: dict[TimeScope, tuple[int, int]] = {
    TimeScope.MORNING: (9 * 60, 12 * 60),
    TimeScope.AFTERNOON: (14 * 60, 18 * 60),
    TimeScope.EVENING: (18 * 60, 22 * 60),
}
_TIME_COVERAGE_ROLES: dict[TimeScope, frozenset[StopRole]] = {
    TimeScope.MORNING: frozenset(
        {StopRole.ACTIVITY, StopRole.BREAK, StopRole.LUNCH, StopRole.MEAL}
    ),
    TimeScope.AFTERNOON: frozenset({StopRole.ACTIVITY, StopRole.BREAK}),
    TimeScope.EVENING: frozenset(
        {StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER, StopRole.MEAL}
    ),
}
_MIN_COVERAGE_OVERLAP_MINUTES = 30


def _retrieval_runtime(result: RetrievedCandidateSet) -> RuntimeDecision:
    """Convert retriever metadata into the safe runtime trace contract."""

    return RuntimeDecision(
        stage="candidate_retrieval",
        adapter=result.actual_adapter,
        # A multi-role request may combine dense retrieval for one role with an
        # intentional rule-only bypass for another role that has no applicable
        # semantic query. ``query_count`` is the evidence that an embedding
        # call actually happened; the aggregate adapter may be ``mixed``.
        model_invoked=result.query_count > 0,
        model_name=result.model_id,
        attempts=1 if result.query_count > 0 else 0,
        fallback_reason=result.fallback_reason,
        latency_ms=result.latency_ms,
        requested_mode=result.requested_mode,
        index_version=result.index_version,
        query_count=result.query_count,
        candidate_count=result.candidate_count,
    )


def _merge_retrieval_runtime(
    first: RuntimeDecision,
    second: RuntimeDecision,
) -> RuntimeDecision:
    """Aggregate both bounded retrieval passes into the existing trace slot."""

    if first.stage != second.stage:
        raise ValueError("only candidate_retrieval decisions can be aggregated")
    fallback_reasons = tuple(
        dict.fromkeys(
            reason
            for reason in (first.fallback_reason, second.fallback_reason)
            if reason
        )
    )
    return first.model_copy(
        update={
            "adapter": first.adapter if first.adapter == second.adapter else "mixed",
            "model_invoked": first.model_invoked or second.model_invoked,
            "model_name": (
                first.model_name
                if first.model_name == second.model_name
                else None
            ),
            "attempts": min(2, first.attempts + second.attempts),
            "fallback_reason": ";".join(fallback_reasons) or None,
            "latency_ms": (first.latency_ms or 0) + (second.latency_ms or 0),
            "requested_mode": (
                first.requested_mode
                if first.requested_mode == second.requested_mode
                else "mixed"
            ),
            "index_version": (
                first.index_version
                if first.index_version == second.index_version
                else f"mixed:{first.index_version}+{second.index_version}"
            ),
            "query_count": (first.query_count or 0) + (second.query_count or 0),
            # This is the cumulative number of retrieved candidates across both
            # passes; overlap is intentionally not deduplicated in the trace.
            "candidate_count": (first.candidate_count or 0)
            + (second.candidate_count or 0),
        }
    )


def _retrieval_evidence_for_plans(
    retrieved: RetrievedCandidateSet,
    plans: Sequence[Plan],
) -> list[EvidenceRef]:
    """Keep only profile citations attached to resources in returned plans."""

    selected_ids = {
        stop.resource_id
        for plan in plans
        for stop in plan.stops
    }
    evidence_by_id: dict[str, EvidenceRef] = {}
    for item in retrieved.items:
        if item.candidate.resource_id not in selected_ids:
            continue
        for evidence in item.matched_profile_evidence:
            evidence_by_id.setdefault(evidence.evidence_id, evidence)
    return list(evidence_by_id.values())


class RepairOutcome(str, Enum):
    """One bounded repair scheduling decision for a single root finalist chain."""

    LOCAL_REPLACEMENT = "local_replacement"
    NEXT_FINALIST = "next_finalist"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class _FinalistAttempt:
    plan: Plan
    root_fingerprint: str
    repair_rounds: int = 0


@dataclass(frozen=True)
class _RepairDecision:
    outcome: RepairOutcome
    replacement: _FinalistAttempt | None = None


@dataclass(frozen=True)
class _RepairContext:
    """Everything the bounded repair scheduler may inspect.

    The budgets are deliberately inputs rather than hidden globals: a repair is
    allowed to rearrange only the already bounded finalist pool, and must never
    turn a provider quota into an unbounded retry loop.
    """

    failed: _FinalistAttempt
    findings: tuple[VerificationFinding, ...]
    remaining_route_leg_budget: int
    remaining_availability_batches: int


class _RepairCoordinator:
    """Deep internal seam for failure attribution and bounded finalist scheduling.

    It never calls a Provider or verifies a plan. Given already selected finalist
    candidates and verifier findings, it either chooses one single-stop sibling,
    defers to independent finalists, or closes only the current repair chain.
    """

    def decide(
        self,
        *,
        context: _RepairContext,
        pending: deque[_FinalistAttempt],
        verified_compositions: set[str],
    ) -> _RepairDecision:
        failed = context.failed
        target_ids = _repair_target_resource_ids(failed.plan, context.findings)
        if not target_ids:
            return _RepairDecision(RepairOutcome.NEXT_FINALIST)
        needs_availability_recheck = any(
            finding.code == "availability_verified_unavailable"
            for finding in context.findings
        )
        if context.remaining_route_leg_budget <= 0 or (
            needs_availability_recheck
            and context.remaining_availability_batches <= 0
        ):
            return _RepairDecision(RepairOutcome.TERMINAL)
        if failed.repair_rounds >= _MAX_LOCAL_REPLAN_ROUNDS:
            return _RepairDecision(RepairOutcome.TERMINAL)
        replacement = _take_local_replacement(
            pending,
            failed,
            target_ids,
            verified_compositions,
        )
        if replacement is None:
            return _RepairDecision(RepairOutcome.TERMINAL)
        return _RepairDecision(RepairOutcome.LOCAL_REPLACEMENT, replacement)


class PlanningService:
    """Generate and verify bounded plans behind one stable interface."""

    def __init__(
        self,
        weather_provider: WeatherProvider | None = None,
        route_provider: RouteProvider | None = None,
        availability_provider: AvailabilityProvider | None = None,
        catalog: Catalog | None = None,
        planning_intent_provider: PlanningIntentProvider | None = None,
        candidate_retriever: CandidateRetriever | None = None,
    ) -> None:
        self._weather_provider = weather_provider or clear_mock_weather()
        self._route_provider = route_provider or LocalEstimateRouteProvider()
        self._availability_provider = availability_provider or MockAvailabilityProvider()
        self._catalog = catalog or SnapshotCatalog()
        self._plan_verifier = PlanVerifier()
        self._planning_intent_provider = (
            planning_intent_provider or RuleBasedPlanningIntentProvider()
        )
        self._candidate_retriever = candidate_retriever or build_default_candidate_retriever()
        self._plan_spec_compiler = PlanSpecCompiler()

    def _retrieve_for_plan_spec_roles(
        self,
        candidates: list[StopCandidate],
        planning_intent: PlanningIntent,
        plan_specs: Sequence[PlanSpec],
    ) -> tuple[RetrievedCandidateSet, dict[str, float]]:
        """Retrieve independently for each role, then restore Catalog identity order.

        A role-scoped request prevents an activity query from competing directly
        with a restaurant query.  The union is only a bounded candidate set;
        hard catalog filtering already happened before this method and the
        Planner still owns final feasibility.  Identical resource IDs returned
        by more than one compatible meal role are cached in this round.
        """

        specs = tuple(plan_specs)
        roles = tuple(dict.fromkeys(role for spec in specs for role in spec.roles))
        by_role: dict[StopRole, list[StopCandidate]] = {
            role: [
                candidate
                for candidate in candidates
                if candidate.resource_type in _ROLE_RESOURCE_TYPES[role]
            ]
            for role in roles
        }
        results: list[RetrievedCandidateSet] = []
        items_by_id: dict[str, object] = {}
        scores_by_id: dict[str, float] = {}
        for role in roles:
            request = RetrievalRequest(
                candidates=tuple(by_role[role]),
                semantic_request=planning_intent.semantic_request,
                target_role=role,
            )
            result = self._candidate_retriever.retrieve(request)
            results.append(result)
            for item in result.items:
                existing = items_by_id.get(item.candidate.resource_id)
                if existing is None or item.score.final_score > existing.score.final_score:
                    items_by_id[item.candidate.resource_id] = item
                    scores_by_id[item.candidate.resource_id] = item.score.final_score

        selected_ids = set(items_by_id)
        ordered_candidates = [
            candidate for candidate in candidates if candidate.resource_id in selected_ids
        ]
        if not results:
            return (
                RetrievedCandidateSet(
                    mode="rule",
                    index_version="rule-alias.v2",
                    requested_mode="rule",
                    actual_adapter="rule_based",
                    candidate_count=0,
                ),
                {},
            )
        modes = {result.mode for result in results}
        # Hybrid is effective when at least one role used the dense index. A
        # different role may intentionally bypass embeddings because its
        # role-scoped semantic query is empty; that is not a global fallback.
        actual_mode = "hybrid" if "hybrid" in modes else "rule"
        index_versions = tuple(dict.fromkeys(result.index_version for result in results))
        index_version = index_versions[0] if len(index_versions) == 1 else "mixed:" + "+".join(index_versions)
        requested_modes = tuple(dict.fromkeys(result.requested_mode for result in results))
        adapters = tuple(dict.fromkeys(result.actual_adapter for result in results))
        has_dense_result = any(result.query_count > 0 for result in results)
        fallback_reasons = tuple(
            dict.fromkeys(
                result.fallback_reason
                for result in results
                if result.fallback_reason
                and not (
                    result.fallback_reason == "no_dense_query"
                    and has_dense_result
                )
            )
        )
        model_ids = tuple(dict.fromkeys(result.model_id for result in results if result.model_id))
        return (
            RetrievedCandidateSet(
                items=tuple(
                    items_by_id[candidate.resource_id]
                    for candidate in ordered_candidates
                ),
                mode=actual_mode,
                index_version=index_version,
                requested_mode=(requested_modes[0] if len(requested_modes) == 1 else "mixed"),
                actual_adapter=(adapters[0] if len(adapters) == 1 else "mixed"),
                model_id=(model_ids[0] if len(model_ids) == 1 else None),
                query_count=sum(result.query_count for result in results),
                candidate_count=len(ordered_candidates),
                latency_ms=sum(result.latency_ms for result in results),
                fallback_reason=(";".join(fallback_reasons) if fallback_reasons else None),
                fusion_version=(
                    "hybrid-bge-rank-fusion.v1" if actual_mode == "hybrid" else None
                ),
            ),
            scores_by_id,
        )

    @staticmethod
    def _trace(
        observer: RunObserver | None,
        *,
        stage: RunStage,
        status: RunEventStatus,
        message_key: str,
        public_message: str,
        public_details: dict[str, str | int | float | bool | None] | None = None,
    ) -> None:
        if observer is None:
            return
        observer.record(
            PlanningRunEventDraft(
                stage=stage,
                status=status,
                message_key=message_key,
                public_message=public_message,
                public_details=public_details or {},
            )
        )

    def plan(
        self,
        constraints: PlanRequest,
        *,
        observer: RunObserver | None = None,
    ) -> CandidateSet:
        runtime_decision = self._not_run_runtime(
            "planning cannot start before normalized constraints are complete"
        )
        if (
            constraints.location is None
            or constraints.planning_window.clock_bounds is None
            or constraints.planning_window.date is None
        ):
            raise ValueError("planning requires normalized date, time window, and location")

        time_conflict = _planning_time_conflict(constraints)
        if time_conflict is not None:
            return CandidateSet(
                conflict=time_conflict,
                recovery_reason=RecoveryReasonAdapter().from_conflict(
                    time_conflict, constraints
                ),
                runtime_decision=runtime_decision,
            )

        self._trace(
            observer,
            stage=RunStage.STRUCTURE,
            status=RunEventStatus.STARTED,
            message_key="structure.started",
            public_message="正在组织候选行程结构",
        )

        rule_baseline = build_rule_based_planning_intent(constraints)
        explicit_choices = self._plan_spec_compiler.compile_explicit_structure(
            constraints,
            rule_baseline,
        )
        if explicit_choices is not None and explicit_choices.conflict is not None:
            self._trace(
                observer,
                stage=RunStage.STRUCTURE,
                status=RunEventStatus.FALLBACK,
                message_key="structure.fallback",
                public_message="行程结构无法满足当前条件，已安全停止规划",
                public_details={"reason_code": explicit_choices.conflict.code},
            )
            conflict_code = explicit_choices.conflict.code
            return CandidateSet(
                conflict=explicit_choices.conflict,
                recovery_reason=RecoveryReasonAdapter().from_conflict(
                    explicit_choices.conflict,
                    constraints,
                    stage=RecoveryStage.PLAN_STRUCTURE,
                ),
                runtime_decision=self._not_run_runtime(
                    (
                        "unsupported_plan_structure"
                        if conflict_code == "UNSUPPORTED_PLAN_STRUCTURE"
                        else conflict_code.lower()
                    )
                ),
            )

        # Reject deterministic hard conflicts before spending the bounded model
        # budget. PlanningIntent is a soft structural decision and cannot make
        # either conflict valid.
        started_at = perf_counter()
        planning_intent_decision = self._planning_intent_provider.decide(constraints)
        runtime_decision = RuntimeDecision(
            stage="planning_intent",
            adapter=planning_intent_decision.source,
            model_invoked=planning_intent_decision.source != "rule_based",
            model_name=planning_intent_decision.model_name,
            attempts=planning_intent_decision.attempts,
            fallback_reason=planning_intent_decision.fallback_reason,
            latency_ms=_elapsed_ms(started_at),
            input_tokens=planning_intent_decision.input_tokens,
            output_tokens=planning_intent_decision.output_tokens,
            wire_schema_version=(
                planning_intent_decision.structure_proposal.schema_version
                if planning_intent_decision.structure_proposal is not None
                else None
            ),
            prompt_version=planning_intent_decision.prompt_version,
        )
        planning_intent = planning_intent_decision.intent

        location = constraints.location.value
        weather = self._weather_provider.get_weather(
            WeatherRequest(
                city=location.city,
                district=location.district,
                # Explicit places arrive with their provider-derived adcode. The
                # city default is retained only for legacy/default start points
                # that predate the GeocodingProvider seam.
                adcode=location.adcode or "110105",
                date=constraints.planning_window.date.value,
            )
        )
        catalog_result = self._catalog.recall(constraints)
        candidate_by_id = {
            candidate.resource_id: candidate
            for candidate in catalog_result.candidates
        }
        weather_removed = [
            candidate
            for candidate in catalog_result.candidates
            if (
                weather.is_adverse
                and candidate.resource_type == ResourceType.ACTIVITY
                and candidate.weather_sensitive
            )
        ]
        weather_removed_ids = {item.resource_id for item in weather_removed}
        candidates = [
            candidate
            for candidate in catalog_result.candidates
            if candidate.resource_id not in weather_removed_ids
        ]
        catalog_candidates = candidates
        # Explicit constraints, Rule baseline, and model structure proposals
        # are compiled by the same PlanSpecCompiler. Explicit structure is
        # preflighted before the model call; its compiled result remains
        # authoritative while the model may still contribute soft semantics.
        plan_choices = explicit_choices or self._plan_spec_compiler.compile(
            constraints,
            rule_baseline,
            planning_intent_decision.structure_proposal,
        )
        preferred_specs = plan_choices.preferred_specs
        fallback_specs = plan_choices.fallback_specs
        plan_specs = preferred_specs or fallback_specs
        structure_fallback_used = False
        structure_fallback_reason = plan_choices.diagnostic_code
        structure_fallback_stage = (
            "compile"
            if plan_choices.proposal_status == "rejected" and fallback_specs
            else None
        )
        # Compiler acceptance is authoritative. A wire structure is not
        # considered compiled when explicit user roles took precedence.
        if planning_intent_decision.structure_proposal is not None:
            planning_intent_decision = planning_intent_decision.model_copy(
                update={
                    "proposal_accepted": plan_choices.proposal_status == "compiled",
                    "proposal_rejection_reason": plan_choices.diagnostic_code,
                }
        )
        if not plan_specs:
            if plan_choices.conflict is not None:
                self._trace(
                    observer,
                    stage=RunStage.STRUCTURE,
                    status=RunEventStatus.FALLBACK,
                    message_key="structure.fallback",
                    public_message="当前条件下没有可执行的行程结构",
                    public_details={"reason_code": plan_choices.conflict.code},
                )
                return CandidateSet(
                    conflict=plan_choices.conflict,
                    recovery_reason=RecoveryReasonAdapter().from_conflict(
                        plan_choices.conflict,
                        constraints,
                        stage=RecoveryStage.PLAN_STRUCTURE,
                    ),
                    planning_intent_decision=planning_intent_decision,
                    runtime_decision=runtime_decision,
                )
            deadline = constraints.planning_window.explicit_return_deadline
            explicit_window = constraints.planning_window.explicit_trip_range
            if deadline is not None:
                conflict = ConstraintConflict(
                    code="NO_FEASIBLE_PLAN",
                    message="最晚到家时间留给规划的可用时长不足，无法安排可执行行程。",
                    fields=["return_by"],
                )
            elif explicit_window:
                conflict = ConstraintConflict(
                    code="NO_FEASIBLE_PLAN",
                    message="指定时间范围内没有可执行的行程结构。",
                    fields=["time_window"],
                )
            else:
                conflict = ConstraintConflict(
                    code="NO_PLAN_SPEC",
                    message="当前请求没有可执行的行程结构。",
                    fields=["plan_structure"],
                )
            self._trace(
                observer,
                stage=RunStage.STRUCTURE,
                status=RunEventStatus.FALLBACK,
                message_key="structure.fallback",
                public_message="当前条件下没有可执行的行程结构",
                public_details={"reason_code": conflict.code},
            )
            return CandidateSet(
                conflict=conflict,
                recovery_reason=RecoveryReasonAdapter().from_conflict(
                    conflict,
                    constraints,
                    stage=(
                        RecoveryStage.SCHEDULING
                        if deadline is not None or explicit_window
                        else RecoveryStage.PLAN_STRUCTURE
                    ),
                ),
                planning_intent_decision=planning_intent_decision,
                runtime_decision=runtime_decision,
            )
        # Retrieve the active path's role pools only. If an accepted model
        # structure later fails, the Rule fallback performs a fresh bounded
        # retrieval using the deterministic Rule semantics.
        active_planning_intent = (
            planning_intent
            if preferred_specs or plan_choices.explicit_structure
            else rule_baseline
        )
        active_planning_intent_source = (
            planning_intent_decision.source
            if preferred_specs or plan_choices.explicit_structure
            else "fallback"
        )
        retrieval_specs = preferred_specs or fallback_specs
        self._trace(
            observer,
            stage=RunStage.STRUCTURE,
            status=RunEventStatus.COMPLETED,
            message_key="structure.completed",
            public_message="行程结构已确定",
            public_details={"count": len(plan_specs)},
        )
        self._trace(
            observer,
            stage=RunStage.RETRIEVE,
            status=RunEventStatus.STARTED,
            message_key="retrieve.started",
            public_message="正在检索符合条件的地点",
        )
        retrieved, semantic_scores = self._retrieve_for_plan_spec_roles(
            candidates,
            active_planning_intent,
            retrieval_specs,
        )
        retrieval_runtime_decision = _retrieval_runtime(retrieved)
        self._trace(
            observer,
            stage=RunStage.RETRIEVE,
            status=(RunEventStatus.FALLBACK if retrieved.fallback_reason else RunEventStatus.COMPLETED),
            message_key=("retrieve.fallback" if retrieved.fallback_reason else "retrieve.completed"),
            public_message=(
                "语义检索不可用，已回退到规则召回"
                if retrieved.fallback_reason
                else "候选地点检索完成"
            ),
            public_details=(
                {"mode": retrieved.mode, "candidate_count": retrieved.candidate_count}
                if retrieved.mode
                else {"candidate_count": retrieved.candidate_count}
            ),
        )
        candidates = [item.candidate for item in retrieved.items]
        self._trace(
            observer,
            stage=RunStage.CONSTRUCT,
            status=RunEventStatus.STARTED,
            message_key="construct.search_started",
            public_message="正在组合候选行程",
        )
        local_result = _rank_plan_specs(
            candidates,
            constraints,
            preferred_specs or fallback_specs,
            active_planning_intent,
            semantic_scores=(
                semantic_scores
                if not active_planning_intent.semantic_request.is_empty
                else None
            ),
            explicit_structure=plan_choices.explicit_structure,
            planning_intent_source=active_planning_intent_source,
        )
        self._trace(
            observer,
            stage=RunStage.CONSTRUCT,
            status=RunEventStatus.COMPLETED,
            message_key="construct.completed",
            public_message="候选行程组合完成",
            public_details={"count": len(local_result.plans)},
        )
        if weather_removed and not any(
            candidate.resource_type == ResourceType.ACTIVITY
            for candidate in candidates
        ):
            local_result.rejected_fields.add("weather")
        search_trace_by_id = {
            trace.skeleton_id: trace for trace in local_result.search_traces
        }
        search_trace_order = [trace.skeleton_id for trace in local_result.search_traces]

        def increment_search_trace(
            skeleton_id: str | None,
            **increments: int,
        ) -> None:
            key = skeleton_id or "legacy-unknown"
            trace = search_trace_by_id.get(
                key,
                SkeletonSearchTrace(skeleton_id=key),
            )
            if key not in search_trace_by_id:
                search_trace_order.append(key)
            updates = {
                field: getattr(trace, field) + amount
                for field, amount in increments.items()
            }
            search_trace_by_id[key] = trace.model_copy(update=updates)

        def commit_search_traces(current_result: _LocalPlanningResult) -> _LocalPlanningResult:
            return replace(
                current_result,
                search_traces=tuple(
                    search_trace_by_id[key] for key in search_trace_order
                ),
            )

        plans = []
        availability_facts_by_plan: dict[str, tuple[AvailabilityFact, ...]] = {}
        verification_warnings_by_plan: dict[str, tuple[VerificationFinding, ...]] = {}
        route_failure_fields: set[str] = set()
        route_failure_field_sets: list[set[str]] = []
        availability_batches = 0
        route_leg_verifications = 0
        exhausted_repair_chain = False
        verified_compositions: set[str] = set()
        had_route_candidates = False
        preferred_failure_fields: set[str] = set()
        fallback_failure_fields: set[str] = set()
        fallback_route_reserve = 0
        fallback_availability_reserve = 0
        if preferred_specs and fallback_specs:
            max_fallback_route_legs = max(
                len(spec.roles) + int(constraints.planning_window.explicit_return_deadline is not None)
                for spec in fallback_specs
            )
            fallback_route_reserve = min(
                _MAX_ROUTE_LEG_VERIFICATIONS // 2,
                max_fallback_route_legs * _RULE_FALLBACK_MAX_CANDIDATES,
            )
            fallback_availability_reserve = min(
                _MAX_AVAILABILITY_BATCHES // 2,
                _RULE_FALLBACK_MAX_CANDIDATES,
            )

        def verify_local_candidates(
            current_result: _LocalPlanningResult,
            *,
            route_budget_limit: int,
            availability_batch_limit: int,
        ) -> _VerifiedPlanningResult:
            """Run one shared Route/Availability/Verifier pass.

            Preferred and Rule fallback specs both cross this seam. Budgets
            and verified-composition de-duplication are shared across passes.
            The preferred path leaves a bounded reserve for Rule recovery, so
            it cannot consume the entire request budget before fallback runs.
            """

            nonlocal availability_batches, route_leg_verifications
            nonlocal exhausted_repair_chain, had_route_candidates
            route_candidates = _select_route_candidates(
                current_result.plans,
                max(0, route_budget_limit - route_leg_verifications),
                has_return_leg=constraints.planning_window.explicit_return_deadline is not None,
            )
            if route_candidates:
                had_route_candidates = True
            for route_candidate in route_candidates:
                increment_search_trace(
                    route_candidate.skeleton_id,
                    route_candidates=1,
                )

            batch_plans: list[Plan] = []
            batch_failure_fields: set[str] = set()
            batch_failure_field_sets: list[set[str]] = []
            repair_coordinator = _RepairCoordinator()
            pending_candidates = deque(
                _FinalistAttempt(
                    plan=plan,
                    root_fingerprint=plan.composition_fingerprint,
                )
                for plan in route_candidates
            )
            while pending_candidates:
                attempt = pending_candidates.popleft()
                plan = attempt.plan
                if plan.composition_fingerprint in verified_compositions:
                    continue
                verified_compositions.add(plan.composition_fingerprint)
                verified = self._rebuild_route_timeline(
                    plan,
                    constraints,
                    candidate_by_id,
                )
                route_request_count = len(verified.route_legs)
                route_leg_verifications += route_request_count
                increment_search_trace(
                    plan.skeleton_id,
                    route_provider_requests=route_request_count,
                    route_leg_count=len(verified.route_legs),
                )
                verified = _refresh_verified_score(
                    verified,
                    constraints,
                    time_coverage=active_planning_intent.time_coverage,
                )
                availability_facts: tuple[AvailabilityFact, ...] = ()
                availability_budget_exhausted = (
                    availability_batches >= availability_batch_limit
                )
                if not availability_budget_exhausted:
                    availability_facts = tuple(
                        self._availability_provider.check(
                            AvailabilityRequest(
                                date=constraints.planning_window.date.value,
                                checks=tuple(
                                    AvailabilityCheck(
                                        resource_id=stop.resource_id,
                                        start=stop.start,
                                        end=stop.end,
                                    )
                                    for stop in verified.stops
                                ),
                            )
                        )
                    )
                    availability_batches += 1
                verification = self._plan_verifier.verify(
                    verified,
                    constraints,
                    candidate_by_id,
                    availability_facts=availability_facts,
                )
                if not verification.is_feasible:
                    issue_fields = {
                        violation.field
                        for violation in verification.violations
                    }
                    batch_failure_fields.update(issue_fields)
                    batch_failure_field_sets.append(issue_fields)
                    route_failure_fields.update(issue_fields)
                    route_failure_field_sets.append(issue_fields)
                    repair = repair_coordinator.decide(
                        context=_RepairContext(
                            failed=attempt,
                            findings=verification.violations,
                            remaining_route_leg_budget=(
                                route_budget_limit
                                - route_leg_verifications
                            ),
                            remaining_availability_batches=(
                                availability_batch_limit
                                - availability_batches
                            ),
                        ),
                        pending=pending_candidates,
                        verified_compositions=verified_compositions,
                    )
                    if repair.outcome == RepairOutcome.LOCAL_REPLACEMENT:
                        if repair.replacement is None:
                            raise AssertionError(
                                "local replacement repair requires a replacement"
                            )
                        pending_candidates.appendleft(repair.replacement)
                    elif repair.outcome == RepairOutcome.TERMINAL:
                        exhausted_repair_chain = (
                            exhausted_repair_chain
                            or attempt.repair_rounds >= _MAX_LOCAL_REPLAN_ROUNDS
                        )
                    continue
                batch_plans.append(verified)
                availability_facts_by_plan[verified.plan_id] = availability_facts
                verification_warnings_by_plan[verified.plan_id] = verification.warnings
                if availability_budget_exhausted:
                    verification_warnings_by_plan[verified.plan_id] = (
                        *verification.warnings,
                        _availability_budget_warning(),
                    )
                if len(batch_plans) == _MAX_FEASIBLE_PLANS:
                    break
            return _VerifiedPlanningResult(
                plans=batch_plans,
                route_candidates=tuple(route_candidates),
                failure_fields=batch_failure_fields,
                failure_field_sets=tuple(batch_failure_field_sets),
                exhausted_repair_chain=exhausted_repair_chain,
            )

        self._trace(
            observer,
            stage=RunStage.VERIFY,
            status=RunEventStatus.STARTED,
            message_key="verify.started",
            public_message="正在核对路线、时间和可用性",
        )
        preferred_result = local_result
        preferred_verified = verify_local_candidates(
            preferred_result,
            route_budget_limit=(
                _MAX_ROUTE_LEG_VERIFICATIONS - fallback_route_reserve
            ),
            availability_batch_limit=(
                _MAX_AVAILABILITY_BATCHES - fallback_availability_reserve
            ),
        )
        plans = preferred_verified.plans
        preferred_failure_fields = set(preferred_result.rejected_fields)
        preferred_failure_fields.update(preferred_verified.failure_fields)
        local_result = commit_search_traces(preferred_result)

        # An accepted model path may fail at local scheduling or only after
        # route/availability/Verifier checks. Restore the complete Rule path:
        # deterministic semantics, a fresh role-scoped retrieval, and Rule
        # PlanSpecs. Provider budgets and the verification seam remain shared.
        fallback_verified: _VerifiedPlanningResult | None = None
        if (
            not plans
            and preferred_specs
            and fallback_specs
            and not plan_choices.explicit_structure
        ):
            fallback_stage = "route" if preferred_result.plans else "local_search"
            structure_fallback_stage = fallback_stage
            structure_fallback_reason = (
                structure_fallback_reason
                or (
                    "preferred_route_verification_failed"
                    if fallback_stage == "route"
                    else "preferred_local_search_infeasible"
                )
            )

            fallback_retrieved, fallback_semantic_scores = (
                self._retrieve_for_plan_spec_roles(
                    catalog_candidates,
                    rule_baseline,
                    fallback_specs,
                )
            )
            retrieval_runtime_decision = _merge_retrieval_runtime(
                retrieval_runtime_decision,
                _retrieval_runtime(fallback_retrieved),
            )
            retrieved = fallback_retrieved
            semantic_scores = fallback_semantic_scores
            candidates = [item.candidate for item in fallback_retrieved.items]
            active_planning_intent = rule_baseline
            active_planning_intent_source = "fallback"

            fallback_local = _rank_plan_specs(
                candidates,
                constraints,
                fallback_specs,
                rule_baseline,
                semantic_scores=(
                    fallback_semantic_scores
                    if not rule_baseline.semantic_request.is_empty
                    else None
                ),
                explicit_structure=False,
                planning_intent_source="fallback",
            )
            fallback_failure_fields = set(fallback_local.rejected_fields)
            combined_local = _merge_local_planning_results(
                fallback_local,
                preferred=preferred_result,
            )
            search_trace_by_id = {
                trace.skeleton_id: trace
                for trace in combined_local.search_traces
            }
            search_trace_order = [
                trace.skeleton_id for trace in combined_local.search_traces
            ]
            fallback_verified = verify_local_candidates(
                fallback_local,
                route_budget_limit=_MAX_ROUTE_LEG_VERIFICATIONS,
                availability_batch_limit=_MAX_AVAILABILITY_BATCHES,
            )
            plans = fallback_verified.plans
            fallback_failure_fields.update(fallback_verified.failure_fields)
            structure_fallback_used = bool(fallback_verified.plans)
            local_result = combined_local
            local_result = commit_search_traces(local_result)
        else:
            local_result = commit_search_traces(local_result)

        exhausted_repair_chain = exhausted_repair_chain or (
            fallback_verified.exhausted_repair_chain
            if fallback_verified is not None
            else False
        )
        self._trace(
            observer,
            stage=RunStage.VERIFY,
            status=RunEventStatus.COMPLETED if plans else RunEventStatus.FALLBACK,
            message_key="verify.completed" if plans else "verify.fallback",
            public_message=(
                "路线和硬约束核对完成"
                if plans
                else "候选方案未通过完整核验，已返回安全结果"
            ),
            public_details=(
                {"plan_count": len(plans)}
                if plans
                else {"reason_code": "no_verified_plan"}
            ),
        )
        if plans:
            if structure_fallback_stage == "compile":
                structure_fallback_used = True
            plans = _apply_dynamic_strategies(
                plans,
                constraints,
                weather,
                candidate_by_id,
            )
            plans.sort(
                key=lambda plan: (
                    -plan.total_score,
                    plan.total_duration_minutes,
                    plan.composition_fingerprint,
                )
            )
            plans = _diversify_plans(plans, _MAX_RETURNED_PLANS)
            for selected_plan in plans:
                increment_search_trace(
                    selected_plan.skeleton_id,
                    final_selected=1,
                )
            local_result = commit_search_traces(local_result)
            selected_ids = {
                stop.resource_id
                for plan in plans
                for stop in plan.stops
            }
            warnings = _collect_final_warnings(
                plans,
                weather,
                verification_warnings_by_plan,
                catalog_result.warnings,
                time_coverage=active_planning_intent.time_coverage,
            )
            return CandidateSet(
                plans=plans,
                provider_facts=[
                    weather,
                    *[
                        fact
                        for plan in plans
                        for fact in availability_facts_by_plan.get(plan.plan_id, ())
                    ],
                ],
                catalog_violations=catalog_result.violations,
                catalog_warnings=[
                    warning
                    for warning in catalog_result.warnings
                    if warning.resource_id in selected_ids
                ],
                warnings=warnings,
                planning_intent_decision=planning_intent_decision,
                runtime_decision=runtime_decision,
                retrieval_runtime_decision=retrieval_runtime_decision,
                retrieval_mode=retrieved.mode,
                retrieval_index_version=retrieved.index_version,
                semantic_request=active_planning_intent.semantic_request,
                retrieval_evidence=_retrieval_evidence_for_plans(retrieved, plans),
                **_plan_spec_metadata(
                    planning_intent_decision,
                    plan_choices,
                    fallback_used=structure_fallback_used,
                    fallback_reason=structure_fallback_reason,
                    fallback_stage=structure_fallback_stage,
                    preferred_failure_fields=preferred_failure_fields,
                    fallback_failure_fields=fallback_failure_fields,
                ),
                **_search_metadata(local_result),
            )

        # Diagnose a strict-budget exhaustion before returning a generic route
        # failure.  A role-complete local candidate can still reach this point
        # when every route verification attempt fails; if the catalog proves
        # that an eligible role had only over-budget resources, the user-facing
        # cause is still the budget rather than an opaque structure conflict.
        strict_budget_is_blocking = (
            local_result.rejected_fields == {"budget_per_person"}
            or (
                not local_result.rejected_fields
                and _catalog_budget_exhausted_for_plan_specs(
                    catalog_result,
                    plan_specs,
                )
            )
        )
        if constraints.strict_budget and strict_budget_is_blocking:
            budget = (
                constraints.budget_per_person.value
                if constraints.budget_per_person
                else None
            )
            conflict = ConstraintConflict(
                code="NO_PLAN_WITHIN_STRICT_BUDGET",
                message=f"当前目录中没有满足人均 {budget} 元严格预算的可行行程方案。",
                fields=["budget_per_person"],
            )
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=conflict,
                recovery_reason=RecoveryReasonAdapter().from_conflict(
                    conflict,
                    constraints,
                    search_traces=local_result.search_traces,
                    catalog_violations=catalog_result.violations,
                    route_failure_field_sets=route_failure_field_sets,
                ),
                planning_intent_decision=planning_intent_decision,
                runtime_decision=runtime_decision,
                retrieval_runtime_decision=retrieval_runtime_decision,
                retrieval_mode=retrieved.mode,
                retrieval_index_version=retrieved.index_version,
                **_plan_spec_metadata(
                    planning_intent_decision,
                    plan_choices,
                    fallback_used=structure_fallback_used,
                    fallback_reason=structure_fallback_reason,
                    fallback_stage=structure_fallback_stage,
                    preferred_failure_fields=preferred_failure_fields,
                    fallback_failure_fields=fallback_failure_fields,
                ),
                **_search_metadata(local_result),
            )

        if had_route_candidates:
            universal_failure_fields = (
                set.intersection(*route_failure_field_sets)
                if route_failure_field_sets
                else set()
            )
            reported_failure_fields = (
                universal_failure_fields or route_failure_fields
            )
            availability_failed_all = bool(route_failure_field_sets) and all(
                "availability" in finding for finding in route_failure_field_sets
            )
            conflict = ConstraintConflict(
                code=(
                    "NO_PLAN_AFTER_AVAILABILITY"
                    if availability_failed_all
                    else "NO_PLAN_AFTER_LOCAL_REPLAN"
                    if exhausted_repair_chain
                    else "NO_PLAN_AFTER_ROUTE_VERIFICATION"
                ),
                message=(
                    "动态可用性复核后，没有确认有位或可执行的候选地点。"
                    if availability_failed_all
                    else "路线和整单可行性复核后，候选方案均违反硬约束。"
                ),
                fields=sorted(
                    set(reported_failure_fields)
                    | set(_meal_anchor_conflict_fields(constraints))
                ),
            )
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=conflict,
                recovery_reason=RecoveryReasonAdapter().from_conflict(
                    conflict,
                    constraints,
                    search_traces=local_result.search_traces,
                    catalog_violations=catalog_result.violations,
                    route_failure_field_sets=route_failure_field_sets,
                ),
                planning_intent_decision=planning_intent_decision,
                runtime_decision=runtime_decision,
                retrieval_runtime_decision=retrieval_runtime_decision,
                retrieval_mode=retrieved.mode,
                retrieval_index_version=retrieved.index_version,
                **_plan_spec_metadata(
                    planning_intent_decision,
                    plan_choices,
                    fallback_used=structure_fallback_used,
                    fallback_reason=structure_fallback_reason,
                    fallback_stage=structure_fallback_stage,
                    preferred_failure_fields=preferred_failure_fields,
                    fallback_failure_fields=fallback_failure_fields,
                ),
                **_search_metadata(local_result),
            )

        conflict_fields = [
            field
            for field in (
                "departure_at",
                "duration_minutes",
                "time_window",
                "max_distance_km",
                "total_distance_km",
                "return_by",
                "budget_per_person",
                "weather",
                "party",
            )
            if field in local_result.rejected_fields
        ]
        empty_role_pool = any(
            trace.rejected_by.get("empty_role_pool", 0)
            for trace in local_result.search_traces
        )
        if empty_role_pool and constraints.required_stop_roles is not None:
            conflict_fields.append("required_stop_roles")
        # Catalog hard filters can eliminate every candidate before local
        # skeleton enumeration.  Preserve those concrete rejection fields
        # (distance, budget, opening hours, etc.) instead of falling through
        # to a misleading generic structure diagnosis.
        catalog_fields = {
            violation.field
            for violation in catalog_result.violations
            if violation.field
        }
        # Catalog rejections explain a failed run only when no candidate made
        # it into local search.  Once search ran, rejected POIs are incidental:
        # report the plan-level findings instead of mixing unrelated opening,
        # date, or radius filters into the final diagnosis.
        no_catalog_candidates = not catalog_result.candidates
        if no_catalog_candidates and "weather" not in local_result.rejected_fields:
            conflict_fields.extend(sorted(catalog_fields))
        if no_catalog_candidates and any(
            violation.code == "outside_basic_opening_hours"
            for violation in catalog_result.violations
        ):
            conflict_fields.append("opening_hours")
        conflict_fields = [
            *dict.fromkeys(
                [
                    *conflict_fields,
                    *_meal_anchor_conflict_fields(constraints),
                ]
            )
        ]
        if no_catalog_candidates and catalog_fields:
            if "max_distance_km" in conflict_fields:
                failure_code = "NO_CANDIDATES_WITHIN_SEARCH_RADIUS"
            elif "opening_hours" in conflict_fields:
                failure_code = "NO_CANDIDATES_WITHIN_OPENING_HOURS"
            elif "weather" in conflict_fields:
                failure_code = "NO_CANDIDATES_FOR_WEATHER"
            else:
                failure_code = "NO_CANDIDATES_AFTER_HARD_FILTER"
        else:
            rejected_counts: dict[str, int] = {}
            for trace in local_result.search_traces:
                for field, count in trace.rejected_by.items():
                    rejected_counts[field] = rejected_counts.get(field, 0) + count
            time_rejections = sum(
                rejected_counts.get(field, 0)
                for field in (
                    "departure_at",
                    "duration_minutes",
                    "time_window",
                    "return_by",
                    "meal_window",
                )
            )
            distance_rejections = sum(
                rejected_counts.get(field, 0)
                for field in ("max_distance_km", "total_distance_km")
            )

            # Local composition checks use a conservative straight-line route
            # estimate before any Route Provider call. When both time and
            # distance findings exist, report the dominant observed rejection
            # instead of letting field-list order mislabel a distance failure
            # as a scheduling failure.
            if distance_rejections > time_rejections:
                failure_code = "NO_PLAN_WITHIN_DISTANCE"
            elif time_rejections:
                failure_code = "NO_SCHEDULE_WITHIN_TIME_WINDOW"
            elif distance_rejections or {
                "max_distance_km",
                "total_distance_km",
            } & set(conflict_fields):
                failure_code = "NO_PLAN_WITHIN_DISTANCE"
            elif "opening_hours" in conflict_fields:
                failure_code = "NO_CANDIDATES_WITHIN_OPENING_HOURS"
            elif "weather" in conflict_fields:
                failure_code = "NO_CANDIDATES_FOR_WEATHER"
            elif empty_role_pool:
                failure_code = (
                    "NO_CANDIDATES_FOR_REQUIRED_ROLES"
                    if constraints.required_stop_roles is not None
                    else "NO_CANDIDATES_FOR_PLAN_STRUCTURE"
                )
            else:
                failure_code = "NO_FEASIBLE_PLAN"
        conflict = ConstraintConflict(
            code=failure_code,
            message="当前目录中没有满足全部硬约束的可行行程方案。",
            fields=conflict_fields,
        )
        return CandidateSet(
            provider_facts=[weather],
            catalog_violations=catalog_result.violations,
            conflict=conflict,
            recovery_reason=RecoveryReasonAdapter().from_conflict(
                conflict,
                constraints,
                search_traces=local_result.search_traces,
                catalog_violations=catalog_result.violations,
            ),
            planning_intent_decision=planning_intent_decision,
            runtime_decision=runtime_decision,
            retrieval_runtime_decision=retrieval_runtime_decision,
            retrieval_mode=retrieved.mode,
            retrieval_index_version=retrieved.index_version,
            **_plan_spec_metadata(
                planning_intent_decision,
                plan_choices,
                fallback_used=structure_fallback_used,
                fallback_reason=structure_fallback_reason,
                fallback_stage=structure_fallback_stage,
                preferred_failure_fields=preferred_failure_fields,
                fallback_failure_fields=fallback_failure_fields,
            ),
            **_search_metadata(local_result),
        )

    def modify_selected_plan(
        self,
        *,
        selected_plan: Plan,
        constraints: PlanRequest,
        command: ConversationCommand,
        observer: RunObserver | None = None,
    ) -> PlanModificationResult:
        """Replace exactly one selected-plan slot without reopening its shape.

        A modification is deliberately not implemented by calling ``plan()`` on
        a filtered catalog.  That approach can choose a different skeleton or
        silently exchange another station when a plan contains repeated roles.
        Instead, the selected plan is treated as a fixed sequence with one
        ``OpenSlot``.  Every replacement candidate is inserted at that index and
        then sent through the same route, availability and whole-plan verifier
        used by ordinary planning.
        """
        if command.operation != CommandOperation.REPLACE or command.target is None:
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION",
                "当前只支持在已选择方案中替换一个明确站点。",
                fields=["conversation_command"],
            )

        target_matches = _resolve_stop_reference(selected_plan, command.target)
        if len(target_matches) != 1:
            return PlanModificationResult(
                question=QuestionDecision(
                    need_question=True,
                    field="target_reference",
                    question=(
                        "需要明确要替换哪一站，请说明站点序号，或使用方案中的“换这站”按钮。"
                    ),
                    severity="blocking",
                )
            )
        target_index, target_stop = target_matches[0]
        if target_stop.role is None:
            return _modification_conflict(
                "INVALID_MODIFICATION_TARGET",
                "当前站点缺少可用于替换的行程角色。",
                fields=["target_reference", "plan_structure"],
            )

        # The UI does not need to send locks: every non-target position is an
        # implicit FixedStop.  Explicit legacy locks are still resolved and
        # validated so an old natural-language command cannot authorize an
        # unrelated resource or accidentally target the open slot.
        explicit_locks: list[tuple[int, Stop]] = []
        for reference in command.locked_targets:
            matches = _resolve_stop_reference(selected_plan, reference)
            if len(matches) != 1:
                return PlanModificationResult(
                    question=QuestionDecision(
                        need_question=True,
                        field="locked_stop",
                        question="无法唯一定位要保留的站点，请说明第几站或使用方案中的站点按钮。",
                        severity="blocking",
                    )
                )
            lock = matches[0]
            if lock[0] == target_index:
                return _modification_conflict(
                    "INVALID_MODIFICATION_TARGETS",
                    "替换目标不能同时作为保留站点。",
                    fields=["target_reference", "locked_stop"],
                )
            if lock[0] not in {index for index, _ in explicit_locks}:
                explicit_locks.append(lock)

        if not selected_plan.stops or len(selected_plan.stops) > 4:
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION_STRUCTURE",
                "当前方案的站点数量不在可替换范围内。",
                fields=["plan_structure"],
            )
        if any(stop.role is None for stop in selected_plan.stops):
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION_STRUCTURE",
                "当前方案缺少完整的站点角色，无法安全匹配替换候选。",
                fields=["plan_structure"],
            )
        if not selected_plan.skeleton_id:
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION_STRUCTURE",
                "当前方案缺少可恢复的骨架信息，无法安全保持站点顺序。",
                fields=["plan_structure"],
            )

        selected_spec = PlanSpec(
            spec_id=selected_plan.skeleton_id,
            roles=tuple(
                stop.role
                for stop in selected_plan.stops
                if stop.role is not None
            ),
        )
        self._trace(
            observer,
            stage=RunStage.RETRIEVE,
            status=RunEventStatus.STARTED,
            message_key="retrieve.replacement_started",
            public_message="正在检索替换候选",
        )
        recalled = self._catalog.recall(constraints)
        candidate_by_id = {
            candidate.resource_id: candidate for candidate in recalled.candidates
        }
        fixed_indices = tuple(index for index in range(len(selected_plan.stops)) if index != target_index)
        fixed_ids = {
            selected_plan.stops[index].resource_id for index in fixed_indices
        }
        missing_fixed = [
            selected_plan.stops[index]
            for index in fixed_indices
            if selected_plan.stops[index].resource_id not in candidate_by_id
        ]
        if missing_fixed:
            self._trace(
                observer,
                stage=RunStage.RETRIEVE,
                status=RunEventStatus.FALLBACK,
                message_key="retrieve.replacement_fallback",
                public_message="替换候选检索无法保留原方案中的固定站点",
                public_details={"reason_code": "locked_stop_unavailable"},
            )
            return PlanModificationResult(
                candidate_set=CandidateSet(
                    catalog_violations=recalled.violations,
                    catalog_warnings=recalled.warnings,
                    conflict=ConstraintConflict(
                        code="LOCKED_STOP_UNAVAILABLE",
                        message="原方案中的非目标站点当前无法满足目录约束，系统没有擅自解除固定位置。",
                        fields=["locked_stop"],
                    ),
                )
            )

        weather = self._weather_provider.get_weather(
            WeatherRequest(
                city=constraints.location.value.city,
                district=constraints.location.value.district,
                adcode=constraints.location.value.adcode or "110105",
                date=constraints.planning_window.date.value,
            )
        )
        accepted_types = _ROLE_RESOURCE_TYPES.get(target_stop.role, frozenset())
        replacement_candidates = [
            candidate
            for candidate in recalled.candidates
            if candidate.resource_type in accepted_types
            and candidate.resource_id != target_stop.resource_id
            and candidate.resource_id not in fixed_ids
            and not (weather.is_adverse and candidate.weather_sensitive)
        ]
        if not replacement_candidates:
            self._trace(
                observer,
                stage=RunStage.RETRIEVE,
                status=RunEventStatus.FALLBACK,
                message_key="retrieve.replacement_fallback",
                public_message="当前没有可用的替换候选",
                public_details={"reason_code": "no_replacement_candidates"},
            )
            return _modification_conflict(
                "NO_REPLACEMENT_CANDIDATES",
                "当前目录中没有与该站点角色兼容的其他候选。",
                fields=["replacement", "catalog"],
            )

        criteria = command.replacement_criteria
        semantic_criteria = tuple(
            criterion
            for criterion in criteria
            if isinstance(criterion, SemanticCriterion)
        )
        needs_shorter_route = any(
            isinstance(criterion, RouteObjective)
            for criterion in criteria
        )
        replacement_semantic_request = compile_replacement_semantics(
            criteria,
            target_role=target_stop.role,
            evidence=command.evidence,
        )
        retrieved_replacements = self._candidate_retriever.retrieve(
            RetrievalRequest(
                candidates=tuple(replacement_candidates),
                semantic_request=replacement_semantic_request,
                target_role=target_stop.role,
                excluded_resource_ids=frozenset(fixed_ids | {target_stop.resource_id}),
            )
        )
        retrieval_runtime_decision = _retrieval_runtime(retrieved_replacements)
        self._trace(
            observer,
            stage=RunStage.RETRIEVE,
            status=(
                RunEventStatus.FALLBACK
                if retrieved_replacements.fallback_reason
                else RunEventStatus.COMPLETED
            ),
            message_key=(
                "retrieve.replacement_fallback"
                if retrieved_replacements.fallback_reason
                else "retrieve.replacement_completed"
            ),
            public_message=(
                "替换候选检索已回退到规则召回"
                if retrieved_replacements.fallback_reason
                else "替换候选检索完成"
            ),
            public_details={
                "mode": retrieved_replacements.mode,
                "candidate_count": retrieved_replacements.candidate_count,
            },
        )
        retrieval_scores = {
            item.candidate.resource_id: item.score.final_score
            for item in retrieved_replacements.items
        }
        replacement_candidates = [item.candidate for item in retrieved_replacements.items]
        origin = GeoPoint(
            latitude=constraints.location.value.latitude,
            longitude=constraints.location.value.longitude,
        )
        # ``missing_fixed`` was handled above; the target resource itself may be
        # stale, so only use a placeholder-free sequence for the cheap distance
        # estimate below.
        base_sequence = tuple(
            candidate_by_id.get(stop.resource_id)
            for stop in selected_plan.stops
        )

        def candidate_rank(candidate: StopCandidate) -> tuple[float, int, float, str]:
            semantic_matches = sum(
                len(_matching_terms(criterion.text, _candidate_terms(candidate)))
                for criterion in semantic_criteria
            )
            sequence = list(base_sequence)
            sequence[target_index] = candidate
            cheap_distance = _estimate_sequence_distance(
                tuple(item for item in sequence if item is not None),
                origin,
                has_return_leg=constraints.planning_window.explicit_return_deadline is not None,
            )
            return (
                (
                    -retrieval_scores.get(candidate.resource_id, 0.0)
                    if retrieved_replacements.mode != "rule"
                    else 0.0
                ),
                -semantic_matches,
                cheap_distance,
                candidate.resource_id,
            )

        replacement_candidates.sort(key=candidate_rank)
        # Route and availability budgets are shared with normal planning.  A
        # four-stop plan with a return leg can therefore inspect only a bounded
        # number of replacements, while still allowing the two requested
        # finalists whenever the provider budget permits.
        legs_per_candidate = len(selected_plan.stops) + int(constraints.planning_window.explicit_return_deadline is not None)
        max_candidates = max(
            1,
            min(
                8,
                _MAX_AVAILABILITY_BATCHES,
                _MAX_ROUTE_LEG_VERIFICATIONS // max(1, legs_per_candidate),
            ),
        )
        replacement_candidates = replacement_candidates[:max_candidates]

        modification_intent = build_rule_based_planning_intent(constraints)
        base_distance = _total_route_distance(selected_plan)
        verified_plans: list[tuple[Plan, tuple[VerificationFinding, ...], tuple[AvailabilityFact, ...], int]] = []
        route_leg_verifications = 0
        availability_batches = 0
        self._trace(
            observer,
            stage=RunStage.VERIFY,
            status=RunEventStatus.STARTED,
            message_key="verify.replacement_started",
            public_message="正在核对替换后的整套路线",
        )
        for replacement_candidate in replacement_candidates:
            sequence_candidates = list(base_sequence)
            sequence_candidates[target_index] = replacement_candidate
            if any(item is None for item in sequence_candidates):
                continue
            local_plan, rejected_field = _build_local_plan(
                tuple(item for item in sequence_candidates if item is not None),
                selected_spec,
                constraints,
                modification_intent,
                semantic_scores=(
                    retrieval_scores
                    if retrieved_replacements.mode != "rule"
                    else None
                ),
            )
            if local_plan is None:
                del rejected_field
                continue
            if route_leg_verifications + legs_per_candidate > _MAX_ROUTE_LEG_VERIFICATIONS:
                break
            verified = self._rebuild_route_timeline(
                local_plan,
                constraints,
                candidate_by_id,
            )
            route_leg_verifications += len(verified.route_legs)
            verified = _refresh_verified_score(
                verified,
                constraints,
                time_coverage=modification_intent.time_coverage,
            )
            availability_facts: tuple[AvailabilityFact, ...] = ()
            if availability_batches < _MAX_AVAILABILITY_BATCHES:
                availability_facts = tuple(
                    self._availability_provider.check(
                        AvailabilityRequest(
                            date=constraints.planning_window.date.value,
                            checks=tuple(
                                AvailabilityCheck(
                                    resource_id=stop.resource_id,
                                    start=stop.start,
                                    end=stop.end,
                                )
                                for stop in verified.stops
                            ),
                        )
                    )
                )
                availability_batches += 1
            verification = self._plan_verifier.verify(
                verified,
                constraints,
                candidate_by_id,
                availability_facts=availability_facts,
            )
            if not verification.is_feasible:
                continue
            total_distance = _total_route_distance(verified)
            if needs_shorter_route and not total_distance < base_distance - 1e-6:
                continue
            matches = [
                evidence
                for criterion in semantic_criteria
                for evidence in _matching_terms(
                    criterion.text,
                    _candidate_terms(replacement_candidate),
                )
            ]
            semantic_points = min(10.0, 5.0 * len(matches))
            semantic_contribution = ScoreContribution(
                rule_id="planning.replacement.semantic.v1",
                dimension="replacement_preference",
                points=semantic_points,
                message="只按目标替换候选自身的名称和标签匹配修改偏好。",
                evidence=sorted(set(matches)),
            )
            verified = verified.model_copy(
                update={
                    "score_breakdown": [*verified.score_breakdown, semantic_contribution],
                    "total_score": round(verified.total_score + semantic_points, 1),
                    "highlights": (
                        [
                            *verified.highlights,
                            f"替换候选匹配：{'、'.join(sorted(set(matches)))}",
                        ]
                        if matches
                        else verified.highlights
                    ),
                    "tradeoffs": (
                        verified.tradeoffs
                        if matches or not semantic_criteria
                        else [
                            *verified.tradeoffs,
                            *[
                                f"替换候选未找到明确标签证据：{criterion.text}"
                                for criterion in semantic_criteria
                            ],
                        ]
                    ),
                }
            )
            verified_plans.append(
                (verified, verification.warnings, availability_facts, len(matches))
            )

        if not verified_plans:
            self._trace(
                observer,
                stage=RunStage.VERIFY,
                status=RunEventStatus.FALLBACK,
                message_key="verify.replacement_fallback",
                public_message="替换候选未通过整套方案核验",
                public_details={"reason_code": "no_replacement_plan"},
            )
            conflict_code = "NO_CLOSER_REPLACEMENT" if needs_shorter_route else "NO_REPLACEMENT_PLAN"
            conflict_message = (
                "完整路线复核后，没有找到全程距离确实更短的替换方案。"
                if needs_shorter_route
                else "候选替换均未通过完整的时间、营业、预算或动态可用性复核。"
            )
            return PlanModificationResult(
                candidate_set=CandidateSet(
                    provider_facts=[weather],
                    catalog_violations=recalled.violations,
                    catalog_warnings=recalled.warnings,
                    conflict=ConstraintConflict(
                        code=conflict_code,
                        message=conflict_message,
                        fields=["replacement"] + (["route_distance"] if needs_shorter_route else []),
                    ),
                    retrieval_runtime_decision=retrieval_runtime_decision,
                    retrieval_mode=retrieved_replacements.mode,
                    retrieval_index_version=retrieved_replacements.index_version,
                    semantic_request=replacement_semantic_request,
                )
            )

        self._trace(
            observer,
            stage=RunStage.VERIFY,
            status=RunEventStatus.COMPLETED,
            message_key="verify.replacement_completed",
            public_message="替换后的整套路线核验完成",
            public_details={"plan_count": len(verified_plans)},
        )
        verified_plans.sort(
            key=lambda item: (
                -item[3],
                -item[0].total_score,
                _total_route_distance(item[0]),
                item[0].composition_fingerprint,
            )
        )
        verified_plans = verified_plans[:2]
        plans = [item[0] for item in verified_plans]
        verification_warnings = {
            plan.plan_id: item[1] for plan, item in zip(plans, verified_plans, strict=True)
        }
        availability_facts_by_plan = {
            plan.plan_id: item[2] for plan, item in zip(plans, verified_plans, strict=True)
        }
        selected_ids = {stop.resource_id for plan in plans for stop in plan.stops}
        candidate_set = CandidateSet(
            plans=plans,
            provider_facts=[
                weather,
                *[
                    fact
                    for plan in plans
                    for fact in availability_facts_by_plan.get(plan.plan_id, ())
                ],
            ],
            catalog_violations=recalled.violations,
            catalog_warnings=[
                warning for warning in recalled.warnings if warning.resource_id in selected_ids
            ],
            warnings=_collect_final_warnings(
                plans,
                weather,
                verification_warnings,
                recalled.warnings,
                time_coverage=modification_intent.time_coverage,
            ),
            retrieval_runtime_decision=retrieval_runtime_decision,
            retrieval_mode=retrieved_replacements.mode,
            retrieval_index_version=retrieved_replacements.index_version,
            semantic_request=replacement_semantic_request,
            retrieval_evidence=_retrieval_evidence_for_plans(
                retrieved_replacements,
                plans,
            ),
        )
        diffs = tuple(
            PlanDiff(
                base_plan_id=selected_plan.plan_id,
                new_plan_id=plan.plan_id,
                locked_stops=tuple(
                    LockedStop(
                        source_plan_id=selected_plan.plan_id,
                        stop_index=index,
                        resource_id=selected_plan.stops[index].resource_id,
                        role=selected_plan.stops[index].role,
                    )
                    for index in fixed_indices
                ),
                replacements=(
                    StopReplacement(
                        stop_index=target_index,
                        role=target_stop.role,
                        before_resource_id=target_stop.resource_id,
                        before_name=target_stop.name,
                        after_resource_id=plan.stops[target_index].resource_id,
                        after_name=plan.stops[target_index].name,
                    ),
                ),
                route_distance_delta_km=round(
                    _total_route_distance(plan) - base_distance,
                    3,
                ),
                duration_delta_minutes=(
                    plan.total_duration_minutes - selected_plan.total_duration_minutes
                ),
                price_delta=plan.total_price - selected_plan.total_price,
            )
            for plan in plans
        )
        return PlanModificationResult(candidate_set=candidate_set, plan_diffs=diffs)

    @staticmethod
    def _not_run_runtime(reason: str) -> RuntimeDecision:
        return RuntimeDecision(
            stage="planning_intent",
            adapter="not_run",
            model_invoked=False,
            model_name=None,
            attempts=0,
            fallback_reason=reason,
            latency_ms=0,
        )

    def _rebuild_route_timeline(
        self,
        plan: Plan,
        constraints: PlanRequest,
        candidate_by_id: dict[str, StopCandidate],
    ) -> Plan:
        location = constraints.location.value
        current_point = GeoPoint(
            latitude=location.latitude,
            longitude=location.longitude,
        )
        current_name = "出发地"
        start_minutes = _planning_start_minutes(constraints)
        current_minutes = start_minutes
        rebuilt_stops: list[Stop] = []
        rebuilt_legs: list[RouteLeg] = []
        scheduler = TimelineScheduler()

        for stop in plan.stops:
            resource = candidate_by_id.get(stop.resource_id)
            if resource is None:
                raise ValueError(f"route verification requires resource {stop.resource_id}")
            destination = resource.location
            route = self._route_provider.route(
                RouteRequest(
                    origin=current_point,
                    destination=destination,
                    mode=RouteMode.TAXI,
                    departure_at=(
                        datetime.combine(
                            constraints.planning_window.date.value,
                            time.min,
                            tzinfo=ZoneInfo("Asia/Shanghai"),
                        )
                        + timedelta(minutes=current_minutes)
                    ),
                )
            )
            leg_start = _minutes_to_time(current_minutes)
            current_minutes += route.duration_minutes
            leg_end = _minutes_to_time(current_minutes)
            rebuilt_legs.append(
                RouteLeg(
                    origin_name=current_name,
                    destination_name=stop.name,
                    start=leg_start,
                    end=leg_end,
                    mode=route.mode,
                    distance_km=route.distance_km,
                    duration_minutes=route.duration_minutes,
                    source=_route_source(route),
                    provider_mode=route.provider_mode,
                    degraded=route.degraded,
                    degraded_reason=route.degraded_reason,
                    verified_at=route.verified_at,
                    cache_age_seconds=route.cache_age_seconds,
                    geometry=route.geometry,
                )
            )
            # Meal anchors are determined by the role itself.  They must not
            # depend on whether the user happened to state an exact number of
            # stops; explicit role sequences such as lunch -> activity ->
            # dinner need the same temporal semantics.
            scheduled_stop = scheduler.schedule_stop(
                arrival_minutes=current_minutes,
                role=stop.role,
                duration_minutes=stop.duration_minutes,
            )
            current_minutes = scheduled_stop.end_minutes
            rebuilt_stops.append(
                stop.model_copy(
                    update={
                        "start": _minutes_to_time(scheduled_stop.start_minutes),
                        "end": _minutes_to_time(scheduled_stop.end_minutes),
                    }
                )
            )
            current_point = destination
            current_name = stop.name

        if constraints.planning_window.explicit_return_deadline is not None:
            return_route = self._route_provider.route(
                RouteRequest(
                    origin=current_point,
                    destination=GeoPoint(
                        latitude=location.latitude,
                        longitude=location.longitude,
                    ),
                    mode=RouteMode.TAXI,
                    departure_at=(
                        datetime.combine(
                            constraints.planning_window.date.value,
                            time.min,
                            tzinfo=ZoneInfo("Asia/Shanghai"),
                        )
                        + timedelta(minutes=current_minutes)
                    ),
                )
            )
            rebuilt_legs.append(
                RouteLeg(
                    origin_name=current_name,
                    destination_name="出发地",
                    start=_minutes_to_time(current_minutes),
                    end=_minutes_to_time(
                        current_minutes + return_route.duration_minutes
                    ),
                    mode=return_route.mode,
                    distance_km=return_route.distance_km,
                    duration_minutes=return_route.duration_minutes,
                    source=_route_source(return_route),
                    provider_mode=return_route.provider_mode,
                    degraded=return_route.degraded,
                    degraded_reason=return_route.degraded_reason,
                    verified_at=return_route.verified_at,
                    cache_age_seconds=return_route.cache_age_seconds,
                    geometry=return_route.geometry,
                )
            )
            current_minutes += return_route.duration_minutes

        return plan.model_copy(
            update={
                "stops": rebuilt_stops,
                "route_legs": rebuilt_legs,
                "total_duration_minutes": current_minutes - start_minutes,
            }
        )

@dataclass(frozen=True)
class _LocalPlanningResult:
    plans: list[Plan]
    rejected_fields: set[str]
    search_stats: BeamSearchStats | None = None
    search_traces: tuple[SkeletonSearchTrace, ...] = ()
    primary_search_mode: str = "legacy"
    legacy_fallback_used: bool = False
    legacy_fallback_reason: str | None = None
    beam_stats: BeamSearchStats | None = None
    legacy_stats: BeamSearchStats | None = None
    accepted_plan_spec_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _VerifiedPlanningResult:
    """One bounded Route/Availability/Verifier pass over local finalists."""

    plans: list[Plan]
    route_candidates: tuple[Plan, ...]
    failure_fields: set[str]
    failure_field_sets: tuple[set[str], ...]
    exhausted_repair_chain: bool = False


def _search_metadata(result: _LocalPlanningResult | None) -> dict[str, object]:
    """Expose bounded composition metrics without coupling API to the searcher."""

    if result is None or result.search_stats is None:
        return {}
    stats = result.search_stats
    return {
        "search_mode": stats.mode,
        "search_beam_width": stats.beam_width,
        "search_max_expansions": stats.max_expansions,
        "search_theoretical_combinations": stats.theoretical_combinations,
        "search_expansions": stats.expansions,
        "search_finalist_count": stats.finalists,
        "search_pruned_by": dict(stats.pruned_by),
        "search_traces": list(result.search_traces),
        "primary_search_mode": result.primary_search_mode,
        "legacy_fallback_used": result.legacy_fallback_used,
        "legacy_fallback_reason": result.legacy_fallback_reason,
        "beam_expansions": (
            result.beam_stats.expansions if result.beam_stats is not None else None
        ),
        "beam_finalist_count": (
            result.beam_stats.finalists if result.beam_stats is not None else None
        ),
        "legacy_expansions": (
            result.legacy_stats.expansions
            if result.legacy_stats is not None
            else None
        ),
        "accepted_plan_spec_ids": list(result.accepted_plan_spec_ids),
    }


def _plan_spec_metadata(
    decision: object,
    choices: PlanSpecChoices,
    *,
    fallback_used: bool,
    fallback_reason: str | None,
    fallback_stage: str | None = None,
    preferred_failure_fields: Sequence[str] = (),
    fallback_failure_fields: Sequence[str] = (),
) -> dict[str, object]:
    """Serialize PlanSpec compilation provenance into CandidateSet safely."""

    proposal = getattr(decision, "structure_proposal", None)
    return {
        "planning_intent_proposal_schema_version": (
            proposal.schema_version if proposal is not None else None
        ),
        "planning_intent_proposal_slots": (
            [
                {"role": slot.role.value, "inclusion": slot.inclusion}
                for slot in proposal.slots
            ]
            if proposal is not None
            else []
        ),
        "planning_intent_proposal_rejected": (
            proposal is not None and choices.proposal_status == "rejected"
        ),
        "planning_intent_proposal_compiled": choices.proposal_status == "compiled",
        "planning_intent_preferred_spec_ids": [
            spec.spec_id for spec in choices.preferred_specs
        ],
        "planning_intent_fallback_spec_ids": [
            spec.spec_id for spec in choices.fallback_specs
        ],
        "planning_intent_structure_fallback_attempted": fallback_stage is not None,
        "planning_intent_structure_fallback_used": fallback_used,
        "planning_intent_structure_fallback_reason": fallback_reason,
        "planning_intent_structure_fallback_stage": fallback_stage,
        "planning_intent_preferred_failure_fields": sorted(
            set(preferred_failure_fields)
        ),
        "planning_intent_fallback_failure_fields": sorted(
            set(fallback_failure_fields)
        ),
    }


def _dedupe_plan_specs(
    specs: Sequence[PlanSpec],
) -> tuple[PlanSpec, ...]:
    by_key: dict[tuple[str, tuple[StopRole, ...]], PlanSpec] = {}
    for spec in specs:
        by_key[(spec.spec_id, spec.roles)] = spec
    return tuple(by_key.values())


def _merge_local_planning_results(
    fallback: _LocalPlanningResult,
    *,
    preferred: _LocalPlanningResult,
) -> _LocalPlanningResult:
    """Use Rule local candidates while retaining both search traces."""

    preferred_stats = preferred.search_stats
    fallback_stats = fallback.search_stats
    combined_stats = fallback_stats
    if preferred_stats is not None and fallback_stats is not None:
        combined_stats = replace(
            fallback_stats,
            theoretical_combinations=(
                preferred_stats.theoretical_combinations
                + fallback_stats.theoretical_combinations
            ),
            expansions=preferred_stats.expansions + fallback_stats.expansions,
            pruned_by=_merge_rejection_counts(
                preferred_stats.pruned_by,
                fallback_stats.pruned_by,
            ),
        )
    return replace(
        fallback,
        rejected_fields=fallback.rejected_fields | preferred.rejected_fields,
        search_stats=combined_stats,
        search_traces=_merge_search_traces(
            preferred.search_traces,
            fallback.search_traces,
        ),
        accepted_plan_spec_ids=fallback.accepted_plan_spec_ids,
        primary_search_mode=fallback.primary_search_mode,
    )


def _select_route_candidates(
    plans: list[Plan],
    route_leg_budget: int,
    *,
    has_return_leg: bool = False,
) -> list[Plan]:
    by_skeleton: dict[str, deque[Plan]] = {}
    for plan in plans:
        key = plan.skeleton_id or "legacy-unknown"
        by_skeleton.setdefault(key, deque()).append(plan)

    selected: list[Plan] = []
    remaining_legs = route_leg_budget
    while remaining_legs > 0:
        added_this_round = False
        for candidates in by_skeleton.values():
            if not candidates:
                continue
            required_legs = len(candidates[0].stops) + int(has_return_leg)
            if required_legs > remaining_legs:
                candidates.clear()
                continue
            selected.append(candidates.popleft())
            remaining_legs -= required_legs
            added_this_round = True
        if not added_this_round:
            break
    return selected


def _repair_target_resource_ids(
    plan: Plan,
    findings: tuple[VerificationFinding, ...],
) -> set[str]:
    """Return only deterministically attributable stop ids eligible for repair.

    Weather-sensitive activities are deliberately absent: adverse weather is a
    reliable pre-combination prune in this M2 planner. Aggregate duration and
    total-distance failures also remain NEXT_FINALIST because choosing a stop to
    replace would be a guess rather than a trustworthy repair.
    """
    targets: set[str] = set()
    for finding in findings:
        if (
            finding.code in {
                "availability_verified_unavailable",
                "visit_outside_opening_hours",
            }
            and finding.resource_id is not None
        ):
            targets.add(finding.resource_id)
            continue
        if finding.code not in {
            "route_leg_exceeds_distance",
            "return_after_deadline",
        } or finding.route_leg_index is None:
            continue
        if not plan.stops:
            continue
        stop_index = min(finding.route_leg_index, len(plan.stops) - 1)
        if stop_index >= 0:
            targets.add(plan.stops[stop_index].resource_id)
    return targets


def _take_local_replacement(
    pending: deque[_FinalistAttempt],
    failed: _FinalistAttempt,
    target_ids: set[str],
    verified_compositions: set[str],
) -> _FinalistAttempt | None:
    """Pick one same-shape candidate that changes exactly one attributable stop."""
    failed_ids = [stop.resource_id for stop in failed.plan.stops]
    for attempt in pending:
        candidate = attempt.plan
        candidate_ids = [stop.resource_id for stop in candidate.stops]
        if candidate.composition_fingerprint in verified_compositions:
            continue
        if candidate.skeleton_id != failed.plan.skeleton_id or len(candidate_ids) != len(failed_ids):
            continue
        changed = [
            (before, after)
            for before, after in zip(failed_ids, candidate_ids, strict=True)
            if before != after
        ]
        if len(changed) == 1 and changed[0][0] in target_ids:
            pending.remove(attempt)
            return _FinalistAttempt(
                plan=candidate,
                root_fingerprint=failed.root_fingerprint,
                repair_rounds=failed.repair_rounds + 1,
            )
    return None


def _availability_budget_warning() -> VerificationFinding:
    return VerificationFinding(
        code="availability_budget_exhausted",
        field="availability",
        message="已达到动态可用性复核预算，该方案未获得本轮库存确认。",
    )


def _collect_final_warnings(
    plans: list[Plan],
    weather: WeatherFact,
    verification_warnings: dict[str, tuple[VerificationFinding, ...]],
    catalog_warnings: list[object],
    *,
    time_coverage: TimeCoverageObjective | None = None,
) -> list[PlanWarning]:
    """Expose only warnings tied to plans that survived whole-plan verification."""
    warnings: list[PlanWarning] = []
    for plan in plans:
        for finding in verification_warnings.get(plan.plan_id, ()):
            warnings.append(
                PlanWarning(
                    code=finding.code,
                    message=finding.message,
                    plan_id=plan.plan_id,
                    resource_id=finding.resource_id,
                    route_leg_index=finding.route_leg_index,
                )
            )
        resource_ids = {stop.resource_id for stop in plan.stops}
        for warning in catalog_warnings:
            if getattr(warning, "resource_id", None) in resource_ids:
                warnings.append(
                    PlanWarning(
                        code=f"catalog_{warning.code.value}",
                        message=warning.message,
                        plan_id=plan.plan_id,
                        resource_id=warning.resource_id,
                    )
                )
        if weather.degraded or weather.cache_age_seconds is not None or weather.condition == "未知":
            warnings.append(
                PlanWarning(
                    code="weather_fact_unconfirmed",
                    message="天气事实来自降级、缓存或未知条件，建议出发前再次确认。",
                    plan_id=plan.plan_id,
                    source=weather.source.value,
                    degraded=weather.degraded,
                    stale=weather.cache_age_seconds is not None,
                )
            )
        if time_coverage is not None:
            _, _, _, covered_periods = _time_coverage_score(
                time_coverage,
                stops=plan.stops,
                total_duration_minutes=plan.total_duration_minutes,
                available_minutes=plan.total_duration_minutes,
                stop_count=len(plan.stops),
            )
            if len(covered_periods) < len(time_coverage.target_periods):
                missing = "、".join(
                    period.value
                    for period in time_coverage.target_periods
                    if period not in covered_periods
                )
                warnings.append(
                    PlanWarning(
                        code="time_coverage_incomplete",
                        message=f"该方案未完整覆盖用户希望利用的时段：{missing}。",
                        plan_id=plan.plan_id,
                    )
                )
    return warnings


def _diversify_plans(plans: list[Plan], max_count: int) -> list[Plan]:
    """从已按总分排序的可行候选中，贪心保留彼此有实际差异的最多 max_count 个。

    第一个总是最高分方案；之后每次都选“与已选集合差异最大”的候选，直到
    差异低于门槛或凑够数量，从而避免返回内容近似的数值 Top-3。
    """
    if not plans or max_count <= 0:
        return []
    selected = [plans[0]]
    remaining = plans[1:]
    while len(selected) < max_count and remaining:
        best_index = max(
            range(len(remaining)),
            key=lambda index: _min_plan_diversity(remaining[index], selected),
        )
        best = remaining[best_index]
        if _min_plan_diversity(best, selected) < _MIN_PLAN_DIVERSITY:
            break
        selected.append(best)
        remaining.pop(best_index)
    return selected


def _apply_dynamic_strategies(
    plans: list[Plan],
    constraints: PlanRequest,
    weather: WeatherFact,
    candidates: dict[str, StopCandidate],
) -> list[Plan]:
    """Attach the best evidence-backed fixed strategy to each feasible plan.

    Feasibility never depends on this policy. It only reweights already verified
    candidates, so a strategy cannot promote a hard-constraint violation.
    """
    if not plans:
        return []
    comparable_prices = [
        plan.total_price
        for plan in plans
        if plan.price_status != PlanPriceStatus.INCOMPLETE
    ]
    distances = [sum(leg.distance_km for leg in plan.route_legs) for plan in plans]
    base_scores = [plan.total_score for plan in plans]
    experience_scores = [_experience_score(plan) for plan in plans]
    family_scores = [_family_score(plan, candidates) for plan in plans]
    weather_scores = [_weather_score(plan, weather, candidates) for plan in plans]
    active = _active_strategies(constraints, weather)

    enriched: list[Plan] = []
    for index, plan in enumerate(plans):
        cost_is_comparable = plan.price_status != PlanPriceStatus.INCOMPLETE
        metrics: dict[str, float | None] = {
            "base": _normalize_higher(base_scores[index], base_scores),
            # ``total_price`` 仍是展示和已知项目求和所需的算术字段；当任一
            # Stop 价格未知时，它绝不代表完整价格。未知价格不参加省钱比较。
            "cost": (
                _normalize_lower(plan.total_price, comparable_prices)
                if cost_is_comparable
                else None
            ),
            "travel": _normalize_lower(distances[index], distances),
            "experience": _normalize_higher(
                experience_scores[index], experience_scores
            ),
            "family": _normalize_higher(family_scores[index], family_scores),
            "weather": _normalize_higher(weather_scores[index], weather_scores),
        }
        scored_strategies = [
            (strategy, score)
            for strategy in active
            if (score := _strategy_score(strategy, metrics)) is not None
        ]
        strategy, policy_score = max(
            scored_strategies,
            key=lambda item: (item[1], -_strategy_priority(item[0])),
        )
        contribution = ScoreContribution(
            rule_id=f"planning.strategy.{strategy.value}.v1",
            dimension="strategy",
            points=round(policy_score * 8.0, 1),
            message=f"按 {strategy.value} 的固定权重重排已验证候选。",
            evidence=[
                f"base={metrics['base']:.2f}",
                (
                    f"cost={metrics['cost']:.2f}"
                    if metrics["cost"] is not None
                    else "cost=uncomparable"
                ),
                f"price_status={plan.price_status.value}",
                f"travel={metrics['travel']:.2f}",
                f"experience={metrics['experience']:.2f}",
                f"family={metrics['family']:.2f}",
                f"weather={metrics['weather']:.2f}",
            ],
        )
        enriched.append(
            plan.model_copy(
                update={
                    "strategy": strategy,
                    "score_breakdown": [*plan.score_breakdown, contribution],
                    "total_score": round(plan.total_score + contribution.points, 1),
                }
            )
        )
    return enriched


def _active_strategies(
    constraints: PlanRequest,
    weather: WeatherFact,
) -> tuple[PlanStrategy, ...]:
    strategies = [
        PlanStrategy.BALANCED,
        PlanStrategy.LOW_COST,
        PlanStrategy.LOW_TRAVEL,
    ]
    if constraints.preferences or constraints.diet_tags or constraints.scene_tags:
        strategies.append(PlanStrategy.EXPERIENCE)
    if constraints.party and constraints.party.value.children > 0:
        strategies.append(PlanStrategy.FAMILY_SAFE)
    if weather.is_adverse:
        strategies.append(PlanStrategy.WEATHER_SAFE)
    return tuple(strategies)


def _strategy_score(
    strategy: PlanStrategy,
    metrics: dict[str, float | None],
) -> float | None:
    weights: dict[PlanStrategy, dict[str, float]] = {
        PlanStrategy.BALANCED: {
            "base": 0.45,
            "cost": 0.15,
            "travel": 0.20,
            "experience": 0.20,
        },
        PlanStrategy.LOW_COST: {
            "base": 0.10,
            "cost": 0.70,
            "travel": 0.15,
            "experience": 0.05,
        },
        PlanStrategy.LOW_TRAVEL: {
            "base": 0.15,
            "cost": 0.15,
            "travel": 0.70,
        },
        PlanStrategy.EXPERIENCE: {
            "base": 0.15,
            "travel": 0.10,
            "experience": 0.75,
        },
        PlanStrategy.FAMILY_SAFE: {
            "base": 0.15,
            "travel": 0.15,
            "family": 0.70,
        },
        PlanStrategy.WEATHER_SAFE: {
            "base": 0.15,
            "travel": 0.15,
            "weather": 0.70,
        },
    }
    # 价格不完整时，LOW_COST 没有可比较的事实，因此不能把未知价方案包装成
    # “更省钱”。其余策略将缺失的 cost 视为中性值，只降低这条软偏好的影响，
    # 不改变已经由 Verifier 得出的硬约束结论。
    if strategy == PlanStrategy.LOW_COST and metrics["cost"] is None:
        return None
    return sum(
        (metrics[dimension] if metrics[dimension] is not None else 0.5) * weight
        for dimension, weight in weights[strategy].items()
    )


def _strategy_priority(strategy: PlanStrategy) -> int:
    return (
        PlanStrategy.BALANCED,
        PlanStrategy.LOW_COST,
        PlanStrategy.LOW_TRAVEL,
        PlanStrategy.EXPERIENCE,
        PlanStrategy.FAMILY_SAFE,
        PlanStrategy.WEATHER_SAFE,
    ).index(strategy)


def _normalize_higher(value: float, values: list[float]) -> float:
    lower, upper = min(values), max(values)
    return 1.0 if upper == lower else (value - lower) / (upper - lower)


def _normalize_lower(value: float, values: list[float]) -> float:
    lower, upper = min(values), max(values)
    return 1.0 if upper == lower else (upper - value) / (upper - lower)


def _experience_score(plan: Plan) -> float:
    return sum(
        contribution.points
        for contribution in plan.score_breakdown
        if contribution.dimension in {"preference", "diet", "scene"}
    )


def _family_score(plan: Plan, candidates: dict[str, StopCandidate]) -> float:
    family_terms = {"亲子", "儿童", "家庭"}
    return float(
        sum(
            bool(
                family_terms
                & {
                    tag.casefold()
                    for tag in (
                        candidate.category_tags
                        + candidate.preference_tags
                        + candidate.scene_tags
                    )
                }
            )
            for stop in plan.stops
            if (candidate := candidates.get(stop.resource_id)) is not None
        )
    )


def _weather_score(
    plan: Plan,
    weather: WeatherFact,
    candidates: dict[str, StopCandidate],
) -> float:
    if not weather.is_adverse:
        return 0.0
    activities = [
        candidates[stop.resource_id]
        for stop in plan.stops
        if stop.resource_id in candidates
        and candidates[stop.resource_id].resource_type == ResourceType.ACTIVITY
    ]
    return float(sum(not candidate.weather_sensitive for candidate in activities))


def _min_plan_diversity(candidate: Plan, selected: list[Plan]) -> float:
    return min(_plan_diversity(candidate, plan) for plan in selected)


def _plan_diversity(left: Plan, right: Plan) -> float:
    """0=完全相同，1=完全不同；按 POI 重叠、成本和路程三个可观察指标衡量。"""
    left_ids = {stop.resource_id for stop in left.stops}
    right_ids = {stop.resource_id for stop in right.stops}
    union = len(left_ids | right_ids)
    poi_overlap = len(left_ids & right_ids) / union if union else 0.0
    price_gap = abs(left.total_price - right.total_price) / max(
        1.0, left.total_price, right.total_price
    )
    left_distance = sum(leg.distance_km for leg in left.route_legs)
    right_distance = sum(leg.distance_km for leg in right.route_legs)
    distance_gap = abs(left_distance - right_distance) / max(
        0.5, left_distance, right_distance
    )
    similarity = (
        0.6 * poi_overlap
        + 0.2 * (1.0 - price_gap)
        + 0.2 * (1.0 - distance_gap)
    )
    diversity = 1.0 - similarity
    # A different stop count is a meaningful user-visible alternative even
    # when the shorter plan reuses most of the longer plan's POIs.  Without
    # this floor, adding the newly feasible meal-anchored three-stop plan could
    # hide a valid four-stop full-day plan as a near duplicate.
    if left.skeleton_id != right.skeleton_id and len(left.stops) != len(right.stops):
        diversity = max(diversity, 0.4)
    return diversity


_build_planning_intent = build_rule_based_planning_intent


def _rank_plan_specs(
    candidates: list[StopCandidate],
    constraints: PlanRequest,
    plan_specs: Sequence[PlanSpec],
    planning_intent: PlanningIntent,
    semantic_scores: dict[str, float] | None = None,
    *,
    explicit_structure: bool = False,
    planning_intent_source: str = "rule_based",
) -> _LocalPlanningResult:
    mode = os.getenv("HFT_PLANNER_SEARCH_MODE", "beam").strip().lower()
    if mode not in {"legacy", "beam"}:
        raise RuntimeError("HFT_PLANNER_SEARCH_MODE must be one of: legacy, beam")
    if mode == "beam":
        beam_result = _rank_plan_specs_beam(
            candidates,
            constraints,
            plan_specs,
            planning_intent,
            semantic_scores=semantic_scores,
        )
        beam_result = replace(
            beam_result,
            primary_search_mode="beam",
            beam_stats=beam_result.search_stats,
            accepted_plan_spec_ids=tuple(
                spec.spec_id for spec in plan_specs
            ),
        )
        fallback_reason = _beam_fallback_reason(
            beam_result,
            explicit_structure=explicit_structure,
            planning_intent_source=planning_intent_source,
        )
        if fallback_reason is None:
            return beam_result

        # Legacy is deliberately invoked only when Beam failed before any
        # route/provider truth was observed.  This is a search recovery path,
        # not a way to hide a real budget, distance, timing, availability, or
        # provider conflict.
        legacy_result = _rank_plan_specs_legacy(
            candidates,
            constraints,
            plan_specs,
            planning_intent,
            semantic_scores=semantic_scores,
        )
        merged_traces = _merge_search_traces(
            beam_result.search_traces,
            legacy_result.search_traces,
        )
        combined_stats = replace(
            beam_result.search_stats,
            expansions=(
                (beam_result.search_stats.expansions if beam_result.search_stats else 0)
                + (legacy_result.search_stats.expansions if legacy_result.search_stats else 0)
            ),
            finalists=(legacy_result.search_stats.finalists if legacy_result.search_stats else 0),
            theoretical_combinations=(
                (beam_result.search_stats.theoretical_combinations if beam_result.search_stats else 0)
                + (legacy_result.search_stats.theoretical_combinations if legacy_result.search_stats else 0)
            ),
            pruned_by=_merge_rejection_counts(
                beam_result.search_stats.pruned_by if beam_result.search_stats else (),
                legacy_result.search_stats.pruned_by if legacy_result.search_stats else (),
            ),
        )
        return replace(
            legacy_result,
            search_stats=combined_stats,
            search_traces=merged_traces,
            primary_search_mode="beam",
            legacy_fallback_used=True,
            legacy_fallback_reason=fallback_reason,
            beam_stats=beam_result.search_stats,
            legacy_stats=legacy_result.search_stats,
            accepted_plan_spec_ids=beam_result.accepted_plan_spec_ids,
        )
    return _rank_plan_specs_legacy(
        candidates,
        constraints,
        plan_specs,
        planning_intent,
        semantic_scores=semantic_scores,
        accepted_plan_spec_ids=tuple(
            spec.spec_id for spec in plan_specs
        ),
    )


_SEARCH_HARD_FAILURE_FIELDS = frozenset(
    {
        "departure_at",
        "time_window",
        "duration_minutes",
        "max_distance_km",
        "total_distance_km",
        "return_by",
        "budget_per_person",
        "opening_hours",
        "meal_window",
        "availability",
        "weather",
        "party",
    }
)


def _beam_fallback_reason(
    result: _LocalPlanningResult,
    *,
    explicit_structure: bool,
    planning_intent_source: str,
) -> str | None:
    """Return a safe Legacy fallback code for a pre-provider Beam miss.

    The fallback is intentionally conservative.  A hard rejection, or a
    catalog role with no candidates, is already a product truth and must not
    be disguised as a search miss.  Only a bounded search miss with a
    non-empty theoretical space is eligible.
    """

    stats = result.search_stats
    if stats is None or result.plans:
        return None
    if stats.theoretical_combinations <= 0:
        return None
    if result.rejected_fields & _SEARCH_HARD_FAILURE_FIELDS:
        return None
    if result.search_traces and all(
        "empty_role_pool" in trace.rejected_by
        and trace.theoretical_combinations == 0
        for trace in result.search_traces
    ):
        return None
    if stats.expansions >= stats.max_expansions:
        return "beam_budget_exhausted"
    if explicit_structure:
        return "beam_explicit_structure_no_local_finalist"
    if planning_intent_source == "llm":
        return "beam_llm_plan_spec_no_local_finalist"
    return "beam_no_local_finalist"


def _merge_rejection_counts(
    *values: tuple[tuple[str, int], ...],
) -> tuple[tuple[str, int], ...]:
    merged: dict[str, int] = {}
    for items in values:
        for key, count in items:
            merged[key] = merged.get(key, 0) + count
    return tuple(sorted(merged.items()))


def _merge_search_traces(
    *groups: tuple[SkeletonSearchTrace, ...],
) -> tuple[SkeletonSearchTrace, ...]:
    by_id: dict[str, SkeletonSearchTrace] = {}
    order: list[str] = []
    for group in groups:
        for trace in group:
            current = by_id.get(trace.skeleton_id)
            if current is None:
                by_id[trace.skeleton_id] = trace
                order.append(trace.skeleton_id)
                continue
            rejected = dict(current.rejected_by)
            for key, count in trace.rejected_by.items():
                rejected[key] = rejected.get(key, 0) + count
            by_id[trace.skeleton_id] = current.model_copy(
                update={
                    "theoretical_combinations": max(
                        current.theoretical_combinations,
                        trace.theoretical_combinations,
                    ),
                    "expansions": current.expansions + trace.expansions,
                    "finalists": current.finalists + trace.finalists,
                    "local_schedule_passes": (
                        current.local_schedule_passes
                        + trace.local_schedule_passes
                    ),
                    "route_candidates": (
                        current.route_candidates + trace.route_candidates
                    ),
                    "route_provider_requests": (
                        current.route_provider_requests
                        + trace.route_provider_requests
                    ),
                    "route_leg_count": (
                        current.route_leg_count + trace.route_leg_count
                    ),
                    "final_selected": (
                        current.final_selected + trace.final_selected
                    ),
                    "rejected_by": rejected,
                }
            )
    return tuple(by_id[key] for key in order)


def _rank_plan_specs_legacy(
    candidates: list[StopCandidate],
    constraints: PlanRequest,
    plan_specs: Sequence[PlanSpec],
    planning_intent: PlanningIntent,
    semantic_scores: dict[str, float] | None = None,
    *,
    accepted_plan_spec_ids: tuple[str, ...] = (),
) -> _LocalPlanningResult:
    plans: list[Plan] = []
    rejected_fields: set[str] = set()
    search_traces: list[SkeletonSearchTrace] = []
    for spec in plan_specs:
        role_pool_limit = (
            _MAX_CANDIDATES_PER_ROLE_FOR_MULTI_STOP
            if len(spec.roles) >= 3
            else None
        )
        role_pools = [
            _candidates_for_role(
                candidates,
                role,
                constraints,
                limit=role_pool_limit,
                semantic_scores=semantic_scores,
            )
            for role in spec.roles
        ]
        theoretical_combinations = math.prod(len(pool) for pool in role_pools)
        expansions = 0
        local_schedule_passes = 0
        rejected_by: dict[str, int] = {}
        if any(not pool for pool in role_pools):
            rejected_by["empty_role_pool"] = 1
            search_traces.append(
                SkeletonSearchTrace(
                    skeleton_id=spec.spec_id,
                    theoretical_combinations=theoretical_combinations,
                    rejected_by=rejected_by,
                )
            )
            continue
        for sequence in product(*role_pools):
            expansions += 1
            if len({item.resource_id for item in sequence}) != len(sequence):
                rejected_by["duplicate_resource"] = rejected_by.get(
                    "duplicate_resource", 0
                ) + 1
                continue
            plan, rejected_field = _build_local_plan(
                sequence,
                spec,
                constraints,
                planning_intent,
                semantic_scores=semantic_scores,
            )
            if plan is not None:
                plans.append(plan)
                local_schedule_passes += 1
            elif rejected_field:
                rejected_fields.add(rejected_field)
                rejected_by[rejected_field] = rejected_by.get(rejected_field, 0) + 1
            else:
                rejected_by["local_plan"] = rejected_by.get("local_plan", 0) + 1
        search_traces.append(
            SkeletonSearchTrace(
                skeleton_id=spec.spec_id,
                theoretical_combinations=theoretical_combinations,
                expansions=expansions,
                finalists=local_schedule_passes,
                local_schedule_passes=local_schedule_passes,
                rejected_by=rejected_by,
            )
        )
    ranked = sorted(
        plans,
        key=lambda plan: (
            -plan.total_score,
            plan.total_duration_minutes,
            plan.composition_fingerprint,
        ),
    )
    unique_plans: list[Plan] = []
    seen_compositions: set[tuple[str, ...]] = set()
    for plan in ranked:
        composition = tuple(stop.resource_id for stop in plan.stops)
        if composition in seen_compositions:
            continue
        seen_compositions.add(composition)
        unique_plans.append(plan)
    legacy_rejected: dict[str, int] = {}
    for trace in search_traces:
        for field, count in trace.rejected_by.items():
            legacy_rejected[field] = legacy_rejected.get(field, 0) + count
    return _LocalPlanningResult(
        plans=unique_plans,
        rejected_fields=rejected_fields,
        search_stats=BeamSearchStats(
            mode="legacy",
            beam_width=0,
            max_expansions=0,
            max_finalists=0,
            theoretical_combinations=sum(
                trace.theoretical_combinations for trace in search_traces
            ),
            expansions=sum(trace.expansions for trace in search_traces),
            finalists=len(unique_plans),
            pruned_by=tuple(sorted(legacy_rejected.items())),
        ),
        search_traces=tuple(search_traces),
        primary_search_mode="legacy",
        legacy_stats=BeamSearchStats(
            mode="legacy",
            beam_width=0,
            max_expansions=0,
            max_finalists=0,
            theoretical_combinations=sum(
                trace.theoretical_combinations for trace in search_traces
            ),
            expansions=sum(trace.expansions for trace in search_traces),
            finalists=len(unique_plans),
            pruned_by=tuple(sorted(legacy_rejected.items())),
        ),
        accepted_plan_spec_ids=accepted_plan_spec_ids,
    )


def _rank_plan_specs_beam(
    candidates: list[StopCandidate],
    constraints: PlanRequest,
    plan_specs: Sequence[PlanSpec],
    planning_intent: PlanningIntent,
    semantic_scores: dict[str, float] | None = None,
) -> _LocalPlanningResult:
    """Route-aware bounded composition search used by S-P3.

    The final local plan construction remains the single source of scoring and
    candidate validity.  Beam search only controls which compositions reach
    that stage, using conservative distance/time/budget lower bounds.
    """

    plans: list[Plan] = []
    rejected_fields: set[str] = set()
    search_traces: list[SkeletonSearchTrace] = []
    expansions = 0
    pruned: dict[str, int] = {}
    config = BeamSearchConfig()
    origin = GeoPoint(
        latitude=constraints.location.value.latitude,
        longitude=constraints.location.value.longitude,
    )

    prepared_specs: list[tuple[PlanSpec, list[list[StopCandidate]], int]] = []
    for spec in plan_specs:
        full_role_pools = [
            _candidates_for_role(
                candidates,
                role,
                constraints,
                limit=None,
                semantic_scores=semantic_scores,
            )
            for role in spec.roles
        ]
        theoretical_combinations = math.prod(len(pool) for pool in full_role_pools)
        if any(not pool for pool in full_role_pools):
            search_traces.append(
                SkeletonSearchTrace(
                    skeleton_id=spec.spec_id,
                    theoretical_combinations=theoretical_combinations,
                    rejected_by={"empty_role_pool": 1},
                )
            )
            continue
        # Keep the theoretical space honest, but order each full pool by the
        # same semantic/role signal used by the bounded search.  Passing raw
        # catalog order here made the first expansion slots miss a strongly
        # matching dessert/quiet candidate even when retrieval had scored it.
        role_pools = [
            _order_beam_role_pool(pool, role, constraints, semantic_scores)
            for role, pool in zip(spec.roles, full_role_pools, strict=True)
        ]
        prepared_specs.append((spec, role_pools, theoretical_combinations))

    remaining_specs = len(prepared_specs)
    for spec, role_pools, theoretical_combinations in prepared_specs:
        if remaining_specs <= 0:
            break
        remaining_expansions = config.max_expansions - expansions
        if remaining_expansions <= 0:
            break
        # Each closed-world structure receives an independent share of the
        # search budget.  Unused budget is redistributed, but one early
        # skeleton cannot consume all expansions/finalists before later
        # skeletons get a chance to produce local plans.
        spec_expansion_budget = max(1, remaining_expansions // remaining_specs)
        spec_finalist_budget = max(
            1,
            config.route_leg_budget // max(1, len(spec.roles)),
        )
        result = bounded_beam_search(
            role_pools=role_pools,
            roles=spec.roles,
            constraints=constraints,
            origin=origin,
            semantic_scores=semantic_scores,
            config=replace(
                config,
                max_expansions=spec_expansion_budget,
                max_finalists=spec_finalist_budget,
            ),
        )
        expansions += result.stats.expansions
        for field, count in result.stats.pruned_by:
            pruned[field] = pruned.get(field, 0) + count
        rejected_fields.update(result.rejected_fields)
        local_schedule_passes = 0
        local_rejected_by = dict(result.stats.pruned_by)
        for sequence in result.sequences:
            plan, rejected_field = _build_local_plan(
                sequence,
                spec,
                constraints,
                planning_intent,
                semantic_scores=semantic_scores,
            )
            if plan is not None:
                plans.append(plan)
                local_schedule_passes += 1
            elif rejected_field:
                rejected_fields.add(rejected_field)
                local_rejected_by[rejected_field] = (
                    local_rejected_by.get(rejected_field, 0) + 1
                )
            else:
                local_rejected_by["local_plan"] = (
                    local_rejected_by.get("local_plan", 0) + 1
                )
        search_traces.append(
            SkeletonSearchTrace(
                skeleton_id=spec.spec_id,
                theoretical_combinations=theoretical_combinations,
                expansions=result.stats.expansions,
                finalists=result.stats.finalists,
                local_schedule_passes=local_schedule_passes,
                rejected_by=local_rejected_by,
            )
        )
        remaining_specs -= 1

    plans.sort(
        key=lambda plan: (
            -plan.total_score,
            plan.total_duration_minutes,
            plan.composition_fingerprint,
        )
    )
    unique_plans: list[Plan] = []
    seen_compositions: set[tuple[str, ...]] = set()
    for plan in plans:
        composition = tuple(stop.resource_id for stop in plan.stops)
        if composition in seen_compositions:
            continue
        seen_compositions.add(composition)
        unique_plans.append(plan)
    # This is the global hand-off pool for route verification.  It is applied
    # only after every skeleton had a local scheduling opportunity.  Preserve
    # at least one finalist per skeleton before filling the remaining slots by
    # score; otherwise a high-scoring two-stop shape can starve every richer
    # structure even though those structures were searched fairly.
    unique_plans = _fair_top_k_by_skeleton(unique_plans, config.max_finalists)
    return _LocalPlanningResult(
        plans=unique_plans,
        rejected_fields=rejected_fields,
        search_stats=BeamSearchStats(
            mode="beam",
            beam_width=config.beam_width,
            max_expansions=config.max_expansions,
            max_finalists=config.max_finalists,
            theoretical_combinations=sum(
                trace.theoretical_combinations for trace in search_traces
            ),
            expansions=expansions,
            finalists=len(unique_plans),
            pruned_by=tuple(sorted(pruned.items())),
        ),
        search_traces=tuple(search_traces),
        primary_search_mode="beam",
        beam_stats=BeamSearchStats(
            mode="beam",
            beam_width=config.beam_width,
            max_expansions=config.max_expansions,
            max_finalists=config.max_finalists,
            theoretical_combinations=sum(
                trace.theoretical_combinations for trace in search_traces
            ),
            expansions=expansions,
            finalists=len(unique_plans),
            pruned_by=tuple(sorted(pruned.items())),
        ),
    )


def _candidates_for_role(
    candidates: list[StopCandidate],
    role: StopRole,
    constraints: PlanRequest,
    *,
    limit: int | None,
    semantic_scores: dict[str, float] | None = None,
) -> list[StopCandidate]:
    accepted_types = _ROLE_RESOURCE_TYPES[role]
    matching = [
        candidate
        for candidate in candidates
        if candidate.resource_type in accepted_types
    ]
    if limit is None or len(matching) <= limit:
        return matching
    if semantic_scores is None:
        return sorted(
            matching,
            key=lambda candidate: _candidate_role_rank(candidate, role, constraints),
        )[:limit]

    # Hybrid retrieval must influence the bounded pool before combinations are
    # enumerated.  Keep the deterministic role rank as a tie-breaker so the
    # Rule mode remains unchanged and equal semantic scores stay reproducible.
    return sorted(
        matching,
        key=lambda candidate: (
            -semantic_scores.get(candidate.resource_id, 0.0),
            *_candidate_role_rank(candidate, role, constraints),
        ),
    )[:limit]


def _fair_top_k_by_skeleton(plans: list[Plan], limit: int) -> list[Plan]:
    """Take a score-ordered pool without starving a searched skeleton."""

    if len(plans) <= limit:
        return plans
    by_skeleton: dict[str, deque[Plan]] = {}
    for plan in plans:
        by_skeleton.setdefault(plan.skeleton_id or "legacy-unknown", deque()).append(plan)
    selected: list[Plan] = []
    while len(selected) < limit:
        added = False
        for queue in by_skeleton.values():
            if not queue:
                continue
            selected.append(queue.popleft())
            added = True
            if len(selected) == limit:
                break
        if not added:
            break
    return selected


def _candidate_role_rank(
    candidate: StopCandidate,
    role: StopRole,
    constraints: PlanRequest,
) -> tuple[float, float, str]:
    """Cheap deterministic pre-rank used only to bound multi-stop enumeration."""

    terms = _candidate_terms(candidate)
    preference_matches = sum(
        bool(_matching_terms(preference, terms))
        for preference in dict.fromkeys(constraints.preferences)
    )
    scene_matches = len(
        _matching_requested_tags(constraints.scene_tags, candidate.scene_tags)
    )
    diet_matches = (
        len(_matching_requested_tags(constraints.diet_tags, candidate.diet_tags))
        if role in {StopRole.MEAL, StopRole.LUNCH, StopRole.DINNER}
        else 0
    )
    avoid_matches = sum(
        bool(_matching_terms(avoid, terms))
        for avoid in dict.fromkeys(constraints.avoid)
    )
    over_budget = bool(
        constraints.budget_per_person
        and candidate.price_kind
        in {PriceKind.KNOWN, PriceKind.ESTIMATED, PriceKind.FREE}
        and (candidate.avg_price or 0) > constraints.budget_per_person.value
    )
    local_score = (
        10.0 * preference_matches
        + 5.0 * scene_matches
        + 5.0 * diet_matches
        - 20.0 * avoid_matches
        - 10.0 * over_budget
    )
    origin = GeoPoint(
        latitude=constraints.location.value.latitude,
        longitude=constraints.location.value.longitude,
    )
    distance = _haversine_km(origin, candidate.location)
    return (-local_score, distance, candidate.resource_id)


def _order_beam_role_pool(
    pool: list[StopCandidate],
    role: StopRole,
    constraints: PlanRequest,
    semantic_scores: dict[str, float] | None,
) -> list[StopCandidate]:
    """Order a full role pool before Beam's per-state bounded prefix.

    The Beam search still owns the expansion budget.  This helper only makes
    the bounded prefix evidence-aware; it never removes candidates or turns a
    heuristic score into a hard fact.
    """

    return sorted(
        pool,
        key=lambda candidate: _beam_candidate_rank(
            candidate,
            role,
            constraints,
            semantic_scores,
        ),
    )


def _beam_candidate_rank(
    candidate: StopCandidate,
    role: StopRole,
    constraints: PlanRequest,
    semantic_scores: dict[str, float] | None,
) -> tuple[float, float, str]:
    """Combine grounded tag evidence with the retriever score for the prefix.

    A pure dense/lexical score can surface a loosely related name before a
    catalog candidate carrying the exact requested preference tag.  Giving
    the deterministic role evidence a small bounded contribution keeps the
    user-visible preference contract intact without making it a hard filter.
    """

    role_rank = _candidate_role_rank(candidate, role, constraints)
    role_signal = max(0.0, -role_rank[0])
    semantic_signal = (semantic_scores or {}).get(candidate.resource_id, 0.0)
    return (
        -(semantic_signal + 0.25 * role_signal),
        role_rank[1],
        candidate.resource_id,
    )


def _build_local_plan(
    sequence: tuple[StopCandidate, ...],
    plan_spec: PlanSpec,
    constraints: PlanRequest,
    planning_intent: PlanningIntent,
    semantic_scores: dict[str, float] | None = None,
) -> tuple[Plan | None, str | None]:
    if len(sequence) != len(plan_spec.roles):
        raise ValueError("plan sequence must fill every PlanSpec role")

    location = constraints.location.value
    window = constraints.planning_window.clock_bounds
    origin = GeoPoint(latitude=location.latitude, longitude=location.longitude)
    route_distances: list[float] = []
    current_point = origin
    for candidate in sequence:
        route_distances.append(_haversine_km(current_point, candidate.location))
        current_point = candidate.location
    scheduler = TimelineScheduler()
    estimated_timeline = scheduler.schedule(
        start_minutes=_planning_start_minutes(constraints),
        roles=plan_spec.roles,
        travel_minutes=tuple(_estimated_route_minutes(distance) for distance in route_distances),
        stop_durations=tuple(item.duration_minutes for item in sequence),
    )
    # Estimated travel is retained for ranking/presentation, but it is not a
    # safe feasibility bound.  A zero-travel schedule is the optimistic lower
    # bound used for early rejection; real route facts are checked later.
    lower_bound_timeline = scheduler.schedule(
        start_minutes=_planning_start_minutes(constraints),
        roles=plan_spec.roles,
        travel_minutes=(0,) * len(sequence),
        stop_durations=tuple(item.duration_minutes for item in sequence),
    )
    total_duration = estimated_timeline.elapsed_minutes
    lower_bound_duration = lower_bound_timeline.elapsed_minutes
    maximum_minutes, target_minutes = _planning_minutes(constraints)
    if constraints.planning_window.explicit_return_deadline:
        start_minutes = _planning_start_minutes(constraints)
        home_arrival = start_minutes + lower_bound_duration
        if home_arrival > _time_to_minutes(
            constraints.planning_window.explicit_return_deadline.value
        ):
            return None, "return_by"
    if lower_bound_duration > maximum_minutes:
        return None, "duration_minutes" if constraints.duration_minutes else "time_window"
    if (
        constraints.max_distance_km
        and max(route_distances)
        > constraints.max_distance_km.value
    ):
        return None, "max_distance_km"

    return_distance = (
        _haversine_km(current_point, origin)
        if constraints.planning_window.explicit_return_deadline
        else 0.0
    )
    if (
        constraints.total_distance_km
        and sum(route_distances) + return_distance
        > constraints.total_distance_km.value
    ):
        return None, "total_distance_km"
    people = 1
    if constraints.party:
        people = max(1, constraints.party.value.adults + constraints.party.value.children)
    per_person_price = sum(
        item.avg_price or 0
        for item in sequence
    )
    total_price = per_person_price * people
    if (
        constraints.strict_budget
        and constraints.budget_per_person
        and per_person_price > constraints.budget_per_person.value
    ):
        return None, "budget_per_person"

    requested_preferences = list(dict.fromkeys(constraints.preferences))
    terms: set[str] = set()
    for candidate in sequence:
        terms.update(_candidate_terms(candidate))
    preference_evidence = {
        preference: _matching_terms(preference, terms)
        for preference in requested_preferences
    }
    matched_preferences = [
        preference for preference, evidence in preference_evidence.items() if evidence
    ]
    meal_candidates = [
        candidate
        for role, candidate in zip(plan_spec.roles, sequence)
        if role in {StopRole.MEAL, StopRole.LUNCH, StopRole.DINNER}
    ]
    matched_diet_tags = _matching_requested_tags(
        constraints.diet_tags,
        [
            tag
            for candidate in meal_candidates
            for tag in candidate.diet_tags
        ],
    )
    matched_scene_tags = _matching_requested_tags(
        constraints.scene_tags,
        [tag for candidate in sequence for tag in candidate.scene_tags],
    )
    avoid_evidence = {
        avoid: _matching_terms(avoid, terms)
        for avoid in dict.fromkeys(constraints.avoid)
    }
    matched_avoid = [avoid for avoid, evidence in avoid_evidence.items() if evidence]
    preference_score = min(20.0, 10.0 * len(matched_preferences))
    diet_score = min(10.0, 5.0 * len(matched_diet_tags))
    scene_score = min(10.0, 5.0 * len(matched_scene_tags))
    duration_score = round(max(
        0.0,
        15.0 * (1 - abs(target_minutes - total_duration) / target_minutes),
    ), 1)
    coverage_score, coverage_message, coverage_evidence, covered_periods = (
        _time_coverage_score(
            planning_intent.time_coverage,
            stops=estimated_timeline.stops,
            stop_roles=plan_spec.roles,
            total_duration_minutes=total_duration,
            available_minutes=maximum_minutes,
            stop_count=len(sequence),
        )
    )
    total_distance = sum(route_distances)
    distance_score = round(
        max(0.0, 5.0 - total_distance / 2),
        1,
    )
    semantic_score = round(
        min(
            12.0,
            8.0 * sum(
                (semantic_scores or {}).get(candidate.resource_id, 0.0)
                for candidate in sequence
            ),
        ),
        1,
    )
    if (
        plan_spec.spec_id
        in {
            "lunch-activity-dinner-v1",
            "activity-break-dinner-v1",
            "activity-lunch-activity-dinner-v1",
        }
    ):
        structure_score = 10.0
    elif planning_intent.pace == PlanPace.RELAXED and len(sequence) == 2:
        structure_score = 10.0
    else:
        structure_score = 5.0
    resource_ids = [item.resource_id for item in sequence]
    score_breakdown = [
        ScoreContribution(
            rule_id="planning.catalog_feasible.v1",
            dimension="feasibility",
            points=40.0,
            message=f"骨架中的 {len(sequence)} 个停靠点均通过 Catalog 单资源硬约束筛选。",
            evidence=resource_ids,
        ),
        ScoreContribution(
            rule_id="planning.skeleton_fit.v1",
            dimension="structure",
            points=structure_score,
            message="按时间窗口、节奏和角色覆盖评价行程骨架。",
            evidence=[
                f"skeleton_id={plan_spec.spec_id}",
                f"pace={planning_intent.pace.value}",
                f"roles={','.join(role.value for role in plan_spec.roles)}",
            ],
        ),
        ScoreContribution(
            rule_id="planning.duration_fit.v1",
            dimension="duration",
            points=duration_score,
            message=f"本地估算总时长 {total_duration} 分钟，目标 {target_minutes} 分钟。",
            evidence=[
                f"estimated_total_minutes={total_duration}",
                f"target_minutes={target_minutes}",
            ],
        ),
        *(
            [
                ScoreContribution(
                    rule_id="planning.time_coverage.v1",
                    dimension="time_coverage",
                    points=coverage_score,
                    message=coverage_message,
                    evidence=coverage_evidence,
                )
            ]
            if planning_intent.time_coverage is not None
            else []
        ),
        ScoreContribution(
            rule_id="planning.travel_distance.v1",
            dimension="travel",
            points=distance_score,
            message="按出发地和相邻停靠点之间的直线距离下界评分。",
            evidence=[
                f"leg_{index}_distance_km={distance:.2f}"
                for index, distance in enumerate(route_distances, start=1)
            ],
        ),
        *(
            [
                ScoreContribution(
                    rule_id="planning.semantic_retrieval.v1",
                    dimension="semantic_relevance",
                    points=semantic_score,
                    message="按候选语义召回相关性进行软排序，未改变硬约束判定。",
                    evidence=[
                        f"{candidate.resource_id}={round((semantic_scores or {}).get(candidate.resource_id, 0.0), 3)}"
                        for candidate in sequence
                        if (semantic_scores or {}).get(candidate.resource_id, 0.0) > 0
                    ],
                )
            ]
            if semantic_scores is not None
            else []
        ),
    ]
    if requested_preferences:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.preference_tag_match.v1",
                dimension="preference",
                points=preference_score,
                message="按可审计概念映射与候选标签证据匹配偏好。",
                evidence=[
                    f"{preference}={tag}"
                    for preference in matched_preferences
                    for tag in preference_evidence[preference]
                ],
            )
        )
    if constraints.diet_tags:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.diet_tag_match.v1",
                dimension="diet",
                points=diet_score,
                message="按餐厅的结构化饮食标签匹配饮食偏好。",
                evidence=[f"matched={item}" for item in matched_diet_tags],
            )
        )
    if constraints.scene_tags:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.scene_tag_match.v1",
                dimension="scene",
                points=scene_score,
                message="按候选的结构化场景标签匹配同行场景。",
                evidence=[f"matched={item}" for item in matched_scene_tags],
            )
        )
    if matched_avoid:
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.avoid_tag_penalty.v1",
                dimension="avoid",
                points=-20.0,
                message="候选标签命中用户希望避开的体验，降低排序分。",
                evidence=[
                    f"{avoid}={tag}"
                    for avoid in matched_avoid
                    for tag in avoid_evidence[avoid]
                ],
            )
        )
    prices_are_usable = all(
        item.price_kind in {PriceKind.KNOWN, PriceKind.ESTIMATED, PriceKind.FREE}
        for item in sequence
    )
    over_budget_preference = bool(
        constraints.budget_per_person
        and prices_are_usable
        and per_person_price > constraints.budget_per_person.value
    )
    if constraints.budget_per_person:
        if prices_are_usable:
            budget_message = (
                f"已知/估算人均合计 {per_person_price} 元，"
                f"预算偏好 {constraints.budget_per_person.value} 元。"
            )
            budget_evidence = [
                f"per_person_price={per_person_price}",
                f"budget_per_person={constraints.budget_per_person.value}",
            ]
        else:
            budget_message = "价格信息不完整，非严格预算不据此淘汰候选。"
            budget_evidence = ["price_status=incomplete"]
        score_breakdown.append(
            ScoreContribution(
                rule_id="planning.budget_preference.v1",
                dimension="budget",
                points=-10.0 if over_budget_preference else 0.0,
                message=budget_message,
                evidence=budget_evidence,
            )
        )
    total_score = round(sum(item.points for item in score_breakdown), 1)

    stops: list[Stop] = []
    for role, candidate, scheduled in zip(
        plan_spec.roles,
        sequence,
        estimated_timeline.stops,
        strict=True,
    ):
        stops.append(_to_stop(candidate, role, scheduled.start_minutes))
    highlights = ["已通过 Catalog 单资源硬约束筛选"]
    if matched_preferences:
        highlights.append(f"匹配偏好：{'、'.join(matched_preferences)}")
    if matched_diet_tags:
        highlights.append(f"匹配饮食：{'、'.join(matched_diet_tags)}")
    if matched_scene_tags:
        highlights.append(f"匹配场景：{'、'.join(matched_scene_tags)}")
    if planning_intent.time_coverage is not None:
        target_periods = planning_intent.time_coverage.target_periods
        missing_periods = tuple(
            period for period in target_periods if period not in covered_periods
        )
        if len(covered_periods) == len(target_periods):
            highlights.append(
                "覆盖目标时段："
                + "、".join(period.value for period in covered_periods)
            )
        else:
            highlights.append(
                "部分覆盖目标时段："
                + "、".join(period.value for period in covered_periods)
            )
    unmatched = [item for item in requested_preferences if item not in matched_preferences]
    tradeoffs = [f"未找到明确标签证据：{item}" for item in unmatched]
    if planning_intent.time_coverage is not None and len(covered_periods) < len(
        planning_intent.time_coverage.target_periods
    ):
        tradeoffs.append(
            "全天目标尚未覆盖："
            + "、".join(period.value for period in missing_periods)
        )
    tradeoffs.extend(
        f"未找到饮食标签证据：{item}"
        for item in constraints.diet_tags
        if item not in matched_diet_tags
    )
    tradeoffs.extend(
        f"未找到场景标签证据：{item}"
        for item in constraints.scene_tags
        if item not in matched_scene_tags
    )
    if matched_avoid:
        tradeoffs.append(f"命中避开项：{'、'.join(matched_avoid)}")
    if over_budget_preference:
        tradeoffs.append(
            f"人均合计 {per_person_price} 元，超出预算偏好 "
            f"{constraints.budget_per_person.value} 元"
        )
    elif constraints.budget_per_person and not prices_are_usable:
        tradeoffs.append("价格信息不完整，无法确认是否满足预算偏好")
    fingerprint = hashlib.sha256(
        "|".join([plan_spec.spec_id, *resource_ids]).encode("utf-8")
    ).hexdigest()[:12]
    return Plan(
        plan_id=f"plan-{uuid.uuid4().hex}",
        composition_fingerprint=f"composition-{fingerprint}",
        skeleton_id=plan_spec.spec_id,
        title=" + ".join(item.name for item in sequence),
        strategy=PlanStrategy.BALANCED,
        total_score=total_score,
        total_price=total_price,
        price_status=_plan_price_status(stops),
        total_duration_minutes=total_duration,
        stops=stops,
        route_legs=[],
        score_breakdown=score_breakdown,
        highlights=highlights,
        tradeoffs=tradeoffs,
    ), None


def _time_coverage_score(
    objective: TimeCoverageObjective | None,
    *,
    stops: Sequence[object],
    stop_roles: Sequence[StopRole | None] | None = None,
    total_duration_minutes: int,
    available_minutes: int,
    stop_count: int,
) -> tuple[float, str, list[str], tuple[TimeScope, ...]]:
    """Score meaningful day-part coverage without creating a hard gate."""

    if objective is None:
        return 0.0, "", [], ()

    intervals: list[tuple[int, int, StopRole | None]] = []
    for index, stop in enumerate(stops):
        role = getattr(stop, "role", None)
        if role is None and stop_roles is not None and index < len(stop_roles):
            role = stop_roles[index]
        start = getattr(stop, "start_minutes", None)
        end = getattr(stop, "end_minutes", None)
        if start is None or end is None:
            start_text = getattr(stop, "start", None)
            end_text = getattr(stop, "end", None)
            if not start_text or not end_text:
                continue
            start = _time_to_minutes(start_text)
            end = _time_to_minutes(end_text)
        if end > start:
            intervals.append((int(start), int(end), role))

    covered: list[TimeScope] = []
    overlap_evidence: list[str] = []
    for period in objective.target_periods:
        bounds = _TIME_COVERAGE_WINDOWS.get(period)
        if bounds is None:
            continue
        eligible_roles = _TIME_COVERAGE_ROLES.get(period)
        overlap = sum(
            max(0, min(end, bounds[1]) - max(start, bounds[0]))
            for start, end, role in intervals
            if role is None or eligible_roles is None or role in eligible_roles
        )
        overlap_evidence.append(f"{period.value}_overlap_minutes={overlap}")
        if overlap >= _MIN_COVERAGE_OVERLAP_MINUTES:
            covered.append(period)

    target_count = len(objective.target_periods)
    coverage_ratio = len(covered) / target_count if target_count else 0.0
    utilization = min(
        1.0,
        max(0.0, total_duration_minutes / max(1, available_minutes)),
    )
    richness = min(1.0, max(0.0, (stop_count - 1) / 3.0))
    points = round(12.0 * coverage_ratio + 6.0 * utilization + 4.0 * richness, 1)
    message = (
        f"覆盖 {len(covered)}/{target_count} 个目标时段，"
        f"时间利用率 {utilization:.0%}，结构丰富度 {richness:.0%}。"
    )
    evidence = [
        f"target_periods={','.join(period.value for period in objective.target_periods)}",
        f"covered_periods={','.join(period.value for period in covered) or 'none'}",
        *overlap_evidence,
        f"utilization={utilization:.3f}",
        f"stop_count={stop_count}",
        f"evidence={objective.evidence}",
    ]
    return points, message, evidence, tuple(covered)


def _to_stop(
    candidate: StopCandidate,
    role: StopRole,
    start_minutes: int,
) -> Stop:
    return Stop(
        resource_id=candidate.resource_id,
        type=StopType(candidate.resource_type.value),
        role=role,
        name=candidate.name,
        start=_minutes_to_time(start_minutes),
        end=_minutes_to_time(start_minutes + candidate.duration_minutes),
        duration_minutes=candidate.duration_minutes,
        price=candidate.avg_price or 0,
        price_kind=candidate.price_kind,
        category_tags=candidate.category_tags,
        image=candidate.image,
        source=candidate.source,
    )


def _refresh_verified_score(
    plan: Plan,
    constraints: PlanRequest,
    *,
    time_coverage: TimeCoverageObjective | None = None,
) -> Plan:
    _, target_minutes = _planning_minutes(constraints)
    duration_score = round(
        max(
            0.0,
            15.0
            * (
                1
                - abs(target_minutes - plan.total_duration_minutes)
                / target_minutes
            ),
        ),
        1,
    )
    total_distance = sum(leg.distance_km for leg in plan.route_legs)
    distance_score = round(max(0.0, 5.0 - total_distance / 2), 1)
    maximum_minutes, _ = _planning_minutes(constraints)
    coverage_score, coverage_message, coverage_evidence, _ = _time_coverage_score(
        time_coverage,
        stops=plan.stops,
        total_duration_minutes=plan.total_duration_minutes,
        available_minutes=maximum_minutes,
        stop_count=len(plan.stops),
    )
    refreshed: list[ScoreContribution] = []
    for contribution in plan.score_breakdown:
        if contribution.rule_id == "planning.duration_fit.v1":
            refreshed.append(
                contribution.model_copy(
                    update={
                        "points": duration_score,
                        "message": (
                            f"路线复核后总时长 {plan.total_duration_minutes} 分钟，"
                            f"目标 {target_minutes} 分钟。"
                        ),
                        "evidence": [
                            f"route_checked_total_minutes={plan.total_duration_minutes}",
                            f"target_minutes={target_minutes}",
                        ],
                    }
                )
            )
        elif contribution.rule_id == "planning.travel_distance.v1":
            refreshed.append(
                contribution.model_copy(
                    update={
                        "points": distance_score,
                        "message": "按 Route Provider 返回的全部路线段距离评分。",
                        "evidence": [
                            f"route_checked_total_distance_km={total_distance:.2f}",
                        ],
                    }
                )
            )
        elif contribution.rule_id == "planning.time_coverage.v1":
            refreshed.append(
                contribution.model_copy(
                    update={
                        "points": coverage_score,
                        "message": coverage_message,
                        "evidence": coverage_evidence,
                    }
                )
            )
        else:
            refreshed.append(contribution)
    return plan.model_copy(
        update={
            "score_breakdown": refreshed,
            "total_score": round(sum(item.points for item in refreshed), 1),
        }
    )


def _planning_minutes(constraints: PlanRequest) -> tuple[int, int]:
    window = constraints.planning_window.clock_bounds
    available_minutes = _time_to_minutes(window.end) - _planning_start_minutes(constraints)
    maximum_minutes = min(
        available_minutes,
        (
            constraints.duration_minutes.value
            if constraints.duration_minutes
            else available_minutes
        ),
    )
    target_minutes = (
        maximum_minutes
        if constraints.duration_minutes
        else max(1, round(available_minutes * 0.8))
    )
    return maximum_minutes, target_minutes


def _planning_start_minutes(constraints: PlanRequest) -> int:
    """The one canonical start for every local and provider-backed timeline."""
    return _time_to_minutes(
        constraints.planning_window.start_at.value
        if constraints.planning_window.start_at is not None
        else "00:00"
    )


def _planning_time_conflict(
    constraints: PlanRequest,
) -> ConstraintConflict | None:
    """Reject incompatible explicit clocks before any provider call."""
    if constraints.planning_window.explicit_departure is None:
        return None
    departure = _planning_start_minutes(constraints)

    # A direct contradiction between the two user-provided clocks is more
    # specific than any derived planning window.  Enrichment may need an
    # operational horizon for the existing Planner, but it must not mask this
    # chronology error as an outside-window failure.
    if constraints.planning_window.explicit_return_deadline is not None and departure >= _time_to_minutes(
        constraints.planning_window.explicit_return_deadline.value
    ):
        return ConstraintConflict(
            code="DEPARTURE_NOT_BEFORE_RETURN_BY",
            message="准时出发必须早于最晚到家时间。",
            fields=["departure_at", "return_by"],
        )

    window = constraints.planning_window.clock_bounds
    # Only a user-authored numeric range is an availability constraint.  A
    # fuzzy period (morning/afternoon/evening) and a DEFAULT_RULE horizon are
    # scheduling hints that Enrichment may shift around an exact departure.
    explicit_window = _is_explicit_time_window(constraints)
    window_start = _time_to_minutes(window.start)
    window_end = _time_to_minutes(window.end)
    if explicit_window and not window_start <= departure < window_end:
        return ConstraintConflict(
            code="DEPARTURE_OUTSIDE_TIME_WINDOW",
            message="指定的准时出发时刻不在可用时间窗内。",
            fields=["departure_at", "time_window"],
        )
    return None


def _is_explicit_time_window(constraints: PlanRequest) -> bool:
    """Return whether ``time_window`` came from a user numeric range.

    ``ConstraintSource.USER_INFERRED`` is also used for fuzzy language such
    as “下午”, so source alone cannot distinguish a hard availability window
    from a derived scheduling horizon.  The explicit range rule is the stable
    internal marker during Resume V1.
    """

    time_window = constraints.planning_window
    if time_window.clock_bounds is None:
        return False
    return time_window.explicit_trip_range


def _meal_anchor_conflict_fields(
    constraints: PlanRequest,
) -> list[str]:
    """Report an explicit meal role whose usable clock cannot reach its anchor.

    This is diagnostic only: the normal route/Verifier path remains the source
    of feasibility.  The helper prevents a short explicit window from being
    reported merely as a generic ``time_window`` failure after local planning
    prunes every sequence before meal-anchor verification.
    """

    required_roles = (
        constraints.required_stop_roles.value
        if constraints.required_stop_roles is not None
        else ()
    )
    if not required_roles or constraints.planning_window.clock_bounds is None:
        return []

    window = constraints.planning_window.clock_bounds
    window_start = _time_to_minutes(window.start)
    window_end = _time_to_minutes(window.end)
    if constraints.planning_window.explicit_departure is not None:
        window_start = max(
            window_start,
            _time_to_minutes(constraints.planning_window.explicit_departure.value),
        )
    if constraints.planning_window.explicit_return_deadline is not None:
        window_end = min(
            window_end,
            _time_to_minutes(constraints.planning_window.explicit_return_deadline.value),
        )

    for role in required_roles:
        anchor = DEFAULT_TEMPORAL_POLICY.window_for(role)
        if anchor is None:
            continue
        anchor_start, anchor_end = anchor
        if window_end < anchor_start or window_start > anchor_end:
            return ["meal_window"]
    return []


def _candidate_terms(candidate: StopCandidate) -> set[str]:
    return {
        value.strip().casefold()
        for value in (
            candidate.name,
            *candidate.category_tags,
            *candidate.preference_tags,
            *candidate.diet_tags,
            *candidate.scene_tags,
        )
        if value.strip()
    }


def _matching_terms(preference: str, terms: set[str]) -> list[str]:
    normalized = preference.strip().casefold()
    accepted = {normalized, *(_PREFERENCE_ALIASES.get(normalized, frozenset()))}
    return sorted(
        term
        for term in terms
        if term in accepted or normalized in term
    )


def _matching_requested_tags(
    requested: list[str],
    available: list[str],
) -> list[str]:
    available_normalized = {item.strip().casefold() for item in available}
    return [
        item
        for item in dict.fromkeys(requested)
        if item.strip().casefold() in available_normalized
    ]


def _resolve_stop_reference(
    plan: Plan,
    reference: TargetReference,
) -> list[tuple[int, Stop]]:
    """Resolve a proposed reference only inside the authorized selected Plan."""
    matches: list[tuple[int, Stop]] = []
    for index, stop in enumerate(plan.stops):
        if reference.stop_index is not None and index != reference.stop_index:
            continue
        if reference.resource_id is not None and stop.resource_id != reference.resource_id:
            continue
        if reference.role is not None:
            role_matches = stop.role == reference.role
            if reference.role == StopRole.MEAL:
                role_matches = stop.role in {
                    StopRole.MEAL,
                    StopRole.LUNCH,
                    StopRole.DINNER,
                }
            if not role_matches:
                continue
        if (
            reference.resource_type is not None
            and stop.type.value != reference.resource_type.value
        ):
            continue
        matches.append((index, stop))
    return matches


def _modification_conflict(
    code: str,
    message: str,
    *,
    fields: list[str],
) -> PlanModificationResult:
    """Build a failed modification result without leaking partial candidates."""

    return PlanModificationResult(
        candidate_set=CandidateSet(
            conflict=ConstraintConflict(
                code=code,
                message=message,
                fields=fields,
            )
        )
    )


def _estimate_sequence_distance(
    sequence: tuple[StopCandidate, ...],
    origin: GeoPoint,
    *,
    has_return_leg: bool,
) -> float:
    """Cheap deterministic route lower bound used only to order replacements."""

    current = origin
    total = 0.0
    for candidate in sequence:
        total += _haversine_km(current, candidate.location)
        current = candidate.location
    if has_return_leg:
        total += _haversine_km(current, origin)
    return total


def _total_route_distance(plan: Plan) -> float:
    return sum(leg.distance_km for leg in plan.route_legs)


def _haversine_km(origin: GeoPoint, destination: GeoPoint) -> float:
    radius_km = 6371.0088
    start_latitude = math.radians(origin.latitude)
    end_latitude = math.radians(destination.latitude)
    latitude_delta = math.radians(destination.latitude - origin.latitude)
    longitude_delta = math.radians(destination.longitude - origin.longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(start_latitude)
        * math.cos(end_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))


def _estimated_route_minutes(distance_km: float) -> int:
    return max(5, round(distance_km / 25.0 * 60))


def _catalog_budget_exhausted_for_plan_specs(
    catalog_result: CatalogResult,
    plan_specs: Sequence[PlanSpec],
) -> bool:
    """Prove that every eligible PlanSpec lost a role to strict budget.

    A catalog can still contain free/cheap activities while every restaurant
    has been pruned by a ten-yuan budget.  Looking only at
    ``catalog_result.candidates`` therefore misclassifies the outcome as a
    generic structure conflict.  This helper uses the candidate type carried
    by each catalog rejection and requires budget to be present for every
    rejected resource in the exhausted role pool; unrelated distance/opening
    rejections cannot manufacture a budget diagnosis.
    """

    if not catalog_result.violations or not plan_specs:
        return False
    surviving_types = {
        candidate.resource_type for candidate in catalog_result.candidates
    }
    violations_by_type: dict[ResourceType, dict[str, set[str]]] = {}
    for violation in catalog_result.violations:
        if violation.resource_type is None:
            continue
        fields_by_resource = violations_by_type.setdefault(
            violation.resource_type,
            {},
        )
        fields_by_resource.setdefault(violation.resource_id, set()).add(
            violation.field
        )

    for spec in plan_specs:
        budget_exhausted_role = False
        for role in spec.roles:
            role_types = _ROLE_RESOURCE_TYPES[role]
            if surviving_types.intersection(role_types):
                continue
            fields_by_resource: dict[str, set[str]] = {}
            for resource_type in role_types:
                for resource_id, fields in violations_by_type.get(
                    resource_type,
                    {},
                ).items():
                    fields_by_resource.setdefault(resource_id, set()).update(fields)
            if fields_by_resource and all(
                "budget_per_person" in fields
                for fields in fields_by_resource.values()
            ):
                budget_exhausted_role = True
                break
        if not budget_exhausted_role:
            return False
    return True


def _route_source(fact: RouteFact) -> RouteSource:
    sources = {
        ProviderSource.AMAP_LIVE: RouteSource.REAL_PROVIDER,
        ProviderSource.CACHE: RouteSource.CACHE,
        ProviderSource.REPLAY: RouteSource.REPLAY,
        ProviderSource.MOCK: RouteSource.LOCAL_ESTIMATE,
        ProviderSource.LOCAL_ESTIMATE: RouteSource.LOCAL_ESTIMATE,
    }
    return sources[fact.source]


def _plan_price_status(stops: list[Stop]) -> PlanPriceStatus:
    kinds = {stop.price_kind for stop in stops}
    if PriceKind.UNKNOWN in kinds:
        return PlanPriceStatus.INCOMPLETE
    if PriceKind.ESTIMATED in kinds:
        return PlanPriceStatus.ESTIMATED
    return PlanPriceStatus.KNOWN


def _time_to_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _minutes_to_time(value: int) -> str:
    hour, minute = divmod(value, 60)
    return f"{hour:02d}:{minute:02d}"


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))

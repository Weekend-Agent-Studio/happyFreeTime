"""Deterministic bounded planning over normalized Catalog candidates."""

from __future__ import annotations

import hashlib
import math
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from itertools import product
from time import perf_counter
from typing import Sequence
from zoneinfo import ZoneInfo

from app.domain.catalog import (
    ConstraintViolation,
    PriceKind,
    ResourceType,
    StopCandidate,
)
from app.domain.constraints import (
    CommandOperation,
    ConversationCommand,
    effective_replacement_criteria,
    NormalizedConstraints,
    QuestionDecision,
    RouteObjective,
    SemanticCriterion,
    StopRole,
    TargetReference,
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
    PlanSkeleton,
    PlanningIntent,
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
from app.domain.semantics import compile_replacement_semantics
from app.providers.route import LocalEstimateRouteProvider, RouteProvider
from app.providers.availability import AvailabilityProvider, MockAvailabilityProvider
from app.providers.weather import WeatherProvider, clear_mock_weather
from app.services.catalog import Catalog, SnapshotCatalog
from app.services.planning_intent import (
    ACTIVITY_BREAK_DINNER_SKELETON as _ACTIVITY_BREAK_DINNER_SKELETON,
    ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON as _ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON,
    ALL_PLAN_SKELETONS as _ALL_PLAN_SKELETONS,
    LUNCH_ACTIVITY_DINNER_SKELETON as _LUNCH_ACTIVITY_DINNER_SKELETON,
    PlanningIntentProvider,
    RuleBasedPlanningIntentProvider,
    build_rule_based_planning_intent,
)
from app.services.plan_verifier import PlanVerifier, VerificationFinding
from app.services.candidate_retriever import (
    CandidateRetriever,
    RetrievalRequest,
    RetrievedCandidateSet,
    build_default_candidate_retriever,
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


def _retrieval_runtime(result: RetrievedCandidateSet) -> RuntimeDecision:
    """Convert retriever metadata into the safe runtime trace contract."""

    return RuntimeDecision(
        stage="candidate_retrieval",
        adapter=result.actual_adapter,
        model_invoked=result.query_count > 0 and result.actual_adapter == "bge_hybrid",
        model_name=result.model_id,
        attempts=1 if result.query_count > 0 else 0,
        fallback_reason=result.fallback_reason,
        latency_ms=result.latency_ms,
        requested_mode=result.requested_mode,
        index_version=result.index_version,
        query_count=result.query_count,
        candidate_count=result.candidate_count,
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

    def _retrieve_for_skeleton_roles(
        self,
        candidates: list[StopCandidate],
        planning_intent: PlanningIntent,
        skeletons: tuple[PlanSkeleton, ...],
    ) -> tuple[RetrievedCandidateSet, dict[str, float]]:
        """Retrieve independently for each role, then restore Catalog identity order.

        A role-scoped request prevents an activity query from competing directly
        with a restaurant query.  The union is only a bounded candidate set;
        hard catalog filtering already happened before this method and the
        Planner still owns final feasibility.  Identical resource IDs returned
        by more than one compatible meal role are cached in this round.
        """

        roles = tuple(dict.fromkeys(role for skeleton in skeletons for role in skeleton.roles))
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
        actual_mode = "hybrid" if modes == {"hybrid"} else "rule"
        index_versions = tuple(dict.fromkeys(result.index_version for result in results))
        index_version = index_versions[0] if len(index_versions) == 1 else "mixed:" + "+".join(index_versions)
        requested_modes = tuple(dict.fromkeys(result.requested_mode for result in results))
        adapters = tuple(dict.fromkeys(result.actual_adapter for result in results))
        fallback_reasons = tuple(
            dict.fromkeys(
                result.fallback_reason
                for result in results
                if result.fallback_reason
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

    def plan(self, constraints: NormalizedConstraints) -> CandidateSet:
        runtime_decision = self._not_run_runtime(
            "planning cannot start before normalized constraints are complete"
        )
        if (
            constraints.location is None
            or constraints.time_window is None
            or constraints.date is None
        ):
            raise ValueError("planning requires normalized date, time window, and location")

        time_conflict = _planning_time_conflict(constraints)
        if time_conflict is not None:
            return CandidateSet(
                conflict=time_conflict,
                runtime_decision=runtime_decision,
            )

        structure_conflict = _unsupported_plan_structure_conflict(constraints)
        if structure_conflict is not None:
            return CandidateSet(
                conflict=structure_conflict,
                runtime_decision=runtime_decision,
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
                date=constraints.date.value,
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
        skeletons = _select_plan_skeletons(constraints, planning_intent)
        retrieved, semantic_scores = self._retrieve_for_skeleton_roles(
            candidates,
            planning_intent,
            skeletons,
        )
        retrieval_runtime_decision = _retrieval_runtime(retrieved)
        candidates = [item.candidate for item in retrieved.items]
        local_result = _rank_skeleton_plans(
            candidates,
            constraints,
            skeletons,
            planning_intent,
            semantic_scores=(
                semantic_scores if not planning_intent.semantic_request.is_empty else None
            ),
        )
        if weather_removed and not any(
            candidate.resource_type == ResourceType.ACTIVITY
            for candidate in candidates
        ):
            local_result.rejected_fields.add("weather")
        route_candidates = _select_route_candidates(
            local_result.plans,
            _MAX_ROUTE_LEG_VERIFICATIONS,
            has_return_leg=constraints.return_by is not None,
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
            route_leg_verifications += len(verified.route_legs)
            verified = _refresh_verified_score(verified, constraints)
            availability_facts: tuple[AvailabilityFact, ...] = ()
            availability_budget_exhausted = availability_batches >= _MAX_AVAILABILITY_BATCHES
            if not availability_budget_exhausted:
                availability_facts = tuple(
                    self._availability_provider.check(
                        AvailabilityRequest(
                            date=constraints.date.value,
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
                route_failure_fields.update(issue_fields)
                route_failure_field_sets.append(issue_fields)
                repair = repair_coordinator.decide(
                    context=_RepairContext(
                        failed=attempt,
                        findings=verification.violations,
                        remaining_route_leg_budget=(
                            _MAX_ROUTE_LEG_VERIFICATIONS - route_leg_verifications
                        ),
                        remaining_availability_batches=(
                            _MAX_AVAILABILITY_BATCHES - availability_batches
                        ),
                    ),
                    pending=pending_candidates,
                    verified_compositions=verified_compositions,
                )
                if repair.outcome == RepairOutcome.LOCAL_REPLACEMENT:
                    if repair.replacement is None:
                        raise AssertionError("local replacement repair requires a replacement")
                    pending_candidates.appendleft(repair.replacement)
                elif repair.outcome == RepairOutcome.TERMINAL:
                    exhausted_repair_chain = (
                        exhausted_repair_chain
                        or attempt.repair_rounds >= _MAX_LOCAL_REPLAN_ROUNDS
                    )
                continue
            plans.append(verified)
            availability_facts_by_plan[verified.plan_id] = availability_facts
            verification_warnings_by_plan[verified.plan_id] = verification.warnings
            if availability_budget_exhausted:
                verification_warnings_by_plan[verified.plan_id] = (
                    *verification.warnings,
                    _availability_budget_warning(),
                )
            if len(plans) == _MAX_FEASIBLE_PLANS:
                break

        if plans:
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
                semantic_request=planning_intent.semantic_request,
                retrieval_evidence=_retrieval_evidence_for_plans(retrieved, plans),
            )

        if route_candidates:
            universal_failure_fields = (
                set.intersection(*route_failure_field_sets)
                if route_failure_field_sets
                else set()
            )
            reported_failure_fields = (
                universal_failure_fields or route_failure_fields
            )
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=ConstraintConflict(
                    code=(
                        "NO_PLAN_AFTER_LOCAL_REPLAN"
                        if exhausted_repair_chain
                        else "NO_PLAN_AFTER_ROUTE_VERIFICATION"
                    ),
                    message="路线和整单可行性复核后，候选方案均违反硬约束。",
                    fields=[
                        field
                        for field in (
                            "required_stop_roles",
                            "plan_structure",
                            "departure_at",
                            "time_window",
                            "duration_minutes",
                            "max_distance_km",
                            "opening_hours",
                            "meal_window",
                            "return_by",
                            "total_distance_km",
                            "availability",
                    )
                    if field in reported_failure_fields
                    ] + _structure_conflict_fields(planning_intent),
                    relaxation_options=_route_relaxation_options(
                        reported_failure_fields
                    ),
                ),
                planning_intent_decision=planning_intent_decision,
                runtime_decision=runtime_decision,
                retrieval_runtime_decision=retrieval_runtime_decision,
                retrieval_mode=retrieved.mode,
                retrieval_index_version=retrieved.index_version,
            )

        # 严格预算是硬约束：没有满足条件的结果时返回结构化冲突，不能偷偷放宽
        # 后仍告诉用户“已满足预算”。普通不可行则返回更通用的冲突类型。
        strict_budget_is_blocking = (
            local_result.rejected_fields == {"budget_per_person"}
            or (
                not local_result.rejected_fields
                and not catalog_result.candidates
                and _catalog_budget_is_universal(catalog_result.violations)
            )
        )
        if constraints.strict_budget and strict_budget_is_blocking:
            budget = constraints.budget_per_person.value if constraints.budget_per_person else None
            structure_fields = _structure_conflict_fields(planning_intent)
            relaxation_options = (
                ["提高人均预算", "取消严格预算限制"]
                if structure_fields
                else ["提高人均预算", "只保留一个核心停靠点", "允许免费活动搭配简餐"]
            )
            return CandidateSet(
                provider_facts=[weather],
                catalog_violations=catalog_result.violations,
                conflict=ConstraintConflict(
                    code="NO_PLAN_WITHIN_STRICT_BUDGET",
                    message=f"当前目录中没有满足人均 {budget} 元严格预算的可行行程方案。",
                    fields=["budget_per_person", *structure_fields],
                    relaxation_options=relaxation_options,
                ),
                planning_intent_decision=planning_intent_decision,
                runtime_decision=runtime_decision,
                retrieval_runtime_decision=retrieval_runtime_decision,
                retrieval_mode=retrieved.mode,
                retrieval_index_version=retrieved.index_version,
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
        if (
            _structure_conflict_fields(planning_intent)
            and any(
                violation.code == "outside_basic_opening_hours"
                for violation in catalog_result.violations
            )
        ):
            conflict_fields.append("opening_hours")
        structure_fields = _structure_conflict_fields(planning_intent)
        if not conflict_fields and structure_fields:
            conflict_fields = structure_fields
        elif not conflict_fields:
            conflict_fields = ["time_window", "max_distance_km", "party"]
        conflict_fields = [*dict.fromkeys([*conflict_fields, *structure_fields])]
        relaxation_options = []
        if "duration_minutes" in conflict_fields:
            relaxation_options.extend(["增加可用时长", "缩短停留时长"])
        if "max_distance_km" in conflict_fields:
            relaxation_options.append("扩大距离范围")
        if "total_distance_km" in conflict_fields:
            relaxation_options.append("放宽全程距离限制或选择更近的地点")
        if "return_by" in conflict_fields:
            relaxation_options.append("延后最晚到家时间或缩短行程")
        if "budget_per_person" in conflict_fields:
            relaxation_options.append("提高人均预算")
        if "weather" in conflict_fields:
            relaxation_options.extend(["选择室内活动", "调整出行日期"])
        if "party" in conflict_fields:
            relaxation_options.append("调整活动偏好")
        return CandidateSet(
            provider_facts=[weather],
            catalog_violations=catalog_result.violations,
            conflict=ConstraintConflict(
                code="NO_FEASIBLE_PLAN",
                message="当前目录中没有满足全部硬约束的可行行程方案。",
                fields=conflict_fields,
                relaxation_options=relaxation_options,
            ),
            planning_intent_decision=planning_intent_decision,
            runtime_decision=runtime_decision,
            retrieval_runtime_decision=retrieval_runtime_decision,
            retrieval_mode=retrieved.mode,
            retrieval_index_version=retrieved.index_version,
        )

    def modify_selected_plan(
        self,
        *,
        selected_plan: Plan,
        constraints: NormalizedConstraints,
        command: ConversationCommand,
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
                relaxation_options=["先选择方案，再点击某一站的“换这站”"],
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
                relaxation_options=["重新生成方案后重试"],
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
                    relaxation_options=["选择不同的替换目标"],
                )
            if lock[0] not in {index for index, _ in explicit_locks}:
                explicit_locks.append(lock)

        if not selected_plan.stops or len(selected_plan.stops) > 4:
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION_STRUCTURE",
                "当前方案的站点数量不在可替换范围内。",
                fields=["plan_structure"],
                relaxation_options=["重新生成 1 到 4 站方案"],
            )
        if any(stop.role is None for stop in selected_plan.stops):
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION_STRUCTURE",
                "当前方案缺少完整的站点角色，无法安全匹配替换候选。",
                fields=["plan_structure"],
                relaxation_options=["重新生成方案后重试"],
            )
        if not selected_plan.skeleton_id:
            return _modification_conflict(
                "UNSUPPORTED_MODIFICATION_STRUCTURE",
                "当前方案缺少可恢复的骨架信息，无法安全保持站点顺序。",
                fields=["plan_structure"],
                relaxation_options=["重新生成方案后重试"],
            )

        skeleton = PlanSkeleton(
            skeleton_id=selected_plan.skeleton_id,
            roles=tuple(stop.role for stop in selected_plan.stops),
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
            return PlanModificationResult(
                candidate_set=CandidateSet(
                    catalog_violations=recalled.violations,
                    catalog_warnings=recalled.warnings,
                    conflict=ConstraintConflict(
                        code="LOCKED_STOP_UNAVAILABLE",
                        message="原方案中的非目标站点当前无法满足目录约束，系统没有擅自解除固定位置。",
                        fields=["locked_stop"],
                        relaxation_options=["重新选择方案或放宽当前约束"],
                    ),
                )
            )

        weather = self._weather_provider.get_weather(
            WeatherRequest(
                city=constraints.location.value.city,
                district=constraints.location.value.district,
                adcode=constraints.location.value.adcode or "110105",
                date=constraints.date.value,
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
            return _modification_conflict(
                "NO_REPLACEMENT_CANDIDATES",
                "当前目录中没有与该站点角色兼容的其他候选。",
                fields=["replacement", "catalog"],
                relaxation_options=["放宽时间、距离或预算约束"],
            )

        criteria = effective_replacement_criteria(command)
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
                has_return_leg=constraints.return_by is not None,
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
        legs_per_candidate = len(selected_plan.stops) + int(constraints.return_by is not None)
        max_candidates = max(
            1,
            min(
                8,
                _MAX_AVAILABILITY_BATCHES,
                _MAX_ROUTE_LEG_VERIFICATIONS // max(1, legs_per_candidate),
            ),
        )
        replacement_candidates = replacement_candidates[:max_candidates]

        base_intent = build_rule_based_planning_intent(constraints)
        modification_intent = base_intent.model_copy(
            update={
                "minimum_stops": len(skeleton.roles),
                "maximum_stops": len(skeleton.roles),
            }
        )
        base_distance = _total_route_distance(selected_plan)
        verified_plans: list[tuple[Plan, tuple[VerificationFinding, ...], tuple[AvailabilityFact, ...], int]] = []
        route_leg_verifications = 0
        availability_batches = 0
        for replacement_candidate in replacement_candidates:
            sequence_candidates = list(base_sequence)
            sequence_candidates[target_index] = replacement_candidate
            if any(item is None for item in sequence_candidates):
                continue
            local_plan, rejected_field = _build_local_plan(
                tuple(item for item in sequence_candidates if item is not None),
                skeleton,
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
            verified = _refresh_verified_score(verified, constraints)
            availability_facts: tuple[AvailabilityFact, ...] = ()
            if availability_batches < _MAX_AVAILABILITY_BATCHES:
                availability_facts = tuple(
                    self._availability_provider.check(
                        AvailabilityRequest(
                            date=constraints.date.value,
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
                        relaxation_options=["允许距离相近的地点" if needs_shorter_route else "放宽时间、距离或预算约束"],
                    ),
                    retrieval_runtime_decision=retrieval_runtime_decision,
                    retrieval_mode=retrieved_replacements.mode,
                    retrieval_index_version=retrieved_replacements.index_version,
                    semantic_request=replacement_semantic_request,
                )
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
        constraints: NormalizedConstraints,
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
                            constraints.date.value,
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
            stop_start = _minutes_to_time(current_minutes)
            current_minutes += stop.duration_minutes
            rebuilt_stops.append(
                stop.model_copy(
                    update={
                        "start": stop_start,
                        "end": _minutes_to_time(current_minutes),
                    }
                )
            )
            current_point = destination
            current_name = stop.name

        if constraints.return_by is not None:
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
                            constraints.date.value,
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
    constraints: NormalizedConstraints,
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
    constraints: NormalizedConstraints,
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
    return 1.0 - similarity


_build_planning_intent = build_rule_based_planning_intent


def _unsupported_plan_structure_conflict(
    constraints: NormalizedConstraints,
) -> ConstraintConflict | None:
    exact_stop_count = (
        constraints.exact_stop_count.value
        if constraints.exact_stop_count is not None
        else None
    )
    required_roles = (
        constraints.required_stop_roles.value
        if constraints.required_stop_roles is not None
        else None
    )
    if exact_stop_count is None and required_roles is None:
        return None
    if exact_stop_count == 1 and required_roles == (StopRole.DINNER,):
        return None

    fields = []
    if exact_stop_count is not None:
        fields.append("exact_stop_count")
    if required_roles is not None:
        fields.append("required_stop_roles")
    fields.append("plan_structure")
    return ConstraintConflict(
        code="UNSUPPORTED_PLAN_STRUCTURE",
        message="当前版本只支持默认多站规划，或“只安排一家晚饭”的单站结构。",
        fields=fields,
        relaxation_options=["改为只安排一家晚饭", "移除明确站数或角色限制"],
    )


def _select_plan_skeletons(
    constraints: NormalizedConstraints,
    intent: PlanningIntent,
) -> tuple[PlanSkeleton, ...]:
    maximum_minutes, _ = _planning_minutes(constraints)
    return tuple(
        skeleton
        for skeleton in _ALL_PLAN_SKELETONS
        if _skeleton_matches_intent(skeleton, intent, maximum_minutes)
    )


def _skeleton_matches_intent(
    skeleton: PlanSkeleton,
    intent: PlanningIntent,
    available_minutes: int,
) -> bool:
    """Keep every bounded skeleton behind one structural eligibility rule."""
    roles = skeleton.roles
    if not intent.minimum_stops <= len(roles) <= intent.maximum_stops:
        return False
    role_set = set(roles)
    if not set(intent.required_roles).issubset(role_set):
        return False
    if not role_set.issubset(set(intent.required_roles) | set(intent.optional_roles)):
        return False
    for before, after in intent.precedence:
        if before in role_set and after in role_set and roles.index(before) >= roles.index(after):
            return False
    # Preserve the existing capacity gates for richer skeletons. A resource visit
    # cannot be shorter than the catalog's minimum useful slot; exact feasibility
    # remains in _build_local_plan with real candidate durations.
    minimum_capacity = {
        _LUNCH_ACTIVITY_DINNER_SKELETON.skeleton_id: 6 * 60,
        _ACTIVITY_BREAK_DINNER_SKELETON.skeleton_id: 5 * 60,
        _ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON.skeleton_id: 8 * 60,
    }.get(skeleton.skeleton_id, len(roles) * 30)
    return minimum_capacity <= available_minutes


def _structure_conflict_fields(intent: PlanningIntent) -> list[str]:
    if intent.minimum_stops == intent.maximum_stops == 1 and intent.required_roles == (StopRole.DINNER,):
        return ["required_stop_roles", "plan_structure"]
    return []


def _rank_skeleton_plans(
    candidates: list[StopCandidate],
    constraints: NormalizedConstraints,
    skeletons: tuple[PlanSkeleton, ...],
    planning_intent: PlanningIntent,
    semantic_scores: dict[str, float] | None = None,
) -> _LocalPlanningResult:
    plans: list[Plan] = []
    rejected_fields: set[str] = set()
    for skeleton in skeletons:
        role_pool_limit = (
            _MAX_CANDIDATES_PER_ROLE_FOR_MULTI_STOP
            if len(skeleton.roles) >= 3
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
            for role in skeleton.roles
        ]
        if any(not pool for pool in role_pools):
            continue
        for sequence in product(*role_pools):
            if len({item.resource_id for item in sequence}) != len(sequence):
                continue
            plan, rejected_field = _build_local_plan(
                sequence,
                skeleton,
                constraints,
                planning_intent,
                semantic_scores=semantic_scores,
            )
            if plan is not None:
                plans.append(plan)
            elif rejected_field:
                rejected_fields.add(rejected_field)
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
    return _LocalPlanningResult(plans=unique_plans, rejected_fields=rejected_fields)


def _candidates_for_role(
    candidates: list[StopCandidate],
    role: StopRole,
    constraints: NormalizedConstraints,
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


def _candidate_role_rank(
    candidate: StopCandidate,
    role: StopRole,
    constraints: NormalizedConstraints,
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


def _build_local_plan(
    sequence: tuple[StopCandidate, ...],
    skeleton: PlanSkeleton,
    constraints: NormalizedConstraints,
    planning_intent: PlanningIntent,
    semantic_scores: dict[str, float] | None = None,
) -> tuple[Plan | None, str | None]:
    if len(sequence) != len(skeleton.roles):
        raise ValueError("plan sequence must fill every skeleton role")

    location = constraints.location.value
    window = constraints.time_window.value
    origin = GeoPoint(latitude=location.latitude, longitude=location.longitude)
    route_distances: list[float] = []
    current_point = origin
    for candidate in sequence:
        route_distances.append(_haversine_km(current_point, candidate.location))
        current_point = candidate.location
    route_minutes = sum(
        _estimated_route_minutes(distance)
        for distance in route_distances
    )
    total_duration = sum(item.duration_minutes for item in sequence) + route_minutes
    maximum_minutes, target_minutes = _planning_minutes(constraints)
    if total_duration > maximum_minutes:
        return None, "duration_minutes" if constraints.duration_minutes else "time_window"
    if (
        constraints.max_distance_km
        and max(route_distances)
        > constraints.max_distance_km.value
    ):
        return None, "max_distance_km"

    return_distance = (
        _haversine_km(current_point, origin)
        if constraints.return_by
        else 0.0
    )
    if (
        constraints.total_distance_km
        and sum(route_distances) + return_distance
        > constraints.total_distance_km.value
    ):
        return None, "total_distance_km"
    if constraints.return_by:
        start_minutes = _planning_start_minutes(constraints)
        home_arrival = (
            start_minutes
            + total_duration
            + _estimated_route_minutes(return_distance)
        )
        if home_arrival > _time_to_minutes(constraints.return_by.value):
            return None, "return_by"

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
        for role, candidate in zip(skeleton.roles, sequence)
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
        skeleton.skeleton_id
        in {
            _LUNCH_ACTIVITY_DINNER_SKELETON.skeleton_id,
            _ACTIVITY_BREAK_DINNER_SKELETON.skeleton_id,
            _ACTIVITY_LUNCH_ACTIVITY_DINNER_SKELETON.skeleton_id,
        }
        and {StopRole.LUNCH, StopRole.DINNER}.issubset(
            planning_intent.optional_roles
        )
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
                f"skeleton_id={skeleton.skeleton_id}",
                f"pace={planning_intent.pace.value}",
                f"roles={','.join(role.value for role in skeleton.roles)}",
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

    current_minutes = _planning_start_minutes(constraints)
    stops: list[Stop] = []
    for role, candidate, distance in zip(
        skeleton.roles,
        sequence,
        route_distances,
    ):
        current_minutes += _estimated_route_minutes(distance)
        stops.append(_to_stop(candidate, role, current_minutes))
        current_minutes += candidate.duration_minutes
    highlights = ["已通过 Catalog 单资源硬约束筛选"]
    if matched_preferences:
        highlights.append(f"匹配偏好：{'、'.join(matched_preferences)}")
    if matched_diet_tags:
        highlights.append(f"匹配饮食：{'、'.join(matched_diet_tags)}")
    if matched_scene_tags:
        highlights.append(f"匹配场景：{'、'.join(matched_scene_tags)}")
    unmatched = [item for item in requested_preferences if item not in matched_preferences]
    tradeoffs = [f"未找到明确标签证据：{item}" for item in unmatched]
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
        "|".join([skeleton.skeleton_id, *resource_ids]).encode("utf-8")
    ).hexdigest()[:12]
    return Plan(
        plan_id=f"plan-{uuid.uuid4().hex}",
        composition_fingerprint=f"composition-{fingerprint}",
        skeleton_id=skeleton.skeleton_id,
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
    constraints: NormalizedConstraints,
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
        else:
            refreshed.append(contribution)
    return plan.model_copy(
        update={
            "score_breakdown": refreshed,
            "total_score": round(sum(item.points for item in refreshed), 1),
        }
    )


def _planning_minutes(constraints: NormalizedConstraints) -> tuple[int, int]:
    window = constraints.time_window.value
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


def _planning_start_minutes(constraints: NormalizedConstraints) -> int:
    """The one canonical start for every local and provider-backed timeline."""
    return _time_to_minutes(
        constraints.departure_at.value
        if constraints.departure_at is not None
        else constraints.time_window.value.start
    )


def _planning_time_conflict(
    constraints: NormalizedConstraints,
) -> ConstraintConflict | None:
    """Reject incompatible explicit clocks before any provider call."""
    if constraints.departure_at is None:
        return None
    departure = _planning_start_minutes(constraints)
    window = constraints.time_window.value
    window_start = _time_to_minutes(window.start)
    window_end = _time_to_minutes(window.end)
    if not window_start <= departure < window_end:
        return ConstraintConflict(
            code="DEPARTURE_OUTSIDE_TIME_WINDOW",
            message="指定的准时出发时刻不在可用时间窗内。",
            fields=["departure_at", "time_window"],
            relaxation_options=["调整出发时刻或可用时间窗"],
        )
    if constraints.return_by is not None and departure >= _time_to_minutes(
        constraints.return_by.value
    ):
        return ConstraintConflict(
            code="DEPARTURE_NOT_BEFORE_RETURN_BY",
            message="准时出发必须早于最晚到家时间。",
            fields=["departure_at", "return_by"],
            relaxation_options=["提前出发或延后最晚到家时间"],
        )
    return None


def _route_relaxation_options(fields: set[str]) -> list[str]:
    options: list[str] = []
    if fields & {"time_window", "duration_minutes"}:
        options.extend(["延长可用时间", "缩短停留时长"])
    if "max_distance_km" in fields:
        options.append("选择路程更短的地点")
    if "opening_hours" in fields:
        options.append("调整到店时间或选择营业时段更匹配的地点")
    if "meal_window" in fields:
        options.append("调整活动时长或顺序，使用餐落在常规用餐时段")
    if "return_by" in fields:
        options.append("延后最晚到家时间或缩短行程")
    if "total_distance_km" in fields:
        options.append("放宽全程距离限制或选择更近的地点")
    return options


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
    relaxation_options: list[str],
) -> PlanModificationResult:
    """Build a failed modification result without leaking partial candidates."""

    return PlanModificationResult(
        candidate_set=CandidateSet(
            conflict=ConstraintConflict(
                code=code,
                message=message,
                fields=fields,
                relaxation_options=relaxation_options,
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


def _catalog_budget_is_universal(
    violations: list[ConstraintViolation],
) -> bool:
    fields_by_resource: dict[str, set[str]] = {}
    for violation in violations:
        fields_by_resource.setdefault(violation.resource_id, set()).add(violation.field)
    return bool(fields_by_resource) and all(
        "budget_per_person" in fields
        for fields in fields_by_resource.values()
    )


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

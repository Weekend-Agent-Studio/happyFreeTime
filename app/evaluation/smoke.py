"""不调用 LLM 的轻量行为评测，用固定用例守住关键产品结果。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.domain.constraints import ActorContext, IdentityType, Interpretation
from app.domain.evaluation import EvalCase, EvalOutcome, EvalReport, ExpectedOutcome
from app.domain.providers import (
    GeoPoint,
    GeocodeRequest,
    GeocodingFact,
    ProviderMode,
    ProviderSource,
    RouteFact,
    WeatherFact,
    WeatherRequest,
)
from app.providers.availability import MockAvailabilityProvider
from app.services.catalog import SnapshotCatalog
from app.services.enrichment import EnrichmentService, EnvironmentContext
from app.services.opening_hours import visit_fits_opening_hours
from app.services.planning import PlanningService
from app.services.question_gate import GateContext, NeedQuestionGate


HARD_CONSTRAINT_RATE_THRESHOLD = 0.95


def load_cases(path: Path) -> list[EvalCase]:
    """从 JSON 加载并通过 Pydantic 校验评测用例。"""
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase.model_validate(case) for case in raw_cases]


def run_smoke_cases(
    cases: list[EvalCase],
    environment: EnvironmentContext,
) -> EvalReport:
    """运行 Enrichment -> Gate -> Planning，并按结果类型判分。

    这不是最终质量评测：它不测真实 Router 抽取准确率，也不评价推荐主观质量；
    它用于快速发现默认规则、反问字段或冲突语义的回归。
    """
    gate = NeedQuestionGate()
    catalog = SnapshotCatalog()
    actor = ActorContext(
        user_id="eval-user",
        session_id="eval-session",
        identity_type=IdentityType.DEMO,
    )
    outcomes: list[EvalOutcome] = []

    for case in cases:
        geocoding_provider = (
            _ScenarioGeocodingProvider(case.geocoding)
            if case.geocoding is not None
            else None
        )
        enrichment_service = EnrichmentService(geocoding_provider=geocoding_provider)
        interpretation = Interpretation(
            primary_intent=case.intent,
            intent_scores={case.intent: 1.0},
            raw_constraints=case.raw_constraints,
        )
        enrichment = enrichment_service.enrich(interpretation, actor, environment)
        decision = gate.decide(interpretation, enrichment, GateContext())

        # 每个用例只关心一个稳定的外部结果：需要提问、生成方案或返回冲突。
        # 这样内部算法可以重构，而评测仍围绕用户可观察行为。
        if decision.need_question:
            actual = ExpectedOutcome.QUESTION
            passed = (
                case.expected_outcome == actual
                and decision.field == case.expected_question_field
            )
            details = f"question_field={decision.field}"
        else:
            recalled_resources = {
                candidate.resource_id: candidate
                for candidate in catalog.recall(enrichment.constraints).candidates
            }
            planner = PlanningService(
                catalog=catalog,
                weather_provider=(
                    _ScenarioWeatherProvider(case.weather.condition, case.weather.is_adverse)
                    if case.weather is not None
                    else None
                ),
                availability_provider=(
                    MockAvailabilityProvider(
                        statuses=case.availability.statuses,
                        default_status=case.availability.default_status,
                    )
                    if case.availability is not None
                    else None
                ),
                route_provider=(
                    _ScenarioRouteProvider(
                        case.route,
                        recalled_resources,
                    )
                    if case.route is not None
                    else None
                ),
            )
            candidate_set = planner.plan(enrichment.constraints)
            if enrichment.geocoding_fact is not None:
                candidate_set = candidate_set.model_copy(
                    update={
                        "provider_facts": [
                            enrichment.geocoding_fact,
                            *candidate_set.provider_facts,
                        ]
                    }
                )
            if candidate_set.plans:
                actual = ExpectedOutcome.PLAN
                postconditions_passed, postcondition_details = _plan_postconditions_hold(
                    case,
                    candidate_set,
                    recalled_resources,
                    enrichment.constraints.date.value.weekday(),
                )
                passed = case.expected_outcome == actual and postconditions_passed
                details = f"plans={len(candidate_set.plans)}; {postcondition_details}"
            else:
                actual = ExpectedOutcome.CONFLICT
                conflict_code = candidate_set.conflict.code if candidate_set.conflict else None
                expected_fields = set(case.expected_conflict_fields)
                actual_fields = set(candidate_set.conflict.fields) if candidate_set.conflict else set()
                fields_match = expected_fields.issubset(actual_fields)
                passed = (
                    case.expected_outcome == actual
                    and conflict_code == case.expected_conflict_code
                    and fields_match
                )
                details = f"conflict_code={conflict_code}; conflict_fields={sorted(actual_fields)}"

        outcomes.append(
            EvalOutcome(
                case_id=case.case_id,
                passed=passed,
                actual_outcome=actual,
                details=details,
            )
        )

    hard_outcomes = [outcome for case, outcome in zip(cases, outcomes, strict=True) if "hard_constraint" in case.tags]
    hard_constraint_passed = sum(outcome.passed for outcome in hard_outcomes)
    hard_constraint_rate = (
        hard_constraint_passed / len(hard_outcomes)
        if hard_outcomes
        else 1.0
    )
    return EvalReport(
        total=len(outcomes),
        passed=sum(outcome.passed for outcome in outcomes),
        hard_constraint_total=len(hard_outcomes),
        hard_constraint_passed=hard_constraint_passed,
        hard_constraint_rate=hard_constraint_rate,
        outcomes=outcomes,
    )


def _plan_postconditions_hold(
    case: EvalCase,
    candidate_set: object,
    resources: dict[str, object],
    weekday_index: int,
) -> tuple[bool, str]:
    """Check every returned plan, so a bad fallback cannot hide behind one good plan."""
    expected = case.expected_plan_facts
    if expected is None:
        return True, "postconditions=not_requested"

    plans = getattr(candidate_set, "plans")
    failures: list[str] = []
    for plan in plans:
        legs = plan.route_legs
        if expected.return_to_origin is not None:
            actually_returns = bool(legs) and legs[-1].destination_name == "出发地"
            if actually_returns != expected.return_to_origin:
                failures.append(f"{plan.plan_id}:return_to_origin={actually_returns}")
        if expected.latest_return_time is not None:
            arrival = legs[-1].end if legs else None
            if (
                arrival is None
                or _clock_minutes(arrival) is None
                or _clock_minutes(expected.latest_return_time) is None
                or _clock_minutes(arrival) > _clock_minutes(expected.latest_return_time)
            ):
                failures.append(f"{plan.plan_id}:return_arrival={arrival}")
        if expected.max_total_distance_km is not None:
            total_distance = sum(leg.distance_km for leg in legs)
            if total_distance > expected.max_total_distance_km:
                failures.append(f"{plan.plan_id}:total_distance={total_distance:.2f}")
        if expected.max_route_leg_distance_km is not None:
            longest_leg = max((leg.distance_km for leg in legs), default=0.0)
            if longest_leg > expected.max_route_leg_distance_km:
                failures.append(f"{plan.plan_id}:longest_leg={longest_leg:.2f}")
        if expected.route_sources:
            actual_sources = {leg.source.value for leg in legs}
            if not actual_sources.issubset(set(expected.route_sources)):
                failures.append(f"{plan.plan_id}:route_sources={sorted(actual_sources)}")
        if expected.children_allowed:
            unsupported = [
                stop.resource_id
                for stop in plan.stops
                if (resource := resources.get(stop.resource_id)) is not None
                and resource.children_allowed is False
            ]
            if unsupported:
                failures.append(f"{plan.plan_id}:children_unsupported={unsupported}")
        if expected.no_weather_sensitive_activities:
            weather_sensitive = [
                stop.resource_id
                for stop in plan.stops
                if (resource := resources.get(stop.resource_id)) is not None
                and resource.resource_type.value == "activity"
                and resource.weather_sensitive
            ]
            if weather_sensitive:
                failures.append(f"{plan.plan_id}:weather_sensitive={weather_sensitive}")
        if expected.opening_hours_valid:
            weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[weekday_index]
            outside_hours = [
                stop.resource_id
                for stop in plan.stops
                if (resource := resources.get(stop.resource_id)) is not None
                and (hours := resource.open_hours.get(weekday))
                and visit_fits_opening_hours(hours, stop.start, stop.end) is False
            ]
            if outside_hours:
                failures.append(f"{plan.plan_id}:outside_opening={outside_hours}")

    if expected.weather_sources:
        actual_weather_sources = {
            fact.source.value
            for fact in getattr(candidate_set, "provider_facts")
            if getattr(fact, "kind", None) == "weather"
        }
        if not actual_weather_sources.issubset(set(expected.weather_sources)):
            failures.append(f"weather_sources={sorted(actual_weather_sources)}")
    if expected.provider_fact_kinds:
        actual_kinds = {getattr(fact, "kind", None) for fact in getattr(candidate_set, "provider_facts")}
        if not set(expected.provider_fact_kinds).issubset(actual_kinds):
            failures.append(f"provider_fact_kinds={sorted(str(kind) for kind in actual_kinds)}")
    warning_codes = {warning.code for warning in getattr(candidate_set, "warnings", [])}
    if not set(expected.required_warning_codes).issubset(warning_codes):
        failures.append(f"warning_codes={sorted(warning_codes)}")
    returned_ids = {
        stop.resource_id
        for plan in plans
        for stop in plan.stops
    }
    if set(expected.excluded_resource_ids) & returned_ids:
        failures.append(f"excluded_resources_present={sorted(set(expected.excluded_resource_ids) & returned_ids)}")
    if not set(expected.required_resource_ids).issubset(returned_ids):
        failures.append(f"required_resources_missing={sorted(set(expected.required_resource_ids) - returned_ids)}")
    return not failures, (
        "postconditions=passed" if not failures else "postconditions_failed=" + ", ".join(failures)
    )


def _clock_minutes(value: str) -> int | None:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError):
        return None
    if hour < 0 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


class _ScenarioWeatherProvider:
    """Small offline provider used only by declarative smoke cases."""

    def __init__(self, condition: str, is_adverse: bool) -> None:
        self._condition = condition
        self._is_adverse = is_adverse

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        now = datetime.now(timezone.utc)
        return WeatherFact(
            city=request.city,
            district=request.district,
            date=request.date,
            condition=self._condition,
            temperature_c=None,
            precipitation_mm=1.0 if self._is_adverse else 0.0,
            is_adverse=self._is_adverse,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=now,
            verified_at=now,
        )


class _ScenarioGeocodingProvider:
    def __init__(self, scenario) -> None:
        self._scenario = scenario

    def geocode(self, request: GeocodeRequest) -> GeocodingFact:
        now = datetime.now(timezone.utc)
        scenario = self._scenario
        if scenario.resolution.value == "resolved":
            return GeocodingFact(
                request=request,
                resolution=scenario.resolution,
                point=GeoPoint(latitude=scenario.latitude, longitude=scenario.longitude),
                city=scenario.city,
                district=scenario.district,
                address=scenario.address,
                adcode=scenario.adcode,
                source=ProviderSource.REPLAY,
                mode=ProviderMode.REPLAY,
                observed_at=now,
                verified_at=now,
                verified=True,
            )
        return GeocodingFact(
            request=request,
            resolution=scenario.resolution,
            source=ProviderSource.REPLAY,
            mode=ProviderMode.REPLAY,
            observed_at=now,
            verified_at=now,
            verified=False,
        )


class _ScenarioRouteProvider:
    def __init__(self, scenario, resources: dict[str, object]) -> None:
        self._scenario = scenario
        self._resource_by_point = {
            (round(resource.location.latitude, 6), round(resource.location.longitude, 6)): resource.resource_id
            for resource in resources.values()
        }

    def route(self, request) -> RouteFact:
        resource_id = self._resource_by_point.get(
            (round(request.destination.latitude, 6), round(request.destination.longitude, 6))
        )
        distance = self._scenario.distance_by_resource_id.get(
            resource_id or "",
            self._scenario.default_distance_km,
        )
        now = datetime.now(timezone.utc)
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            distance_km=distance,
            duration_minutes=self._scenario.duration_minutes,
            geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY,
            provider_mode=ProviderMode.REPLAY,
            verified_at=now,
        )

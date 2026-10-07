from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.constraints import (
    ActorContext,
    ConstraintSource,
    EventClockProposal,
    Intent,
    IdentityType,
    Interpretation,
    PlanRequest,
    RawConstraints,
    PeriodProposal,
    TripRangeProposal,
    TimeScope,
)
from app.domain.planning import PlanPace
from app.orchestration.entry_graph import default_environment_provider
from app.services.constraint_engine import (
    ConflictedRequest,
    ConstraintEngine,
    NeedsClarification,
    ResolvedRequest,
)
from app.services.demo_router import DemoRouter
from app.services.enrichment import EnrichmentService, EnvironmentContext
from app.services.request_patch_compiler import RequestPatchProposalCompiler
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from app.services.router_extractor import RouterContext


class RequestPatchCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext(
            user_id="test-user",
            session_id="time-compiler-test",
            identity_type=IdentityType.DEMO,
        )
        self.environment = EnvironmentContext(
            now=datetime(2026, 10, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=default_environment_provider(self.actor).default_location,
        )
        self.router = DemoRouter()
        self.enrichment = EnrichmentService()
        self.compiler = RequestPatchProposalCompiler()
        self.engine = ConstraintEngine()
        self.context = RouterContext(
            current_date=self.environment.now.date(),
            timezone="Asia/Shanghai",
        )

    def _compile(self, text: str):
        interpretation = self.router.interpret(text, self.context)
        enrichment = self.enrichment.enrich(
            interpretation,
            self.actor,
            self.environment,
        )
        compilation = self.compiler.compile(
            interpretation,
            enrichment.request_patch,
            self.environment,
        )
        outcome = self.engine.apply(
            PlanRequest(),
            compilation.patch,
            issues=compilation.issues,
        )
        return interpretation, compilation, outcome

    def test_weekday_without_time_uses_visible_default_window(self) -> None:
        _, compilation, outcome = self._compile("周六出去玩")
        self.assertIsInstance(outcome, ResolvedRequest)
        window = outcome.request.planning_window
        self.assertEqual(window.date.value.isoformat(), "2026-10-03")
        self.assertEqual(window.start_at.value, "14:00")
        self.assertEqual(window.end_at.value, "18:00")
        self.assertEqual(window.start_at.source, ConstraintSource.DEFAULT_RULE)
        self.assertTrue(any(item.field == "planning_window" for item in compilation.assumptions))

    def test_morning_activity_scopes_the_trip_window(self) -> None:
        interpretation, _, outcome = self._compile("明天早上出去玩")
        self.assertEqual(interpretation.time_proposals[0].event, "trip")
        self.assertIsInstance(outcome, ResolvedRequest)
        self.assertEqual(outcome.request.planning_window.start_at.value, "09:00")
        self.assertEqual(outcome.request.planning_window.end_at.value, "12:00")

    def test_morning_departure_is_a_clarification_not_a_trip_window(self) -> None:
        interpretation, compilation, outcome = self._compile("明天早上出发")
        self.assertEqual(interpretation.time_proposals[0].event, "departure")
        self.assertEqual(interpretation.time_proposals[0].period, TimeScope.MORNING)
        self.assertEqual(compilation.issues[0].field, "departure_at")
        self.assertIsInstance(outcome, NeedsClarification)

    def test_unresolved_date_is_not_silently_replaced_by_default(self) -> None:
        interpretation, compilation, outcome = self._compile("等忙完那天带家里人出去")
        self.assertEqual(interpretation.raw_constraints.date_text, "等忙完那天")
        self.assertEqual(compilation.issues[0].field, "date")
        self.assertIsInstance(outcome, NeedsClarification)
        self.assertNotIn("planning_window.date", compilation.patch.set_fields)

    def test_exact_morning_departure_sets_only_start_bound(self) -> None:
        interpretation, _, outcome = self._compile("明天早上九点出发")
        self.assertEqual(interpretation.time_proposals[0].event, "departure")
        self.assertIsInstance(outcome, ResolvedRequest)
        window = outcome.request.planning_window
        self.assertEqual(window.start_at.value, "09:00")
        self.assertEqual(window.end_at.value, "23:59")
        self.assertEqual(window.start_at.source, ConstraintSource.USER_EXPLICIT)

    def test_exact_return_deadline_sets_end_bound(self) -> None:
        _, _, outcome = self._compile("晚上八点前回来")
        self.assertIsInstance(outcome, ResolvedRequest)
        window = outcome.request.planning_window
        self.assertEqual(window.end_at.value, "20:00")
        self.assertTrue(window.explicit_return_deadline)

    def test_all_day_has_full_window_and_soft_objective(self) -> None:
        _, _, outcome = self._compile("周六玩一整天")
        self.assertIsInstance(outcome, ResolvedRequest)
        self.assertEqual(outcome.request.planning_window.start_at.value, "09:00")
        self.assertEqual(outcome.request.planning_window.end_at.value, "21:00")
        self.assertNotIn("充分利用全天", outcome.request.preferences)
        intent = RuleBasedPlanningIntentProvider().decide(outcome.request).intent
        self.assertEqual(intent.pace, PlanPace.FULL)

    def test_fuzzy_afternoon_window_reaches_an_explicit_dinner_role(self) -> None:
        _, compilation, outcome = self._compile("下午约会，安排活动和晚饭")
        self.assertIsInstance(outcome, ResolvedRequest)
        window = outcome.request.planning_window
        self.assertEqual(window.start_at.value, "14:00")
        self.assertEqual(window.end_at.value, "21:00")
        self.assertEqual(
            window.end_at.rule_id,
            "time.trip.afternoon.dinner_horizon.v1",
        )
        self.assertTrue(
            any(item.rule_id == "time.trip.afternoon.dinner_horizon.v1" for item in compilation.assumptions)
        )

    def test_exact_afternoon_range_is_not_widened_for_dinner(self) -> None:
        interpretation, _, _ = self._compile("周六出去玩")
        interpretation = interpretation.model_copy(
            update={
                "time_proposals": (
                    TripRangeProposal(
                        start="14:00",
                        end="18:00",
                        evidence="下午两点到六点",
                    ),
                ),
                "raw_constraints": interpretation.raw_constraints.model_copy(
                    update={"required_stop_roles": ("activity", "dinner")}
                ),
            }
        )
        enrichment = self.enrichment.enrich(
            interpretation,
            self.actor,
            self.environment,
        )
        compilation = self.compiler.compile(
            interpretation,
            enrichment.request_patch,
            self.environment,
        )
        outcome = self.engine.apply(PlanRequest(), compilation.patch)

        self.assertIsInstance(outcome, ResolvedRequest)
        self.assertEqual(outcome.request.planning_window.start_at.value, "14:00")
        self.assertEqual(outcome.request.planning_window.end_at.value, "18:00")

    def test_return_period_is_asked_before_departure_period(self) -> None:
        _, compilation, outcome = self._compile("下午出发，晚饭前后一定回来")
        self.assertEqual(compilation.issues[0].field, "return_by")
        self.assertIsInstance(outcome, NeedsClarification)

    def test_unresolved_explicit_fields_become_compiler_issues(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                origin_text="我公司附近",
                max_distance_text="别跑得太远",
            ),
        )
        enrichment = self.enrichment.enrich(
            interpretation,
            self.actor,
            self.environment,
        )
        compilation = self.compiler.compile(
            interpretation,
            enrichment.request_patch,
            self.environment,
        )
        self.assertEqual(
            [issue.field for issue in compilation.issues],
            ["location", "max_distance_km"],
        )

    def test_weather_condition_routing_is_a_compiled_fact(self) -> None:
        conditioned = Interpretation(
            primary_intent=Intent.CHECK_WEATHER,
            intent_scores={Intent.CHECK_WEATHER: 1.0},
            raw_constraints=RawConstraints(scene_tags=["indoor"]),
        )
        plain_weather = Interpretation(
            primary_intent=Intent.CHECK_WEATHER,
            intent_scores={Intent.CHECK_WEATHER: 1.0},
        )
        for interpretation, expected in ((conditioned, True), (plain_weather, False)):
            enrichment = self.enrichment.enrich(
                interpretation,
                self.actor,
                self.environment,
            )
            compilation = self.compiler.compile(
                interpretation,
                enrichment.request_patch,
                self.environment,
            )
            self.assertEqual(compilation.condition_requests_plan, expected)

    def test_departure_after_return_is_a_conflict(self) -> None:
        _, _, outcome = self._compile("晚上九点出发，晚上八点前回家")
        self.assertIsInstance(outcome, ConflictedRequest)
        self.assertEqual(outcome.conflict.code, "DEPARTURE_NOT_BEFORE_RETURN_BY")

    def test_departure_outside_explicit_trip_range_is_rejected_before_planning(self) -> None:
        interpretation = self.router.interpret("周六出去玩", self.context)
        enrichment = self.enrichment.enrich(
            interpretation,
            self.actor,
            self.environment,
        )
        interpretation = interpretation.model_copy(
            update={
                "time_proposals": (
                    TripRangeProposal(
                        start="14:00",
                        end="18:00",
                        evidence="下午两点到六点",
                    ),
                    EventClockProposal(
                        event="departure",
                        clock="13:30",
                        evidence="一点半出发",
                    ),
                )
            }
        )

        compilation = self.compiler.compile(
            interpretation,
            enrichment.request_patch,
            self.environment,
        )
        outcome = self.engine.apply(
            PlanRequest(),
            compilation.patch,
            issues=compilation.issues,
        )

        self.assertIsInstance(outcome, NeedsClarification)
        self.assertEqual(compilation.issues[0].code, "DEPARTURE_OUTSIDE_EXPLICIT_WINDOW")


if __name__ == "__main__":
    unittest.main()

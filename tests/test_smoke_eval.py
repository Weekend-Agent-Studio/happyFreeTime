import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.constraints import GeoLocation
from app.domain.evaluation import EvalCase, ExpectedOutcome
from app.evaluation.smoke import load_cases, run_smoke_cases
from app.services.enrichment import EnvironmentContext


class SmokeEvalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

    def test_all_offline_smoke_cases_pass(self) -> None:
        cases = load_cases(Path("evals/smoke_cases.json"))
        report = run_smoke_cases(cases, self.environment)

        self.assertEqual(report.total, len(cases))
        self.assertEqual(report.passed, report.total, report.model_dump())
        self.assertEqual(report.hard_constraint_passed, report.hard_constraint_total)

    def test_conflict_field_is_part_of_hard_constraint_oracle(self) -> None:
        case = EvalCase.model_validate({
            "case_id": "tiny-total-distance",
            "intent": "plan_outing",
            "raw_constraints": {
                "total_distance_text": "全程不超过0.1公里",
                "total_distance_km": 0.1,
            },
            "expected_outcome": ExpectedOutcome.CONFLICT,
            "expected_conflict_code": "NO_FEASIBLE_PLAN",
            "expected_conflict_fields": ["total_distance_km"],
            "tags": ["hard_constraint"],
        })

        report = run_smoke_cases([case], self.environment)

        self.assertEqual(report.passed, 1, report.model_dump())
        self.assertEqual(report.hard_constraint_total, 1)
        self.assertEqual(report.hard_constraint_passed, 1)

    def test_plan_outcome_checks_observable_return_and_provider_postconditions(self) -> None:
        case = EvalCase.model_validate({
            "case_id": "return-postconditions",
            "intent": "plan_outing",
            "raw_constraints": {"return_by_text": "最晚23:00到家"},
            "expected_outcome": ExpectedOutcome.PLAN,
            "expected_plan_facts": {
                "return_to_origin": True,
                "latest_return_time": "23:00",
                "max_total_distance_km": 30,
                "route_sources": ["local_estimate"],
                "weather_sources": ["mock"],
            },
            "tags": ["hard_constraint"],
        })

        report = run_smoke_cases([case], self.environment)

        self.assertEqual(report.passed, 1, report.model_dump())
        self.assertEqual(report.hard_constraint_rate, 1.0)

    def test_hard_constraint_rate_is_not_satisfied_when_a_postcondition_fails(self) -> None:
        case = EvalCase.model_validate({
            "case_id": "broken-return-postcondition",
            "intent": "plan_outing",
            "raw_constraints": {"return_by_text": "最晚23:00到家"},
            "expected_outcome": ExpectedOutcome.PLAN,
            "expected_plan_facts": {"return_to_origin": False},
            "tags": ["hard_constraint"],
        })

        report = run_smoke_cases([case], self.environment)

        self.assertEqual(report.hard_constraint_total, 1)
        self.assertEqual(report.hard_constraint_passed, 0)
        self.assertEqual(report.hard_constraint_rate, 0.0)

    def test_weather_and_child_plan_postconditions_use_the_recalled_resource_facts(self) -> None:
        cases = [
            EvalCase.model_validate({
                "case_id": "rainy-child",
                "intent": "plan_outing",
                "raw_constraints": {"children": 1, "child_age": 6},
                "weather": {"condition": "中雨", "is_adverse": True},
                "expected_outcome": ExpectedOutcome.PLAN,
                "expected_plan_facts": {
                    "children_allowed": True,
                    "no_weather_sensitive_activities": True,
                    "weather_sources": ["replay"],
                },
                "tags": ["hard_constraint"],
            }),
            EvalCase.model_validate({
                "case_id": "night-opening-boundary",
                "intent": "plan_outing",
                "raw_constraints": {"time_text": "晚上"},
                "expected_outcome": ExpectedOutcome.PLAN,
                "expected_plan_facts": {"opening_hours_valid": True},
                "tags": ["hard_constraint"],
            }),
        ]

        report = run_smoke_cases(cases, self.environment)

        self.assertEqual(report.passed, 2, report.model_dump())


if __name__ == "__main__":
    unittest.main()

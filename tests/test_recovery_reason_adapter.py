from datetime import date
import unittest

from app.domain.catalog import ConstraintViolation, ResourceType, ViolationCode
from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    PlanRequest,
    PlanningWindow,
)
from app.domain.planning import ConstraintConflict, SkeletonSearchTrace
from app.domain.recovery import RecoveryKind, RecoveryStage
from app.services.recovery_reason_adapter import RecoveryReasonAdapter


def _request() -> PlanRequest:
    return PlanRequest(
        revision=7,
        planning_window=PlanningWindow(
            date=ConstraintValue[date](
                value=date(2026, 10, 10), source=ConstraintSource.USER_EXPLICIT
            ),
            start_at=ConstraintValue[str](
                value="09:00", source=ConstraintSource.DEFAULT_RULE
            ),
            end_at=ConstraintValue[str](
                value="15:00", source=ConstraintSource.USER_EXPLICIT
            ),
        ),
        max_distance_km=ConstraintValue[float](
            value=8.0, source=ConstraintSource.DEFAULT_RULE
        ),
    )


class RecoveryReasonAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = RecoveryReasonAdapter()

    def test_preserves_real_search_and_catalog_diagnostics(self) -> None:
        reason = self.adapter.from_conflict(
            ConstraintConflict(
                code="NO_FEASIBLE_PLAN",
                message="当前没有方案。",
                fields=["opening_hours", "time_window"],
            ),
            _request(),
            search_traces=(
                SkeletonSearchTrace(
                    skeleton_id="activity-meal",
                    theoretical_combinations=18,
                    expansions=7,
                    finalists=2,
                    route_candidates=2,
                    route_provider_requests=4,
                    rejected_by={"time_window": 3},
                ),
            ),
            catalog_violations=(
                ConstraintViolation(
                    resource_id="poi-1",
                    resource_name="闭店地点",
                    resource_type=ResourceType.ACTIVITY,
                    code=ViolationCode.OUTSIDE_BASIC_OPENING_HOURS,
                    field="opening_hours",
                    message="outside hours",
                ),
            ),
            route_failure_field_sets=(frozenset({"availability"}),),
            plan_version_id="pv-3",
        )
        self.assertEqual(reason.kind, RecoveryKind.NO_FEASIBLE_PLAN)
        self.assertEqual(reason.stage, RecoveryStage.AVAILABILITY)
        self.assertEqual(reason.fields, ("opening_hours", "time_window"))
        self.assertEqual(reason.request_revision, 7)
        self.assertEqual(reason.plan_version_id, "pv-3")
        self.assertEqual(reason.diagnostics.available_minutes, 360)
        self.assertEqual(reason.diagnostics.structures_considered, 1)
        self.assertEqual(reason.diagnostics.combinations_expanded, 7)
        self.assertEqual(reason.diagnostics.route_provider_requests, 4)
        self.assertEqual(reason.diagnostics.rejected_by_time_window, 3)
        self.assertEqual(reason.diagnostics.rejected_by_availability, 1)

    def test_strict_budget_is_attributed_to_budget_not_generic_route(self) -> None:
        reason = self.adapter.from_conflict(
            ConstraintConflict(
                code="NO_PLAN_WITHIN_STRICT_BUDGET",
                message="严格预算内无可行方案。",
                fields=["budget_per_person"],
            ),
            _request(),
        )
        self.assertEqual(reason.stage, RecoveryStage.RETRIEVAL)
        self.assertEqual(reason.fields, ("budget_per_person",))

    def test_cross_field_time_order_is_classified_as_hard_conflict(self) -> None:
        reason = self.adapter.from_conflict(
            ConstraintConflict(
                code="INVALID_TIME_WINDOW_ORDER",
                message="行程开始时间必须早于结束时间。",
                fields=["planning_window.start_at", "planning_window.end_at"],
            ),
            _request(),
        )
        self.assertEqual(reason.kind, RecoveryKind.HARD_CONFLICT)
        self.assertEqual(reason.stage, RecoveryStage.CONSTRAINT_COMPILATION)

    def test_empty_findings_remain_empty_and_do_not_invent_causes(self) -> None:
        reason = self.adapter.from_conflict(
            ConstraintConflict(
                code="NO_FEASIBLE_PLAN", message="暂无可行方案。", fields=[]
            ),
            _request(),
        )
        self.assertEqual(reason.fields, ())
        self.assertIsNone(reason.diagnostics.nearest_candidate_distance_km)
        self.assertIsNone(reason.diagnostics.optimistic_duration_minutes)

    def test_modification_failure_is_bound_to_the_selected_plan_version(self) -> None:
        reason = self.adapter.from_conflict(
            ConstraintConflict(
                code="NO_REPLACEMENT_PLAN",
                message="替换候选没有通过整套校验。",
                fields=["replacement", "time_window"],
            ),
            _request(),
            stage=RecoveryStage.MODIFICATION,
            plan_version_id="pv-selected",
        )
        self.assertEqual(reason.kind, RecoveryKind.MODIFICATION_FAILED)
        self.assertEqual(reason.stage, RecoveryStage.MODIFICATION)
        self.assertEqual(reason.plan_version_id, "pv-selected")
        self.assertEqual(reason.request_revision, 7)

    def test_rejects_unstructured_field_text(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.from_conflict(
                ConstraintConflict(
                    code="NO_FEASIBLE_PLAN",
                    message="暂无可行方案。",
                    fields=["route distance failed: secret provider payload"],
                ),
                _request(),
            )


if __name__ == "__main__":
    unittest.main()

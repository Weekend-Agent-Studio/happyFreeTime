from __future__ import annotations

import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, PlanRequest, PlanningWindow
from app.domain.recovery import (
    ApplyRequestPatchAction,
    OpenConstraintEditorAction,
    RecoveryDiagnostics,
    RecoveryAttempt,
    RecoveryContext,
    RecoveryKind,
    RecoveryReason,
    RecoveryStage,
    PrimaryBottleneckKind,
    RequestFieldAction,
    StartNewRequestAction,
    CancelTurnAction,
    KeepCurrentPlanAction,
)
from app.services.recovery_policy import RecoveryPolicy
from app.services.recovery_bottleneck import resolve_primary_bottleneck


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RecoveryPolicy()

    def reason(
        self,
        *,
        code: str = "NO_PLAN_AFTER_ROUTE_VERIFICATION",
        revision: int = 3,
        source: ConstraintSource | None = None,
    ) -> RecoveryReason:
        sources = {"max_distance_km": source} if source is not None else {}
        return RecoveryReason(
            code=code,
            kind=RecoveryKind.NO_FEASIBLE_PLAN,
            stage=RecoveryStage.ROUTE,
            fields=("max_distance_km",),
            request_revision=revision,
            public_summary="当前条件下暂未找到可行方案。",
            diagnostics=RecoveryDiagnostics(
                current_max_distance_km=8.0,
                nearest_candidate_distance_km=10.6,
                constraint_sources=sources,
            ),
        )

    def test_default_distance_is_the_only_auto_relaxable_case(self) -> None:
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="distance.default.beijing.v1",
            ),
        )

        decision = self.policy.decide(
            self.reason(source=ConstraintSource.DEFAULT_RULE), request
        )

        self.assertIsNotNone(decision.auto_action)
        assert decision.auto_action is not None
        self.assertEqual(
            decision.auto_action.patch.set_fields["max_distance_km"], 11.0
        )
        self.assertEqual(
            decision.auto_action.patch.field_sources["max_distance_km"],
            ConstraintSource.DERIVED,
        )

    def test_explicit_distance_requires_user_confirmation(self) -> None:
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0, source=ConstraintSource.USER_EXPLICIT, raw_text="8 公里内"
            ),
        )

        decision = self.policy.decide(
            self.reason(source=ConstraintSource.USER_EXPLICIT), request
        )

        self.assertIsNone(decision.auto_action)
        action = next(
            item
            for item in decision.actions
            if isinstance(item, ApplyRequestPatchAction)
        )
        self.assertEqual(action.patch.set_fields["max_distance_km"], 11.0)
        self.assertEqual(
            action.patch.field_sources["max_distance_km"],
            ConstraintSource.SESSION_CONFIRMED,
        )

    def test_inferred_user_distance_is_not_silently_relaxed(self) -> None:
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0,
                source=ConstraintSource.USER_INFERRED,
                raw_text="别太远",
            ),
        )

        decision = self.policy.decide(
            self.reason(source=ConstraintSource.USER_INFERRED), request
        )

        self.assertIsNone(decision.auto_action)

    def test_disagreeing_failure_provenance_disables_auto_relaxation(self) -> None:
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0, source=ConstraintSource.DEFAULT_RULE
            ),
        )

        decision = self.policy.decide(
            self.reason(source=ConstraintSource.USER_EXPLICIT), request
        )

        self.assertIsNone(decision.auto_action)

    def test_stale_request_revision_has_no_executable_actions(self) -> None:
        decision = self.policy.decide(self.reason(revision=2), PlanRequest(revision=3))

        self.assertTrue(decision.stale)
        self.assertEqual(decision.actions, ())
        self.assertIsNone(decision.auto_action)

    def test_unknown_failure_offers_safe_editor_without_invented_field_choices(self) -> None:
        reason = self.reason(code="FUTURE_FAILURE").model_copy(
            update={"stage": RecoveryStage.VERIFICATION, "fields": ()}
        )
        decision = self.policy.decide(reason, PlanRequest(revision=3))

        self.assertIsNone(decision.auto_action)
        self.assertFalse(
            any(isinstance(item, ApplyRequestPatchAction) for item in decision.actions)
        )
        self.assertFalse(any(isinstance(item, RequestFieldAction) for item in decision.actions))
        self.assertTrue(
            any(isinstance(item, OpenConstraintEditorAction) for item in decision.actions)
        )

    def test_failure_without_editable_fields_offers_manual_condition_editor(self) -> None:
        reason = self.reason(code="NO_CANDIDATES_FOR_PLAN_STRUCTURE").model_copy(
            update={"fields": ()}
        )
        decision = self.policy.decide(reason, PlanRequest(revision=3))

        editor = next(
            item
            for item in decision.actions
            if isinstance(item, OpenConstraintEditorAction)
        )
        self.assertEqual(editor.label, "手动调整条件")
        self.assertEqual(editor.continuation, "finish")
        self.assertIsNone(editor.plan_version_id)

    def test_start_new_request_action_is_bound_to_current_plan_version(self) -> None:
        reason = self.reason().model_copy(update={"plan_version_id": "plan-v7"})
        decision = self.policy.decide(
            reason,
            PlanRequest(revision=3),
            current_plan_version_id="plan-v7",
        )

        action = next(
            item for item in decision.actions if isinstance(item, StartNewRequestAction)
        )
        self.assertEqual(action.plan_version_id, "plan-v7")

    def test_unrecognized_hard_conflict_still_offers_manual_editor(self) -> None:
        reason = self.reason(code="FUTURE_HARD_CONFLICT").model_copy(
            update={
                "kind": RecoveryKind.HARD_CONFLICT,
                "fields": ("future_constraint",),
            }
        )

        decision = self.policy.decide(reason, PlanRequest(revision=3))

        self.assertTrue(
            any(isinstance(item, OpenConstraintEditorAction) for item in decision.actions)
        )

    def test_policy_output_is_deterministic_and_action_label_is_display_only(self) -> None:
        reason = self.reason(source=ConstraintSource.DEFAULT_RULE)
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0, source=ConstraintSource.DEFAULT_RULE
            ),
        )

        first = self.policy.decide(reason, request)
        second = self.policy.decide(reason, request)

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        action = next(
            item for item in first.actions if isinstance(item, ApplyRequestPatchAction)
        )
        relabeled = action.model_copy(update={"label": "显示文字已改变"})
        self.assertEqual(action.patch, relabeled.patch)
        self.assertNotEqual(action.label, relabeled.label)

    def test_policy_bounds_suggestions_but_keeps_safety_exits(self) -> None:
        reason = self.reason().model_copy(
            update={
                "fields": (
                    "max_distance_km",
                    "total_distance_km",
                    "return_by",
                    "budget_per_person",
                    "exact_stop_count",
                ),
                "kind": RecoveryKind.MODIFICATION_FAILED,
                "plan_version_id": "plan-v3",
            }
        )
        decision = self.policy.decide(
            reason,
            PlanRequest(revision=3),
            current_plan_version_id="plan-v3",
        )
        suggestions = [
            item
            for item in decision.actions
            if not isinstance(
                item,
                (
                    OpenConstraintEditorAction,
                    KeepCurrentPlanAction,
                    CancelTurnAction,
                    StartNewRequestAction,
                ),
            )
        ]
        self.assertLessEqual(len(suggestions), 3)
        self.assertTrue(any(isinstance(item, KeepCurrentPlanAction) for item in decision.actions))
        self.assertTrue(any(isinstance(item, CancelTurnAction) for item in decision.actions))
        self.assertEqual(
            len({item.action_id for item in decision.actions}), len(decision.actions)
        )

    def test_schedule_failure_prioritizes_time_or_stop_count_over_distance(self) -> None:
        request = PlanRequest(
            revision=3,
            planning_window=PlanningWindow(
                start_at=ConstraintValue(value="14:00", source=ConstraintSource.DEFAULT_RULE),
                end_at=ConstraintValue(value="18:00", source=ConstraintSource.DEFAULT_RULE),
            ),
            exact_stop_count=ConstraintValue(
                value=4, source=ConstraintSource.USER_EXPLICIT, raw_text="四站"
            ),
            max_distance_km=ConstraintValue(
                value=5.0, source=ConstraintSource.USER_EXPLICIT, raw_text="5公里内"
            ),
        )
        reason = RecoveryReason(
            code="NO_SCHEDULE_WITHIN_TIME_WINDOW",
            kind=RecoveryKind.NO_FEASIBLE_PLAN,
            stage=RecoveryStage.SCHEDULING,
            fields=("duration_minutes", "max_distance_km"),
            request_revision=3,
            public_summary="四站行程在当前时间内无法排程。",
            diagnostics=RecoveryDiagnostics(
                current_max_distance_km=5.0,
                requested_stop_count=4,
                available_minutes=240,
                optimistic_duration_minutes=330,
                local_schedule_failures=174,
                local_schedule_passes=0,
                rejected_by_route=2,
            ),
        )

        decision = self.policy.decide(reason, request)
        patches = [
            item
            for item in decision.actions
            if isinstance(item, ApplyRequestPatchAction)
        ]
        self.assertTrue(any("planning_window.end_at" in item.patch.set_fields for item in patches))
        self.assertTrue(
            any(
                item.patch.set_fields.get("planning_window.end_at").value == "19:30"
                for item in patches
                if "planning_window.end_at" in item.patch.set_fields
            )
        )
        self.assertTrue(any(item.patch.set_fields.get("exact_stop_count") == 3 for item in patches))
        self.assertFalse(any("max_distance_km" in item.patch.set_fields for item in patches))
        self.assertTrue(all(item.action_fingerprint for item in decision.actions))

    def test_recovery_actions_expose_concrete_and_strategy_fingerprints(self) -> None:
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0, source=ConstraintSource.USER_EXPLICIT
            ),
        )
        decision = self.policy.decide(self.reason(source=ConstraintSource.USER_EXPLICIT), request)
        distance = next(
            item
            for item in decision.actions
            if isinstance(item, ApplyRequestPatchAction)
            and "max_distance_km" in item.patch.set_fields
        )
        self.assertTrue(distance.action_fingerprint)
        self.assertEqual(distance.strategy_fingerprint, "expand:max_distance")
        self.assertNotEqual(distance.action_fingerprint, distance.strategy_fingerprint)

    def test_ineffective_strategy_is_filtered_before_action_budget(self) -> None:
        reason = self.reason(source=ConstraintSource.USER_EXPLICIT)
        request = PlanRequest(
            revision=3,
            max_distance_km=ConstraintValue[float](
                value=8.0, source=ConstraintSource.USER_EXPLICIT
            ),
        )
        primary = resolve_primary_bottleneck(reason)
        attempt = RecoveryAttempt(
            action_kind="apply_request_patch",
            strategy_fingerprint="expand:max_distance",
            concrete_fingerprint="apply_request_patch:max_distance_km:max_distance_km=11.0",
            target_fields=("max_distance_km",),
            before_bottleneck=primary.kind,
            after_bottleneck=primary.kind,
            improved=False,
            outcome="still_failed",
        )
        decision = self.policy.decide(
            reason,
            request,
            context=RecoveryContext(
                reason=reason,
                primary_bottleneck=primary,
                request=request,
                previous_attempts=(attempt,),
            ),
        )
        self.assertFalse(
            any(
                isinstance(item, ApplyRequestPatchAction)
                and "max_distance_km" in item.patch.set_fields
                for item in decision.actions
            )
        )
        self.assertTrue(
            any(item.kind == "start_new_request" for item in decision.actions)
        )

    def test_safety_exits_survive_used_action_history(self) -> None:
        reason = self.reason(source=ConstraintSource.USER_EXPLICIT)
        decision = self.policy.decide(
            reason,
            PlanRequest(revision=3),
            context=RecoveryContext(
                reason=reason,
                primary_bottleneck=resolve_primary_bottleneck(reason),
                request=PlanRequest(revision=3),
                used_concrete_fingerprints=(
                    "open_constraint_editor",
                    "keep_current_plan",
                    "cancel_turn",
                    "start_new_request",
                ),
            ),
        )
        self.assertTrue(any(item.kind == "open_constraint_editor" for item in decision.actions))
        self.assertTrue(any(item.kind == "cancel_turn" for item in decision.actions))
        self.assertTrue(any(item.kind == "start_new_request" for item in decision.actions))


if __name__ == "__main__":
    unittest.main()

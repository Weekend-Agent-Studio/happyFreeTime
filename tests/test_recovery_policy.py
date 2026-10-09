from __future__ import annotations

import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, PlanRequest
from app.domain.recovery import (
    ApplyRequestPatchAction,
    RecoveryDiagnostics,
    RecoveryKind,
    RecoveryReason,
    RecoveryStage,
    RequestFieldAction,
)
from app.services.recovery_policy import RecoveryPolicy


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

    def test_unknown_failure_has_generic_choices_without_invented_numbers(self) -> None:
        reason = self.reason(code="FUTURE_FAILURE")
        decision = self.policy.decide(reason, PlanRequest(revision=3))

        self.assertIsNone(decision.auto_action)
        self.assertFalse(
            any(isinstance(item, ApplyRequestPatchAction) for item in decision.actions)
        )
        self.assertTrue(
            any(
                isinstance(item, RequestFieldAction)
                and item.field == "max_distance_km"
                for item in decision.actions
            )
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


if __name__ == "__main__":
    unittest.main()

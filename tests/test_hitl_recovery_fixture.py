from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.domain.constraints import PlanRequest
from app.domain.recovery import ApplyRequestPatchAction, RecoveryReason
from app.services.recovery_policy import RecoveryPolicy


class HitlRecoveryFixtureTests(unittest.TestCase):
    def test_reviewed_recovery_contract_cases(self) -> None:
        fixture_path = Path(__file__).parents[1] / "evals" / "hitl_recovery_v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(fixture["suite"], "hitl_recovery_policy_contract_v1")
        self.assertEqual(fixture["review_status"], "reviewed deterministic recovery-contract cases")

        policy = RecoveryPolicy()
        for case in fixture["cases"]:
            with self.subTest(case_id=case["case_id"]):
                request = PlanRequest.model_validate(case["request"])
                reason = RecoveryReason.model_validate(case["reason"])
                decision = policy.decide(
                    reason,
                    request,
                    current_plan_version_id=case.get("current_plan_version_id"),
                )
                expected = case["expected"]
                action_kinds = {action.kind for action in decision.actions}
                self.assertEqual(decision.stale, expected.get("stale", False))
                self.assertEqual(decision.auto_action is not None, expected["auto_action"])
                self.assertEqual(len(decision.actions), expected.get("action_count", len(decision.actions)))
                self.assertTrue(set(expected.get("required_action_kinds", ())).issubset(action_kinds))
                self.assertFalse(set(expected.get("forbidden_action_kinds", ())) & action_kinds)

                action_ids = {action.action_id for action in decision.actions}
                self.assertTrue(set(expected.get("required_action_ids", ())).issubset(action_ids))
                patch_values = {
                    key: value
                    for action in decision.actions
                    if isinstance(action, ApplyRequestPatchAction)
                    for key, value in action.patch.set_fields.items()
                }
                for key, value in expected.get("required_patch", {}).items():
                    self.assertEqual(patch_values.get(key), value)
                if "distance_patch" in expected:
                    distance_action = next(
                        action
                        for action in decision.actions
                        if isinstance(action, ApplyRequestPatchAction)
                        and "max_distance_km" in action.patch.set_fields
                    )
                    self.assertEqual(
                        distance_action.patch.set_fields["max_distance_km"],
                        expected["distance_patch"],
                    )
                    if "distance_patch_source" in expected:
                        self.assertEqual(
                            distance_action.patch.field_sources["max_distance_km"].value,
                            expected["distance_patch_source"],
                        )
                if "availability_choices" in expected:
                    availability_action = next(
                        action
                        for action in decision.actions
                        if getattr(action, "field", None) == "availability"
                    )
                    self.assertEqual(
                        list(availability_action.choices),
                        expected["availability_choices"],
                    )


if __name__ == "__main__":
    unittest.main()

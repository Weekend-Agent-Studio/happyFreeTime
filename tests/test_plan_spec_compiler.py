import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, StopRole
from app.domain.planning import PlanStructureProposal
from app.services.plan_spec_compiler import PlanSpecCompiler
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from tests.test_planning import planning_constraints


class PlanSpecCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["纪念日", "适合聊天"]}
        )
        self.baseline = RuleBasedPlanningIntentProvider().decide(
            self.constraints
        ).intent

    def test_unregistered_order_is_compiled_without_registry_match(self) -> None:
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v2",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "break", "inclusion": "core"},
                {"role": "dinner", "inclusion": "core"},
            ],
            pace="relaxed",
        )
        choices = PlanSpecCompiler().compile(
            self.constraints, self.baseline, proposal
        )
        self.assertEqual(choices.proposal_status, "compiled")
        self.assertEqual(
            choices.preferred_specs[0].roles,
            (StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER),
        )

    def test_repeated_activity_is_preserved(self) -> None:
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v2",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "lunch", "inclusion": "core"},
                {"role": "activity", "inclusion": "core"},
                {"role": "dinner", "inclusion": "core"},
            ]
        )
        choices = PlanSpecCompiler().compile(
            self.constraints, self.baseline, proposal
        )
        self.assertEqual(choices.proposal_status, "compiled")
        self.assertEqual(
            choices.preferred_specs[0].roles,
            (
                StopRole.ACTIVITY,
                StopRole.LUNCH,
                StopRole.ACTIVITY,
                StopRole.DINNER,
            ),
        )

    def test_optional_slot_expands_finite_variants(self) -> None:
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v2",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "break", "inclusion": "optional"},
                {"role": "dinner", "inclusion": "core"},
            ]
        )
        choices = PlanSpecCompiler().compile(
            self.constraints, self.baseline, proposal
        )
        self.assertEqual(
            {spec.roles for spec in choices.preferred_specs},
            {
                (StopRole.ACTIVITY, StopRole.DINNER),
                (StopRole.ACTIVITY, StopRole.BREAK, StopRole.DINNER),
            },
        )

    def test_explicit_roles_and_count_are_not_silently_removed(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=3, source=ConstraintSource.USER_EXPLICIT
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v2",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "dinner", "inclusion": "core"},
            ]
        )
        choices = PlanSpecCompiler().compile(
            constraints,
            RuleBasedPlanningIntentProvider().decide(constraints).intent,
            proposal,
        )
        self.assertEqual(choices.proposal_status, "rejected")
        self.assertEqual(choices.diagnostic_code, "explicit_roles_not_preserved")
        self.assertFalse(choices.preferred_specs)

    def test_invalid_meal_order_is_rejected_with_rule_fallback(self) -> None:
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v2",
            slots=[
                {"role": "dinner", "inclusion": "core"},
                {"role": "lunch", "inclusion": "core"},
            ]
        )
        choices = PlanSpecCompiler().compile(
            self.constraints, self.baseline, proposal
        )
        self.assertEqual(choices.proposal_status, "rejected")
        self.assertEqual(choices.diagnostic_code, "lunch_before_dinner_required")
        self.assertTrue(choices.fallback_specs)


if __name__ == "__main__":
    unittest.main()

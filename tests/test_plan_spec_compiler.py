import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, StopRole
from app.domain.planning import PlanStructureProposal
from app.domain.semantics import SoftObjective
from app.services.plan_spec_compiler import PlanSpecCompiler
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from tests.test_planning import planning_constraints, with_planning_window


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
            schema_version="plan-structure-proposal.v3",
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
            schema_version="plan-structure-proposal.v3",
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

    def test_required_activity_is_a_lower_bound_for_model_structure(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY,),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="去玩",
                )
            }
        )
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "lunch", "inclusion": "optional"},
                {"role": "activity", "inclusion": "core"},
                {"role": "dinner", "inclusion": "core"},
            ],
        )

        compiler = PlanSpecCompiler()
        self.assertIsNone(
            compiler.compile_explicit_structure(constraints, self.baseline)
        )
        choices = compiler.compile(constraints, self.baseline, proposal)

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

    def test_required_lunch_keeps_model_activity_slots(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.LUNCH,),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="要吃午饭",
                )
            }
        )
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "lunch", "inclusion": "core"},
                {"role": "activity", "inclusion": "core"},
                {"role": "dinner", "inclusion": "optional"},
            ],
        )

        choices = PlanSpecCompiler().compile(constraints, self.baseline, proposal)

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

    def test_minimal_model_structure_is_rejected_for_partial_requirements(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY,),
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[{"role": "activity", "inclusion": "core"}],
        )

        choices = PlanSpecCompiler().compile(constraints, self.baseline, proposal)

        self.assertEqual(choices.proposal_status, "rejected")
        self.assertEqual(choices.diagnostic_code, "structure_under_specified")
        self.assertFalse(choices.preferred_specs)
        self.assertTrue(choices.fallback_specs)
        self.assertTrue(all(len(spec.roles) >= 2 for spec in choices.fallback_specs))

    def test_exact_count_three_allows_model_to_complete_required_activity(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=3,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="三站",
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY,),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "lunch", "inclusion": "core"},
                {"role": "dinner", "inclusion": "optional"},
            ],
        )

        choices = PlanSpecCompiler().compile(constraints, self.baseline, proposal)

        self.assertEqual(choices.proposal_status, "compiled")
        self.assertTrue(all(len(spec.roles) == 3 for spec in choices.preferred_specs))

    def test_optional_slot_expands_finite_variants(self) -> None:
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
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
        self.assertTrue(
            all(not hasattr(spec, "optional_roles") for spec in choices.preferred_specs)
        )

    def test_explicit_roles_and_count_override_model_structure(self) -> None:
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
            schema_version="plan-structure-proposal.v3",
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
        self.assertTrue(choices.explicit_structure)
        self.assertEqual(choices.proposal_status, "not_used")
        self.assertIsNone(choices.conflict)
        self.assertEqual(
            [spec.roles for spec in choices.preferred_specs],
            [(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER)],
        )
        self.assertFalse(choices.fallback_specs)

    def test_invalid_meal_order_is_rejected_with_rule_fallback(self) -> None:
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
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

    def test_partial_count_is_completed_without_registry_match(self) -> None:
        constraints = with_planning_window(
            planning_constraints(time_end="22:00"),
            start="14:00",
            end="22:00",
        ).model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=3,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="三站",
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.DINNER,),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="要吃晚饭",
                ),
            }
        )

        compiler = PlanSpecCompiler()
        self.assertIsNone(
            compiler.compile_explicit_structure(constraints, self.baseline)
        )
        choices = compiler.compile(constraints, self.baseline, None)
        self.assertEqual(choices.proposal_status, "not_used")
        self.assertTrue(choices.preferred_specs)
        self.assertTrue(all(len(spec.roles) == 3 for spec in choices.preferred_specs))
        self.assertTrue(
            all(StopRole.DINNER in spec.roles for spec in choices.preferred_specs)
        )

    def test_complete_unregistered_sequence_is_executable(self) -> None:
        constraints = with_planning_window(
            planning_constraints(time_end="22:00"),
            start="14:00",
            end="22:00",
        ).model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=3,
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY, StopRole.ACTIVITY, StopRole.DINNER),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        choices = PlanSpecCompiler().compile_explicit_structure(
            constraints,
            self.baseline,
        )
        self.assertIsNotNone(choices)
        self.assertIsNone(choices.conflict)
        self.assertEqual(
            choices.preferred_specs[0].roles,
            (StopRole.ACTIVITY, StopRole.ACTIVITY, StopRole.DINNER),
        )

    def test_invalid_partial_proposal_uses_dynamic_fallback(self) -> None:
        constraints = with_planning_window(
            planning_constraints(time_end="22:00"),
            start="14:00",
            end="22:00",
        ).model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=3,
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.DINNER,),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        invalid = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "activity", "inclusion": "core"},
                {"role": "break", "inclusion": "core"},
            ],
        )
        choices = PlanSpecCompiler().compile(constraints, self.baseline, invalid)
        self.assertEqual(choices.proposal_status, "rejected")
        self.assertEqual(choices.diagnostic_code, "explicit_roles_not_preserved")
        self.assertTrue(choices.fallback_specs)
        self.assertTrue(
            any(StopRole.DINNER in spec.roles for spec in choices.fallback_specs)
        )

    def test_unregistered_required_sequence_gets_bounded_rule_completion(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(
                        StopRole.ACTIVITY,
                        StopRole.ACTIVITY,
                        StopRole.ACTIVITY,
                    ),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="活动、活动、活动",
                )
            }
        )

        choices = PlanSpecCompiler().compile(constraints, self.baseline, None)

        self.assertEqual(choices.proposal_status, "not_used")
        # With no model proposal, the deterministic completion is the active
        # preferred path (``fallback_specs`` is reserved for a rejected
        # proposal).  It must still contain the unregistered required
        # sequence rather than collapsing to a registered one-stop shape.
        self.assertTrue(choices.preferred_specs)
        self.assertLessEqual(len(choices.preferred_specs), 24)
        self.assertTrue(
            any(
                spec.roles[:3]
                == (
                    StopRole.ACTIVITY,
                    StopRole.ACTIVITY,
                    StopRole.ACTIVITY,
                )
                for spec in choices.preferred_specs
            )
        )

    def test_rule_completion_binds_required_meals_to_concrete_roles(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY, StopRole.LUNCH, StopRole.ACTIVITY),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="活动、午饭、活动",
                )
            }
        )

        choices = PlanSpecCompiler().compile(constraints, self.baseline, None)

        self.assertTrue(choices.preferred_specs)
        self.assertTrue(
            any(
                spec.roles
                == (
                    StopRole.ACTIVITY,
                    StopRole.LUNCH,
                    StopRole.ACTIVITY,
                )
                for spec in choices.preferred_specs
            )
        )

    def test_rejected_model_proposal_uses_dynamic_rule_completion(self) -> None:
        constraints = self.constraints.model_copy(
            update={
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(
                        StopRole.ACTIVITY,
                        StopRole.ACTIVITY,
                        StopRole.ACTIVITY,
                    ),
                    source=ConstraintSource.USER_EXPLICIT,
                )
            }
        )
        rejected = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[{"role": "activity", "inclusion": "core"}],
        )

        choices = PlanSpecCompiler().compile(constraints, self.baseline, rejected)

        self.assertEqual(choices.proposal_status, "rejected")
        self.assertEqual(choices.diagnostic_code, "explicit_roles_not_preserved")
        self.assertTrue(choices.fallback_specs)
        self.assertTrue(
            any(
                spec.roles[:3]
                == (
                    StopRole.ACTIVITY,
                    StopRole.ACTIVITY,
                    StopRole.ACTIVITY,
                )
                for spec in choices.fallback_specs
            )
        )
        self.assertTrue(
            all(spec.roles.count(StopRole.LUNCH) <= 1 for spec in choices.preferred_specs)
        )
        self.assertTrue(
            all(
                StopRole.MEAL not in spec.roles
                for spec in choices.preferred_specs
            )
        )

    def test_partial_structure_reports_capacity_shortage(self) -> None:
        constraints = with_planning_window(
            planning_constraints(time_end="14:30"),
            start="14:00",
            end="14:30",
        ).model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=3,
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.DINNER,),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        choices = PlanSpecCompiler().compile(constraints, self.baseline, None)
        self.assertEqual(choices.conflict.code, "NO_FEASIBLE_PLAN")

    def test_required_roles_cannot_exceed_exact_count(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=2,
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY, StopRole.LUNCH, StopRole.DINNER),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        choices = PlanSpecCompiler().compile_explicit_structure(
            constraints,
            self.baseline,
        )
        self.assertEqual(choices.conflict.code, "UNSUPPORTED_PLAN_STRUCTURE")

    def test_objectives_must_reference_existing_evidence(self) -> None:
        evidence_id = self.baseline.semantic_request.evidence[0].evidence_id
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v3",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "dinner", "inclusion": "core"},
            ],
            objectives=[
                SoftObjective(
                    kind="novelty",
                    evidence_refs=(evidence_id,),
                )
            ],
        )
        choices = PlanSpecCompiler().compile(
            self.constraints, self.baseline, proposal
        )
        self.assertEqual(choices.proposal_status, "compiled")

        ungrounded = proposal.model_copy(
            update={
                "objectives": (
                    SoftObjective(
                        kind="novelty",
                        evidence_refs=("user.preferences.missing",),
                    ),
                )
            }
        )
        rejected = PlanSpecCompiler().compile(
            self.constraints, self.baseline, ungrounded
        )
        self.assertEqual(rejected.proposal_status, "rejected")
        self.assertEqual(rejected.diagnostic_code, "objective_ungrounded")


if __name__ == "__main__":
    unittest.main()

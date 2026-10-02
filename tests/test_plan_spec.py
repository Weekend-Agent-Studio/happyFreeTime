import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, StopRole
from app.domain.planning import PlanPace, PlanningIntent
from app.services.plan_spec import PlanSpec
from app.services.planning import StructureCompiler, _select_plan_specs
from tests.test_planning import planning_constraints


class PlanSpecTest(unittest.TestCase):
    def test_plan_spec_is_only_a_concrete_ordered_role_sequence(self) -> None:
        spec = PlanSpec(
            spec_id="activity-dinner-test",
            roles=(StopRole.ACTIVITY, StopRole.DINNER),
        )

        self.assertEqual(spec.roles, (StopRole.ACTIVITY, StopRole.DINNER))
        self.assertFalse(hasattr(spec, "slots"))
        self.assertFalse(hasattr(spec, "precedence"))
        self.assertFalse(hasattr(spec, "min_stops"))
        self.assertFalse(hasattr(spec, "max_stops"))

    def test_explicit_structure_compiles_directly_to_plan_spec(self) -> None:
        constraints = planning_constraints(time_end="21:00").model_copy(
            update={
                "exact_stop_count": ConstraintValue[int](
                    value=2,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="两站",
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.ACTIVITY, StopRole.DINNER),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="先活动再晚饭",
                ),
            }
        )
        compilation = StructureCompiler().compile(constraints)

        self.assertIsNone(compilation.conflict)
        self.assertEqual(len(compilation.specs or ()), 1)
        spec = compilation.specs[0]
        self.assertIsInstance(spec, PlanSpec)
        self.assertEqual(spec.roles, (StopRole.ACTIVITY, StopRole.DINNER))

    def test_rule_factory_returns_only_bounded_plan_specs(self) -> None:
        constraints = planning_constraints(time_end="21:00")
        intent = PlanningIntent(pace=PlanPace.RELAXED)

        specs = _select_plan_specs(constraints, intent)

        self.assertTrue(specs)
        self.assertTrue(all(isinstance(spec, PlanSpec) for spec in specs))
        self.assertTrue(all(1 <= len(spec.roles) <= 4 for spec in specs))

    def test_repeated_roles_are_preserved_in_canonical_spec(self) -> None:
        spec = PlanSpec(
            spec_id="activity-lunch-activity-dinner-test",
            roles=(
                StopRole.ACTIVITY,
                StopRole.LUNCH,
                StopRole.ACTIVITY,
                StopRole.DINNER,
            ),
        )

        self.assertEqual(
            spec.roles,
            (
                StopRole.ACTIVITY,
                StopRole.LUNCH,
                StopRole.ACTIVITY,
                StopRole.DINNER,
            ),
        )

    def test_plan_spec_rejects_empty_or_oversized_sequences(self) -> None:
        for roles in ((), (StopRole.ACTIVITY,) * 5):
            with self.subTest(roles=roles):
                with self.assertRaises(ValueError):
                    PlanSpec(spec_id="invalid", roles=roles)

    def test_plan_spec_rejects_mutable_or_untyped_roles(self) -> None:
        with self.assertRaises(TypeError):
            PlanSpec(spec_id="mutable", roles=[StopRole.ACTIVITY])
        with self.assertRaises(TypeError):
            PlanSpec(spec_id="untyped", roles=("activity",))


if __name__ == "__main__":
    unittest.main()

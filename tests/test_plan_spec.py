import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, StopRole
from app.domain.planning import PlanPace, PlanningIntent
from app.services.plan_spec import PlanSlot, PlanSpec, RolePrecedence
from app.services.planning import StructureCompiler, _select_plan_specs
from tests.test_planning import planning_constraints


class PlanSpecTest(unittest.TestCase):
    def test_plan_spec_is_the_canonical_bounded_structure(self) -> None:
        spec = PlanSpec(
            spec_id="activity-dinner-test",
            slots=(PlanSlot(StopRole.ACTIVITY), PlanSlot(StopRole.DINNER)),
            precedence=(
                RolePrecedence(before=StopRole.ACTIVITY, after=StopRole.DINNER),
            ),
            min_stops=2,
            max_stops=2,
            pace=PlanPace.RELAXED,
        )

        self.assertEqual(spec.roles, (StopRole.ACTIVITY, StopRole.DINNER))
        self.assertEqual((spec.min_stops, spec.max_stops), (2, 2))
        self.assertEqual(spec.pace, PlanPace.RELAXED)

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
        self.assertEqual((spec.min_stops, spec.max_stops), (2, 2))
        self.assertEqual(
            spec.precedence,
            (RolePrecedence(before=StopRole.ACTIVITY, after=StopRole.DINNER),),
        )

    def test_rule_factory_returns_only_plan_specs(self) -> None:
        constraints = planning_constraints(time_end="21:00")
        intent = PlanningIntent(
            required_roles=(StopRole.ACTIVITY,),
            optional_roles=(StopRole.MEAL,),
            minimum_stops=2,
            maximum_stops=2,
            pace=PlanPace.RELAXED,
        )

        specs = _select_plan_specs(constraints, intent)

        self.assertTrue(specs)
        self.assertTrue(all(isinstance(spec, PlanSpec) for spec in specs))
        self.assertTrue(all(len(spec.roles) == 2 for spec in specs))
        self.assertTrue(all(spec.pace == PlanPace.RELAXED for spec in specs))
        self.assertTrue(all(len(spec.roles) <= 4 for spec in specs))

    def test_repeated_roles_are_preserved_in_canonical_spec(self) -> None:
        spec = PlanSpec(
            spec_id="activity-lunch-activity-dinner-test",
            slots=(
                PlanSlot(StopRole.ACTIVITY),
                PlanSlot(StopRole.LUNCH),
                PlanSlot(StopRole.ACTIVITY),
                PlanSlot(StopRole.DINNER),
            ),
            min_stops=4,
            max_stops=4,
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

    def test_optional_slot_can_be_represented_before_compilation(self) -> None:
        spec = PlanSpec(
            spec_id="optional-break-test",
            slots=(
                PlanSlot(StopRole.ACTIVITY),
                PlanSlot(StopRole.BREAK, required=False),
            ),
            min_stops=1,
            max_stops=2,
        )

        self.assertEqual(spec.roles, (StopRole.ACTIVITY, StopRole.BREAK))
        self.assertFalse(spec.slots[1].required)


if __name__ == "__main__":
    unittest.main()

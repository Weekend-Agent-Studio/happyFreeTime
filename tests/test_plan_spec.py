import unittest

from app.domain.constraints import ConstraintSource, ConstraintValue, StopRole
from app.domain.planning import PlanPace, PlanSkeleton, PlanningIntent
from app.services.plan_spec import CompiledPlanSpec, PlanSlot, RolePrecedence
from app.services.planning import StructureCompiler, _select_plan_specs
from tests.test_planning import planning_constraints


class PlanSpecTest(unittest.TestCase):
    def test_compiled_spec_is_a_bounded_compatibility_view_of_skeleton(self) -> None:
        skeleton = PlanSkeleton(
            skeleton_id="activity-dinner-test",
            roles=(StopRole.ACTIVITY, StopRole.DINNER),
        )
        spec = CompiledPlanSpec.from_skeleton(
            skeleton,
            precedence=(
                RolePrecedence(before=StopRole.ACTIVITY, after=StopRole.DINNER),
            ),
            pace=PlanPace.RELAXED,
        )

        self.assertEqual(spec.roles, skeleton.roles)
        self.assertEqual(spec.skeleton, skeleton)
        self.assertEqual((spec.min_stops, spec.max_stops), (2, 2))
        self.assertEqual(spec.pace, PlanPace.RELAXED)

    def test_explicit_structure_compiles_before_soft_intent(self) -> None:
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
        self.assertEqual(spec.roles, (StopRole.ACTIVITY, StopRole.DINNER))
        self.assertEqual((spec.min_stops, spec.max_stops), (2, 2))
        self.assertEqual(
            spec.precedence,
            (RolePrecedence(before=StopRole.ACTIVITY, after=StopRole.DINNER),),
        )

    def test_soft_intent_only_selects_existing_templates(self) -> None:
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
        self.assertTrue(all(isinstance(spec, CompiledPlanSpec) for spec in specs))
        self.assertTrue(all(len(spec.roles) == 2 for spec in specs))
        self.assertTrue(all(spec.pace == PlanPace.RELAXED for spec in specs))
        self.assertTrue(all(len(spec.roles) <= 4 for spec in specs))

    def test_plan_slot_can_mark_future_optional_position(self) -> None:
        spec = CompiledPlanSpec(
            skeleton_id="optional-break-test",
            slots=(
                PlanSlot(StopRole.ACTIVITY),
                PlanSlot(StopRole.BREAK, required=False),
            ),
            min_stops=1,
            max_stops=2,
        )

        self.assertEqual(spec.roles, (StopRole.ACTIVITY, StopRole.BREAK))
        self.assertFalse(spec.slots[1].required)

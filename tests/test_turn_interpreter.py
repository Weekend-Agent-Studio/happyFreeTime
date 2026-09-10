import unittest
from datetime import date

from app.domain.catalog import ResourceType
from app.domain.constraints import CommandOperation, Intent, StopRole
from app.services.demo_router import DemoRouter
from app.services.router_extractor import RouterContext


class TurnInterpreterTest(unittest.TestCase):
    def test_demo_interpreter_reports_offline_runtime_adapter(self) -> None:
        _, runtime = DemoRouter().interpret_with_runtime(
            "今天下午出去玩",
            RouterContext(current_date=date(2026, 9, 8)),
        )

        self.assertEqual(runtime.stage, "turn_interpreter")
        self.assertEqual(runtime.adapter, "demo_rule")
        self.assertFalse(runtime.model_invoked)
        self.assertEqual(runtime.attempts, 0)

    def test_demo_interpreter_extracts_bounded_keep_and_replace_command(self) -> None:
        interpretation = DemoRouter().interpret(
            "餐厅保留，只把活动换近一点",
            RouterContext(
                current_date=date(2026, 9, 8),
                has_plans=True,
                has_selected_plan=True,
            ),
        )

        self.assertEqual(interpretation.primary_intent, Intent.REFINE_PLAN)
        command = interpretation.conversation_command
        self.assertIsNotNone(command)
        self.assertEqual(command.operation, CommandOperation.REPLACE)
        self.assertEqual(command.target.role, StopRole.ACTIVITY)
        self.assertEqual(
            command.locked_targets[0].resource_type,
            ResourceType.RESTAURANT,
        )
        self.assertTrue(command.constraint_patch.prefer_shorter_travel)
        self.assertEqual(command.evidence["replace"], "活动换近一点")
        self.assertEqual(command.evidence["keep"], "餐厅保留")
        self.assertIsNone(command.target.resource_id)
        self.assertIsNone(command.locked_targets[0].resource_id)


if __name__ == "__main__":
    unittest.main()

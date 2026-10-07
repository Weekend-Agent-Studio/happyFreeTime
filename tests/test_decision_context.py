import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.constraints import (
    ActorContext,
    GeoLocation,
    IdentityType,
    Intent,
    PlanRequest,
    StopRole,
)
from app.domain.decision_context import DecisionContextBuilder
from app.domain.decision_context import RequestLifecycle
from app.domain.planning import Plan, Stop, StopType
from app.orchestration.entry_graph import build_entry_graph
from app.services.enrichment import EnvironmentContext
from app.services.router_extractor import RouterContext
from tests.router_support import InterpretationRouter


class DecisionContextTest(unittest.TestCase):
    def test_prompt_projection_contains_request_and_stops_but_redacts_ids(self) -> None:
        plan = Plan.model_construct(
            plan_id="plan-secret",
            stops=[
                Stop.model_construct(
                    resource_id="poi-secret",
                    type=StopType.ACTIVITY,
                    role=StopRole.ACTIVITY,
                    name="安静展览",
                ),
                Stop.model_construct(
                    resource_id="restaurant-secret",
                    type=StopType.RESTAURANT,
                    role=StopRole.DINNER,
                    name="晚餐餐厅",
                ),
            ],
        )
        context = DecisionContextBuilder.build(
            request=PlanRequest(revision=3),
            selected_plan=plan,
            active_plan_version_id="version-secret",
            has_plans=True,
            previous_user_act="patch_constraints",
            last_system_outcome="constraint_patch",
        )

        lines = "\n".join(context.prompt_lines())

        self.assertIn("请求版本：3", lines)
        self.assertIn("第1站：安静展览（activity）", lines)
        self.assertIn("第2站：晚餐餐厅（dinner）", lines)
        self.assertIn("replace_stop", lines)
        self.assertNotIn("plan-secret", lines)
        self.assertNotIn("poi-secret", lines)
        self.assertNotIn("version-secret", lines)

    def test_builder_does_not_offer_replace_without_selected_plan(self) -> None:
        context = DecisionContextBuilder.build(
            request=PlanRequest(),
            has_plans=True,
        )

        self.assertEqual(context.request_lifecycle, RequestLifecycle.PLANNED)
        self.assertNotIn("select_plan", context.allowed_actions)
        self.assertNotIn("replace_stop", context.allowed_actions)
        self.assertIn("patch_constraints", context.allowed_actions)

    def test_builder_does_not_offer_request_patch_without_current_request(self) -> None:
        context = DecisionContextBuilder.build()

        self.assertEqual(context.request_lifecycle, RequestLifecycle.EMPTY)
        self.assertNotIn("patch_constraints", context.allowed_actions)
        self.assertNotIn("replace_stop", context.allowed_actions)

    def test_empty_plan_request_is_not_an_active_request(self) -> None:
        context = DecisionContextBuilder.build(request=PlanRequest())

        self.assertEqual(context.request_lifecycle, RequestLifecycle.EMPTY)
        self.assertIsNone(context.current_request)
        self.assertNotIn("patch_constraints", context.allowed_actions)

    def test_non_empty_request_is_a_draft_until_a_plan_exists(self) -> None:
        context = DecisionContextBuilder.build(
            request=PlanRequest(preferences=["安静"])
        )

        self.assertEqual(context.request_lifecycle, RequestLifecycle.DRAFT)
        self.assertIsNotNone(context.current_request)
        self.assertIn("patch_constraints", context.allowed_actions)
        self.assertNotIn("replace_stop", context.allowed_actions)

    def test_router_context_keeps_context_optional_for_existing_adapters(self) -> None:
        context = RouterContext(
            current_date=date(2026, 10, 6),
            decision_context=DecisionContextBuilder.build(
                request=PlanRequest(revision=2),
                has_plans=True,
            ),
        )

        self.assertEqual(context.decision_context.current_request.revision, 2)

    def test_graph_supplies_bounded_context_to_natural_language_router(self) -> None:
        class CapturingRouter(InterpretationRouter):
            def __init__(self) -> None:
                self.context: RouterContext | None = None

            def interpret(self, user_input: str, context: RouterContext):
                self.context = context
                from app.domain.constraints import Interpretation

                return Interpretation(
                    primary_intent=Intent.CHITCHAT,
                    intent_scores={Intent.CHITCHAT: 1.0},
                    reply="你好",
                )

        router = CapturingRouter()
        environment = EnvironmentContext(
            now=datetime(2026, 10, 6, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="decision-context-test",
            session_id="decision-context-test",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=router,
            environment_provider=lambda _: environment,
        )

        graph.invoke(
            {
                "user_input": "还是便宜点吧",
                "actor": actor,
                "has_plans": True,
                "active_request": PlanRequest(revision=4),
            },
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertIsNotNone(router.context)
        self.assertIsNotNone(router.context.decision_context)
        self.assertEqual(
            router.context.decision_context.current_request.revision,
            4,
        )
        self.assertIn("patch_constraints", router.context.decision_context.allowed_actions)


if __name__ == "__main__":
    unittest.main()

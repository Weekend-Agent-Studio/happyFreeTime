import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from langgraph.types import Command

from app.domain.constraints import (
    ActorContext,
    GeoLocation,
    IdentityType,
    Intent,
    Interpretation,
    RawConstraints,
)
from app.orchestration.entry_graph import build_entry_graph
from app.services.enrichment import EnvironmentContext
from app.services.router_extractor import RouterContext


class FollowUpRouter:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        self.inputs.append(user_input)
        if "200" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 0.99},
                raw_constraints=RawConstraints(
                    date_text="今天",
                    time_text="下午",
                    budget_text="人均200",
                    budget_per_person=200,
                    strict_budget=True,
                ),
            )
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.99},
            raw_constraints=RawConstraints(
                date_text="今天",
                time_text="下午",
                budget_text="千万别超预算",
                strict_budget=True,
            ),
        )


class EntryGraphTest(unittest.TestCase):
    def test_interrupts_for_blocking_field_and_resumes_through_router(self) -> None:
        router = FollowUpRouter()
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(router=router, environment_provider=lambda _: environment)
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-1",
            identity_type=IdentityType.DEMO,
        )
        config = {"configurable": {"thread_id": actor.session_id}}

        first_result = graph.invoke(
            {
                "user_input": "今天下午出去玩，千万别超预算",
                "actor": actor,
            },
            config=config,
        )

        self.assertIn("__interrupt__", first_result)
        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, ("ask_question",))
        self.assertEqual(snapshot.interrupts[0].value["field"], "budget_per_person")

        final_result = graph.invoke(Command(resume="人均200"), config=config)

        self.assertTrue(final_result["ready_for_planning"])
        self.assertEqual(
            final_result["enrichment"].constraints.budget_per_person.value,
            200,
        )
        self.assertEqual(len(router.inputs), 2)
        self.assertIn("用户补充：人均200", router.inputs[1])

    def test_chitchat_finishes_without_entering_planning(self) -> None:
        class ChitchatRouter:
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.CHITCHAT,
                    intent_scores={Intent.CHITCHAT: 1.0},
                    reply="你好，今天想聊点什么？",
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=ChitchatRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-2",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "你好", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertFalse(result["ready_for_planning"])
        self.assertIsNone(result["candidate_set"])
        self.assertEqual(result["interpretation"].reply, "你好，今天想聊点什么？")

    def test_weather_intent_does_not_enter_planning(self) -> None:
        class WeatherRouter:
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.CHECK_WEATHER,
                    intent_scores={Intent.CHECK_WEATHER: 1.0},
                    raw_constraints=RawConstraints(date_text="今天"),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=WeatherRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-3",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "今天天气怎么样", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertFalse(result["ready_for_planning"])

    def test_non_blocking_request_finishes_with_structured_candidate_plans(self) -> None:
        class PlanningRouter:
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 0.99},
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        time_text="下午",
                        max_distance_text="别太远",
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=PlanningRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-4",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "今天下午出去玩，别太远", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertTrue(result["ready_for_planning"])
        self.assertGreaterEqual(len(result["candidate_set"].plans), 1)
        self.assertEqual(len(result["candidate_set"].plans[0].stops), 2)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from langgraph.types import Command

from app.domain.constraints import (
    ActorContext,
    ClarificationAction,
    GeoLocation,
    IdentityType,
    Intent,
    Interpretation,
    RawConstraints,
)
from app.orchestration.entry_graph import build_entry_graph
from app.services.demo_router import DemoRouter
from app.services.enrichment import EnvironmentContext
from app.providers.geocoding import MockGeocodingProvider
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


class ExplicitLocationRouter:
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_text="今天", time_text="下午", location_text="不存在地标"
            ),
        )


class EntryGraphTest(unittest.TestCase):
    def test_refine_plan_without_resolved_command_asks_without_planning(self) -> None:
        class UnresolvedModificationRouter:
            def interpret(
                self,
                user_input: str,
                context: RouterContext,
            ) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    conversation_command=None,
                )

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
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-unresolved-modification",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=UnresolvedModificationRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {
                "user_input": "把那个地方换一下",
                "actor": actor,
                "has_plans": True,
            },
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(
            result["modification_question"].field,
            "conversation_command",
        )
        self.assertTrue(result["modification_question"].need_question)
        self.assertIsNone(result["candidate_set"])
        self.assertIsNone(result["plan_diff"])
        self.assertEqual(result["plan_diffs"], ())

    def test_replace_without_selected_plan_asks_before_planning(self) -> None:
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
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-no-selection",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {
                "user_input": "餐厅保留，只把活动换近一点",
                "actor": actor,
                "has_plans": True,
                "active_plan_version_id": "version-1",
                "active_constraints": None,
                "selected_plan": None,
            },
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(result["modification_question"].field, "selected_plan_id")
        self.assertIsNone(result["candidate_set"])

    def test_unresolvable_explicit_location_interrupts_without_falling_back_to_default(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )
        actor = ActorContext(user_id="demo-user", session_id="session-geo-gate", identity_type=IdentityType.DEMO)
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )

        result = graph.invoke({"user_input": "不存在地标今天下午出去玩", "actor": actor}, config={"configurable": {"thread_id": actor.session_id}})

        self.assertEqual(result["__interrupt__"][0].value["field"], "location")

    def test_location_question_can_use_default_without_reentering_router(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-location-default",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        question = first["__interrupt__"][0].value
        self.assertIn("use-default-location", {item["id"] for item in question["options"]})
        final = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "action": ClarificationAction.USE_DEFAULT.value,
                }
            ),
            config=config,
        )
        self.assertTrue(final["ready_for_planning"])
        self.assertEqual(
            final["enrichment"].constraints.location.source.value,
            "system_context",
        )
        self.assertTrue(
            any(item.rule_id == "clarification.default.location.v1" for item in final["enrichment"].assumptions)
        )

    def test_invalid_clarification_reaches_cap_without_repeating_free_text_forever(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-location-cap",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        current = first["__interrupt__"][0].value
        for _ in range(2):
            result = graph.invoke(
                Command(
                    resume={
                        "clarification_id": current["clarification_id"],
                        "action": ClarificationAction.ANSWER.value,
                        "value": "?",
                    }
                ),
                config=config,
            )
            current = result["__interrupt__"][0].value
        self.assertEqual(current["attempt"], 2)
        self.assertFalse(current["allow_free_text"])

    def test_clarification_cancel_returns_without_planning(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-location-cancel",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        question = first["__interrupt__"][0].value
        cancelled = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "action": ClarificationAction.CANCEL.value,
                }
            ),
            config=config,
        )
        self.assertIsNone(cancelled["candidate_set"])
        self.assertIn("取消", cancelled["interpretation"].reply)

    def test_new_request_during_clarification_does_not_carry_old_interpretation(self) -> None:
        class NewRequestRouter:
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                self.inputs.append(user_input)
                if user_input.startswith("旧需求"):
                    return Interpretation(
                        primary_intent=Intent.PLAN_OUTING,
                        intent_scores={Intent.PLAN_OUTING: 1.0},
                        raw_constraints=RawConstraints(
                            date_text="今天", time_text="下午", location_text="不存在地标",
                            preferences=["旧偏好"],
                        ),
                    )
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    raw_constraints=RawConstraints(date_text="今天", time_text="下午"),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-new-request",
            identity_type=IdentityType.DEMO,
        )
        router = NewRequestRouter()
        graph = build_entry_graph(
            router=router,
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "旧需求今天下午出去玩", "actor": actor}, config=config
        )
        question = first["__interrupt__"][0].value
        resumed = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "action": ClarificationAction.NEW_REQUEST.value,
                    "value": "新需求今天下午看展",
                }
            ),
            config=config,
        )
        self.assertTrue(resumed["ready_for_planning"])
        self.assertEqual(router.inputs, ["旧需求今天下午出去玩", "新需求今天下午看展"])
        self.assertNotIn("旧偏好", resumed["interpretation"].raw_constraints.preferences)
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
        # The answer is projected onto the pending field and resumes at
        # Enrichment; the original request is not sent through the full Router
        # a second time.
        self.assertEqual(len(router.inputs), 1)
        self.assertNotIn("用户补充：人均200", router.inputs[0])

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

    def test_weather_condition_with_plan_constraint_enters_existing_planning_chain(self) -> None:
        class ConditionalWeatherRouter:
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.CHECK_WEATHER,
                    intent_scores={Intent.CHECK_WEATHER: 1.0},
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        time_text="下午",
                        preferences=["下雨就安排室内活动"],
                        scene_tags=["室内"],
                    ),
                    evidence_map={
                        "preferences": "下雨就安排室内活动",
                        "scene_tags": "室内活动",
                    },
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
            router=ConditionalWeatherRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-weather-condition",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "先看天气，要是下雨就安排室内活动", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertTrue(result["ready_for_planning"])
        self.assertIsNotNone(result["candidate_set"])
        self.assertTrue(result["candidate_set"].plans)

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

        config = {"configurable": {"thread_id": actor.session_id}}
        with self.assertNoLogs(level="WARNING"):
            result = graph.invoke(
                {"user_input": "今天下午出去玩，别太远", "actor": actor},
                config=config,
            )
            restored = graph.get_state(config).values["candidate_set"]

        self.assertTrue(result["ready_for_planning"])
        self.assertGreaterEqual(len(result["candidate_set"].plans), 1)
        self.assertEqual(len(result["candidate_set"].plans[0].stops), 2)
        self.assertEqual(restored, result["candidate_set"])

    def test_tonight_is_compiled_to_today_evening_without_a_date_question(self) -> None:
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
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-tonight-contract",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {"user_input": "今晚只安排一家晚饭", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertTrue(result["ready_for_planning"])
        self.assertEqual(result["enrichment"].constraints.date.value, date(2026, 8, 12))
        self.assertEqual(
            result["enrichment"].constraints.time_window.value.model_dump(),
            {"start": "18:00", "end": "22:00"},
        )
        self.assertTrue(result["candidate_set"].plans)

    def test_runtime_decisions_distinguish_demo_router_and_rule_planning(self) -> None:
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
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-runtime-trace",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {"user_input": "今天下午出去玩", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(result["runtime_decisions"][0].adapter, "demo_rule")
        self.assertFalse(result["runtime_decisions"][0].model_invoked)
        self.assertEqual(result["runtime_decisions"][1].adapter, "rule_based")
        self.assertFalse(result["runtime_decisions"][1].model_invoked)

    def test_demo_return_deadline_question_resumes_with_a_bare_clock_answer(self) -> None:
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
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-return-resume",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )
        config = {"configurable": {"thread_id": actor.session_id}}

        first_result = graph.invoke(
            {"user_input": "今天下午出去玩，晚饭前后一定要回家", "actor": actor},
            config=config,
        )
        self.assertEqual(first_result["__interrupt__"][0].value["field"], "return_by")

        final_result = graph.invoke(Command(resume="18:00"), config=config)

        self.assertTrue(final_result["ready_for_planning"])
        self.assertFalse(final_result["question_decision"].need_question)
        self.assertTrue(final_result["candidate_set"].plans)
        self.assertTrue(
            all(
                plan.route_legs[-1].destination_name == "出发地"
                for plan in final_result["candidate_set"].plans
            )
        )

    def test_demo_unresolved_date_is_not_replaced_by_session_default(self) -> None:
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
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-unresolved-date",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {"user_input": "等忙完那天带家里人出去", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(result["__interrupt__"][0].value["field"], "date")
        self.assertEqual(
            result["__interrupt__"][0].value["rule_id"],
            "question.date.unresolved.v1",
        )


if __name__ == "__main__":
    unittest.main()

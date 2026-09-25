import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.catalog import ResourceType
from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    PartyProfile,
    StopRole,
    TimeWindow,
)
from app.domain.planning import (
    PlanPace,
    PlanStructureProposal,
    PlanningIntent,
    PlanningIntentDecision,
    PlanningIntentProposal,
    RoleQueryProposal,
)
from app.domain.semantics import SoftObjective
from app.services.planning import PlanningService, _build_planning_intent
from app.services.catalog import InMemoryCatalog
from app.services.planning_intent import (
    LlmPlanningIntentProvider,
    RuleBasedPlanningIntentProvider,
    build_default_planning_intent_provider,
)
from tests.test_native_planning import candidate
from tests.test_planning import FixedReplayRouteProvider, planning_constraints


class SequencePlanningModel:
    """A deterministic structured-model fake; never makes a network request."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages: list[object]) -> object:
        self.calls += 1
        if not self.responses:
            raise RuntimeError("no fake response left")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def relaxed_proposal(**overrides: object) -> dict[str, object]:
    proposal: dict[str, object] = {
        "required_roles": ["activity"],
        "optional_roles": ["meal"],
        "precedence": [],
        "minimum_stops": 2,
        "maximum_stops": 2,
        "pace": "relaxed",
        "evidence": {"pace": "慢慢走"},
        "confidence": 0.95,
    }
    proposal.update(overrides)
    return proposal


def dinner_only_constraints():
    return planning_constraints(budget=150, time_end="22:00").model_copy(
        update={
            "time_window": ConstraintValue[TimeWindow](
                value=TimeWindow(start="18:00", end="22:00"),
                source=ConstraintSource.USER_INFERRED,
            ),
            "exact_stop_count": ConstraintValue[int](
                value=1,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text="只安排一家",
            ),
            "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                value=(StopRole.DINNER,),
                source=ConstraintSource.USER_EXPLICIT,
                raw_text="晚饭",
            ),
        }
    )


def single_role_constraints(role: StopRole):
    window = {
        StopRole.ACTIVITY: TimeWindow(start="14:00", end="18:00"),
        StopRole.LUNCH: TimeWindow(start="11:30", end="14:00"),
    }[role]
    return planning_constraints(time_end=window.end).model_copy(
        update={
            "time_window": ConstraintValue[TimeWindow](
                value=window,
                source=ConstraintSource.USER_INFERRED,
            ),
            "exact_stop_count": ConstraintValue[int](
                value=1,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text="只安排一个",
            ),
            "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                value=(role,),
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=role.value,
            ),
        }
    )


class PlanningIntentProviderTest(unittest.TestCase):
    def test_v2_objectives_are_merged_into_semantic_request(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["带父母", "新鲜感", "不希望太累"]}
        )
        baseline = RuleBasedPlanningIntentProvider().decide(constraints).intent
        evidence = [item.evidence_id for item in baseline.semantic_request.evidence]
        proposal = PlanStructureProposal(
            schema_version="plan-structure-proposal.v2",
            slots=[
                {"role": "activity", "inclusion": "core"},
                {"role": "dinner", "inclusion": "core"},
            ],
            objectives=[
                SoftObjective(kind="family_friendly", evidence_refs=(evidence[0],)),
                SoftObjective(kind="novelty", evidence_refs=(evidence[1],)),
                SoftObjective(kind="low_fatigue", evidence_refs=(evidence[-1],)),
            ],
        )

        decision = LlmPlanningIntentProvider(
            SequencePlanningModel(proposal)
        ).decide(constraints)

        self.assertEqual(decision.source, "llm")
        self.assertTrue(decision.proposal_accepted)
        self.assertEqual(
            {
                objective.kind.value
                for objective in decision.intent.semantic_request.objectives
            },
            {"family_friendly", "novelty", "low_fatigue"},
        )
        self.assertTrue(
            all(objective.evidence_refs for objective in decision.intent.semantic_request.objectives)
        )

    def test_llm_bounded_slots_and_role_queries_are_compiled_to_grounded_intent(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "preferences": ["纪念日", "适合聊天"],
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="11:00", end="22:00"),
                    source=ConstraintSource.USER_INFERRED,
                ),
            }
        )
        baseline = RuleBasedPlanningIntentProvider().decide(constraints).intent
        evidence = [item.evidence_id for item in baseline.semantic_request.evidence]
        proposal = relaxed_proposal(
            required_roles=[],
            optional_roles=[],
            minimum_stops=4,
            maximum_stops=4,
            slots=[
                {"role": "activity", "required": True},
                {"role": "lunch", "required": True},
                {"role": "activity", "required": True},
                {"role": "dinner", "required": True},
            ],
            role_queries={
                "lunch": {
                    "text": "纪念日午餐，适合聊天",
                    "evidence_refs": evidence,
                },
                "activity": {
                    "text": "纪念日共同体验",
                    "evidence_refs": evidence,
                },
                "dinner": {
                    "text": "浪漫晚餐，适合聊天",
                    "evidence_refs": evidence,
                },
            },
        )

        decision = LlmPlanningIntentProvider(SequencePlanningModel(proposal)).decide(
            constraints
        )

        self.assertEqual(decision.source, "llm")
        self.assertTrue(decision.proposal_present)
        self.assertTrue(decision.proposal_accepted)
        self.assertIsNone(decision.proposal_rejection_reason)
        self.assertEqual(
            decision.intent.required_roles,
            (StopRole.ACTIVITY, StopRole.LUNCH, StopRole.DINNER),
        )
        self.assertEqual(
            tuple(slot.role for slot in decision.intent.slots),
            (
                StopRole.ACTIVITY,
                StopRole.LUNCH,
                StopRole.ACTIVITY,
                StopRole.DINNER,
            ),
        )
        self.assertEqual(
            {
                query.target_role: query.text
                for query in decision.intent.semantic_request.queries
                if query.target_role is not None
            },
            {
                StopRole.LUNCH: "纪念日午餐，适合聊天",
                StopRole.ACTIVITY: "纪念日共同体验",
                StopRole.DINNER: "浪漫晚餐，适合聊天",
            },
        )

    def test_role_query_with_unknown_evidence_falls_back(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["纪念日"]}
        )
        proposal = relaxed_proposal(
            role_queries={
                "activity": RoleQueryProposal(
                    text="未出现的偏好",
                    evidence_refs=("user.preferences.999",),
                )
            }
        )

        decision = LlmPlanningIntentProvider(SequencePlanningModel(proposal)).decide(
            constraints
        )

        self.assertEqual(decision.source, "fallback")
        self.assertEqual(
            decision.fallback_reason,
            "invalid_proposal_contract:proposal_out_of_bounds",
        )
        self.assertFalse(decision.proposal_present)
        self.assertFalse(decision.proposal_accepted)
        self.assertEqual(
            decision.proposal_rejection_reason,
            "invalid_proposal_contract:proposal_out_of_bounds",
        )

    def test_llm_decision_aggregates_token_usage_across_format_repair(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        parsed = PlanningIntentProposal.model_validate(relaxed_proposal())
        model = SequencePlanningModel(
            {
                "raw": SimpleNamespace(
                    usage_metadata={"input_tokens": 30, "output_tokens": 4}
                ),
                "parsed": None,
                "parsing_error": ValueError("invalid json"),
            },
            {
                "raw": SimpleNamespace(
                    response_metadata={
                        "token_usage": {"prompt_tokens": 36, "completion_tokens": 9}
                    }
                ),
                "parsed": parsed,
                "parsing_error": None,
            },
        )

        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(decision.attempts, 2)
        self.assertEqual(decision.input_tokens, 66)
        self.assertEqual(decision.output_tokens, 13)

    def test_rule_provider_keeps_previous_rule_behavior(self) -> None:
        provider = RuleBasedPlanningIntentProvider()
        normal = planning_constraints(time_end="22:00")
        dinner = dinner_only_constraints()

        self.assertEqual(provider.decide(normal).intent, _build_planning_intent(normal))
        self.assertEqual(provider.decide(dinner).intent, _build_planning_intent(dinner))
        self.assertEqual(provider.decide(normal).source, "rule_based")

    def test_valid_llm_proposal_changes_the_bounded_intent(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走", "不要太累"]}
        )
        model = SequencePlanningModel(relaxed_proposal())
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 1)
        self.assertEqual(decision.source, "llm")
        self.assertEqual(decision.intent.pace, PlanPace.RELAXED)
        self.assertEqual(decision.intent.maximum_stops, 2)
        self.assertEqual(decision.attempts, 1)

    def test_grounded_evidence_without_model_queries_keeps_baseline_dense_queries(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["能互动探索"], "scene_tags": ["室内"]}
        )
        baseline = RuleBasedPlanningIntentProvider().decide(constraints).intent
        baseline_evidence = [item.model_dump() for item in baseline.semantic_request.evidence]
        proposal = relaxed_proposal(
            semantic_request={
                "evidence": baseline_evidence,
                "objectives": [],
                "queries": [],
            }
        )

        decision = LlmPlanningIntentProvider(
            SequencePlanningModel(proposal)
        ).decide(constraints)

        self.assertEqual(decision.source, "llm")
        self.assertEqual(
            [query.text for query in decision.intent.semantic_request.queries],
            ["能互动探索", "室内"],
        )

    def test_invalid_first_output_can_be_repaired_once(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel(
            {"roles": ["not-a-role"]},
            relaxed_proposal(),
        )
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 2)
        self.assertEqual(decision.source, "llm")
        self.assertEqual(decision.attempts, 2)

    def test_structured_output_parsing_error_envelope_reaches_repair(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        parsed = PlanningIntentProposal.model_validate(relaxed_proposal())
        model = SequencePlanningModel(
            {
                "raw": object(),
                "parsed": None,
                "parsing_error": ValueError("invalid json"),
            },
            {"raw": object(), "parsed": parsed, "parsing_error": None},
        )
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 2)
        self.assertEqual(decision.source, "llm")
        self.assertEqual(decision.attempts, 2)

    def test_two_invalid_outputs_fall_back_without_unbounded_retry(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel({"unknown": True}, {"unknown": True}, {"unknown": True})
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 2)
        self.assertEqual(decision.source, "fallback")
        self.assertEqual(decision.fallback_reason, "invalid_proposal_parse")
        self.assertEqual(decision.attempts, 2)

    def test_model_exception_falls_back_without_retrying(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel(RuntimeError("provider unavailable"))
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 1)
        self.assertEqual(decision.source, "fallback")
        self.assertEqual(decision.fallback_reason, "provider_error")

    def test_model_timeout_is_distinguished_and_falls_back(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel(TimeoutError("deadline exceeded"))
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 1)
        self.assertEqual(decision.source, "fallback")
        self.assertEqual(decision.fallback_reason, "timeout")

    def test_membership_phrase_maps_to_romantic_objective(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={
                "party": planning_constraints(time_end="22:00").party.model_copy(
                    update={
                        "value": PartyProfile(adults=1, members=["跟女朋友"]),
                    }
                )
            }
        )

        decision = RuleBasedPlanningIntentProvider().decide(constraints)

        self.assertEqual(
            [item.kind.value for item in decision.intent.semantic_request.objectives],
            ["romantic"],
        )
        self.assertEqual(
            decision.intent.semantic_request.objectives[0].evidence_refs,
            ("user.members.1",),
        )

    def test_low_confidence_falls_back(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel(relaxed_proposal(confidence=0.2))
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(decision.source, "fallback")
        self.assertEqual(decision.fallback_reason, "low_confidence")
        self.assertEqual(decision.attempts, 1)

    def test_model_evidence_is_filtered_to_normalized_soft_input(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel(
            relaxed_proposal(
                evidence={
                    "hallucinated": "用户明确要求去月球",
                    "preference": "慢慢走",
                }
            )
        )
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(decision.source, "llm")
        self.assertNotIn("hallucinated", decision.intent.evidence)
        self.assertEqual(decision.intent.evidence["soft_preference"], "慢慢走")

    def test_llm_evidence_drops_stale_baseline_pace(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        # “慢慢走” isn't one of the deterministic pace aliases, so the
        # baseline is balanced while the accepted proposal is relaxed.
        model = SequencePlanningModel(
            relaxed_proposal(
                evidence={
                    "pace": "relaxed",
                    "preference": "慢慢走",
                    "hallucinated": "用户明确要求去月球",
                }
            )
        )
        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(decision.source, "llm")
        self.assertEqual(decision.intent.pace, PlanPace.RELAXED)
        self.assertNotIn("pace", decision.intent.evidence)
        self.assertEqual(decision.intent.evidence["soft_preference"], "慢慢走")
        self.assertNotIn("hallucinated", decision.intent.evidence)

    def test_hard_structure_and_allowed_roles_cannot_be_overridden(self) -> None:
        normal = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        cases = [
            (normal, relaxed_proposal(required_roles=[])),
            (normal, relaxed_proposal(optional_roles=["dinner"])),
            (normal, relaxed_proposal(precedence=[["meal", "activity"]])),
            (
                normal.model_copy(
                    update={
                        "exact_stop_count": ConstraintValue[int](
                            value=1, source=ConstraintSource.USER_EXPLICIT
                        )
                    }
                ),
                relaxed_proposal(minimum_stops=2, maximum_stops=2),
            ),
        ]
        for constraints, proposal in cases:
            with self.subTest(proposal=proposal):
                model = SequencePlanningModel(proposal)
                decision = LlmPlanningIntentProvider(model).decide(constraints)
                self.assertEqual(decision.source, "fallback")
                self.assertEqual(
                    decision.fallback_reason,
                    "invalid_proposal_contract:proposal_out_of_bounds",
                )

    def test_explicit_single_dinner_and_clear_default_skip_model(self) -> None:
        dinner = dinner_only_constraints()
        dinner_model = SequencePlanningModel(relaxed_proposal())
        dinner_decision = LlmPlanningIntentProvider(dinner_model).decide(dinner)
        self.assertEqual(dinner_model.calls, 0)
        self.assertEqual(dinner_decision.source, "rule_based")

        normal_model = SequencePlanningModel(relaxed_proposal())
        normal_decision = LlmPlanningIntentProvider(normal_model).decide(
            planning_constraints(time_end="22:00")
        )
        self.assertEqual(normal_model.calls, 0)
        self.assertEqual(normal_decision.source, "rule_based")

    def test_explicit_single_activity_and_lunch_use_role_specific_rule_intents(self) -> None:
        for role in (StopRole.ACTIVITY, StopRole.LUNCH, StopRole.DINNER):
            with self.subTest(role=role):
                constraints = (
                    dinner_only_constraints()
                    if role == StopRole.DINNER
                    else single_role_constraints(role)
                )
                decision = RuleBasedPlanningIntentProvider().decide(constraints)

                self.assertEqual(decision.intent.required_roles, (role,))
                self.assertEqual(decision.intent.optional_roles, ())
                self.assertEqual(
                    (decision.intent.minimum_stops, decision.intent.maximum_stops),
                    (1, 1),
                )

    def test_single_activity_llm_proposal_stays_inside_activity_only_skeleton(self) -> None:
        constraints = single_role_constraints(StopRole.ACTIVITY).model_copy(
            update={"preferences": ["有新鲜感"]}
        )
        model = SequencePlanningModel(
            relaxed_proposal(
                required_roles=["activity"],
                optional_roles=[],
                minimum_stops=1,
                maximum_stops=1,
                evidence={"preference": "有新鲜感"},
            )
        )

        decision = LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(decision.source, "llm")
        self.assertEqual(decision.intent.required_roles, (StopRole.ACTIVITY,))
        self.assertEqual((decision.intent.minimum_stops, decision.intent.maximum_stops), (1, 1))
        self.assertEqual(model.calls, 1)

    def test_diet_tags_and_avoid_do_not_trigger_structure_model(self) -> None:
        for field_name in ("diet_tags", "avoid"):
            with self.subTest(field_name=field_name):
                constraints = planning_constraints(time_end="22:00").model_copy(
                    update={field_name: ["素食"] if field_name == "diet_tags" else ["拥挤"]}
                )
                model = SequencePlanningModel(relaxed_proposal())
                decision = LlmPlanningIntentProvider(model).decide(constraints)

                self.assertEqual(model.calls, 0)
                self.assertEqual(decision.source, "rule_based")

    def test_deterministic_conflicts_do_not_call_intent_provider(self) -> None:
        class UnexpectedProvider:
            def decide(self, _: object) -> PlanningIntentDecision:
                raise AssertionError("hard conflict must fail before intent model")

        invalid_time = planning_constraints(time_end="18:00").model_copy(
            update={
                "preferences": ["有新鲜感"],
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="14:00", end="18:00"),
                    source=ConstraintSource.USER_EXPLICIT,
                    rule_id="time.explicit_range.v1",
                ),
                "departure_at": ConstraintValue[str](
                    value="13:30",
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )
        invalid_structure = planning_constraints(time_end="22:00").model_copy(
            update={
                "preferences": ["有新鲜感"],
                "exact_stop_count": ConstraintValue[int](
                    value=1,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="只安排一家",
                ),
                "required_stop_roles": ConstraintValue[tuple[StopRole, ...]](
                    value=(StopRole.BREAK,),
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="休息",
                ),
            }
        )

        for constraints, expected_code in (
            (invalid_time, "DEPARTURE_OUTSIDE_TIME_WINDOW"),
            (invalid_structure, "UNSUPPORTED_PLAN_STRUCTURE"),
        ):
            with self.subTest(expected_code=expected_code):
                result = PlanningService(
                    planning_intent_provider=UnexpectedProvider()
                ).plan(constraints)
                self.assertEqual(result.conflict.code, expected_code)
                self.assertIsNone(result.planning_intent_decision)

    def test_structural_soft_preference_triggers_one_model_call(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        model = SequencePlanningModel(relaxed_proposal())

        LlmPlanningIntentProvider(model).decide(constraints)

        self.assertEqual(model.calls, 1)

    def test_llm_mode_requires_an_explicit_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {"HFT_PLANNING_INTENT_MODE": "llm"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "requires LLM_API"):
                build_default_planning_intent_provider()

    def test_llm_builder_configures_timeout_and_disables_internal_retries(self) -> None:
        from app.services import planning_intent as planning_intent_module

        with patch.dict(
            os.environ,
            {
                "HFT_PLANNING_INTENT_MODE": "llm",
                "HFT_PLANNING_INTENT_TIMEOUT_SECONDS": "7.5",
                "LLM_API": "test-key",
            },
            clear=True,
        ), patch.object(planning_intent_module, "ChatOpenAI") as chat_openai:
            chat_openai.return_value.with_structured_output.return_value = object()
            build_default_planning_intent_provider()

        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 7.5)
        self.assertEqual(kwargs["max_retries"], 0)
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})
        chat_openai.return_value.with_structured_output.assert_called_once_with(
            PlanStructureProposal,
            method="function_calling",
            include_raw=True,
        )

    def test_injected_decision_changes_planner_structure_and_keeps_trace(self) -> None:
        constraints = planning_constraints(time_end="22:00").model_copy(
            update={"preferences": ["慢慢走"]}
        )
        intent = PlanningIntent(
            required_roles=(StopRole.ACTIVITY,),
            optional_roles=(StopRole.MEAL,),
            minimum_stops=2,
            maximum_stops=2,
            pace=PlanPace.RELAXED,
            evidence={"pace": "慢慢走"},
        )

        class FixedProvider:
            def decide(self, _: object) -> PlanningIntentDecision:
                return PlanningIntentDecision(
                    intent=intent,
                    source="llm",
                    confidence=0.9,
                    attempts=1,
                    prompt_version="test.v1",
                    model_name="fake",
                    input_tokens=12,
                    output_tokens=5,
                )

        result = PlanningService(
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
            planning_intent_provider=FixedProvider(),
        ).plan(constraints)

        self.assertIsNotNone(result.planning_intent_decision)
        self.assertEqual(result.planning_intent_decision.source, "llm")
        self.assertEqual(result.runtime_decision.input_tokens, 12)
        self.assertEqual(result.runtime_decision.output_tokens, 5)
        self.assertTrue(result.plans)
        self.assertTrue(all(len(plan.stops) == 2 for plan in result.plans))
        self.assertTrue(all(plan.skeleton_id == "activity-meal-v1" for plan in result.plans))

    def test_rule_and_llm_same_input_have_explainable_structure_difference(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate(
                    "morning-activity",
                    ResourceType.ACTIVITY,
                    "上午展览",
                    ["展览"],
                    duration_minutes=120,
                    open_hours={"sat": "09:00-12:00"},
                ),
                candidate(
                    "lunch",
                    ResourceType.RESTAURANT,
                    "午餐馆",
                    ["午餐"],
                    duration_minutes=60,
                    open_hours={"sat": "11:00-14:00"},
                ),
                candidate(
                    "afternoon-activity",
                    ResourceType.ACTIVITY,
                    "下午展览",
                    ["展览"],
                    duration_minutes=300,
                    open_hours={"sat": "12:00-18:00"},
                ),
                candidate(
                    "dinner",
                    ResourceType.RESTAURANT,
                    "晚餐馆",
                    ["晚餐"],
                    duration_minutes=60,
                    open_hours={"sat": "17:00-21:00"},
                ),
            ]
        )
        constraints = planning_constraints(
            budget=1_000,
            max_distance_km=30,
            time_end="21:00",
        ).model_copy(
            update={
                "time_window": ConstraintValue[TimeWindow](
                    value=TimeWindow(start="09:00", end="21:00"),
                    source=ConstraintSource.USER_INFERRED,
                ),
                "preferences": ["慢慢走"],
            }
        )
        rule_result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
            planning_intent_provider=RuleBasedPlanningIntentProvider(),
        ).plan(constraints)
        llm_result = PlanningService(
            catalog=catalog,
            route_provider=FixedReplayRouteProvider(duration_minutes=10, distance_km=2),
            planning_intent_provider=LlmPlanningIntentProvider(
                SequencePlanningModel(relaxed_proposal())
            ),
        ).plan(constraints)

        self.assertEqual(
            rule_result.planning_intent_decision.intent.maximum_stops,
            4,
        )
        self.assertEqual(
            llm_result.planning_intent_decision.intent.maximum_stops,
            2,
        )
        self.assertTrue(rule_result.plans)
        self.assertTrue(llm_result.plans)
        self.assertLess(
            llm_result.planning_intent_decision.intent.maximum_stops,
            rule_result.planning_intent_decision.intent.maximum_stops,
        )
        self.assertTrue(
            all(
                len(plan.stops)
                <= llm_result.planning_intent_decision.intent.maximum_stops
                for plan in llm_result.plans
            )
        )
        self.assertTrue(
            all(
                len(plan.stops)
                <= rule_result.planning_intent_decision.intent.maximum_stops
                for plan in rule_result.plans
            )
        )
        self.assertTrue(any(len(plan.stops) == 4 for plan in rule_result.plans))
        self.assertTrue(all(len(plan.stops) == 2 for plan in llm_result.plans))
        self.assertNotEqual(
            {plan.skeleton_id for plan in rule_result.plans},
            {plan.skeleton_id for plan in llm_result.plans},
        )
        self.assertIsNone(rule_result.conflict)
        self.assertIsNone(llm_result.conflict)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.constraints import (
    ConstraintPatch,
    EventClockProposal,
    Intent,
    Interpretation,
    RawConstraints,
    StopRole,
    PeriodProposal,
    TripRangeProposal,
    TimeScope,
)
from app.domain.decision_context import (
    DecisionContext,
    RequestContextSummary,
    SelectedPlanContextSummary,
    StopContextSummary,
)
from app.domain.turn import (
    AnswerQuery,
    ApplyRequestPatch,
    CheckWeatherProposal,
    ChitchatProposal,
    CreatePlanProposal,
    ModifySelectedPlan,
    NeedsClarification,
    NoAction,
    PatchConstraintsProposal,
    ReplaceStopProposal,
    QueryPlanProposal,
    TurnCompiler,
    TurnProposal,
    TurnTargetProposal,
)
from app.services import router_extractor as router_extractor_module
from app.services.router_extractor import (
    RouterContext,
    TurnInterpreter,
    build_default_turn_interpreter,
)


class FakeStructuredModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> object:
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _planned_context() -> DecisionContext:
    return DecisionContext(
        current_request=RequestContextSummary(revision=1),
        selected_plan=SelectedPlanContextSummary(
            stops=(
                StopContextSummary(stop_index=0, role="activity", name="公园"),
                StopContextSummary(stop_index=1, role="dinner", name="餐厅"),
            )
        ),
        allowed_actions=(
            "create_plan",
            "patch_constraints",
            "replace_stop",
            "query_plan",
            "check_weather",
            "chitchat",
        ),
    )


class TurnInterpreterTest(unittest.TestCase):
    def test_time_wire_contract_uses_discriminated_scopes(self) -> None:
        self.assertEqual(
            TripRangeProposal(start="10:00", end="16:00", evidence="10到16点").kind,
            "trip_range",
        )
        self.assertEqual(
            EventClockProposal(event="departure", clock="09:00", evidence="九点出发").kind,
            "event_clock",
        )
        with self.assertRaises(ValueError):
            TripRangeProposal(start="16:00", end="10:00", evidence="16到10点")
        with self.assertRaises(ValueError):
            PeriodProposal(event="departure", period=TimeScope.ALL_DAY, evidence="全天出发")
        with self.assertRaises(ValueError):
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                time_proposals=(
                    PeriodProposal(event="trip", period=TimeScope.AFTERNOON, evidence="下午"),
                    PeriodProposal(event="departure", period=TimeScope.AFTERNOON, evidence="下午"),
                ),
            )
        with self.assertRaises(ValueError):
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                time_proposals=(
                    TripRangeProposal(start="14:00", end="18:00", evidence="下午"),
                    EventClockProposal(event="departure", clock="14:00", evidence="下午"),
                ),
            )

    def test_production_builder_uses_turn_proposal_schema(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_API": "test-key",
                "MODEL_NAME": "deepseek-v4-flash",
                "BASE_URL": "https://api.deepseek.com",
            },
            clear=True,
        ), patch.object(router_extractor_module, "ChatOpenAI") as chat_openai:
            build_default_turn_interpreter()

        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 2048)
        self.assertEqual(kwargs["timeout"], 15)
        self.assertEqual(kwargs["max_retries"], 0)
        chat_openai.return_value.with_structured_output.assert_called_once_with(
            TurnProposal,
            method="function_calling",
            include_raw=True,
        )

    def test_turn_proposal_has_one_discriminated_action_and_rejects_legacy_fields(self) -> None:
        proposal = TurnProposal(
            act=CreatePlanProposal(raw_constraints=RawConstraints(preferences=["安静"]))
        )
        self.assertEqual(proposal.act.kind, "create_plan")
        with self.assertRaises(ValueError):
            TurnProposal.model_validate(
                {"primary_intent": "plan_outing", "conversation_command": {}}
            )
        with self.assertRaises(ValueError):
            TurnProposal.model_validate(
                {
                    "act": {
                        "kind": "replace_stop",
                        "target": {
                            "raw_text": "活动",
                            "resource_id": "server-owned",
                        },
                    }
                }
            )
        with self.assertRaises(ValueError):
            TurnProposal.model_validate(
                {
                    "act": {
                        "kind": "create_plan",
                        "raw_constraints": {"location_text": "望京"},
                    }
                }
            )

    def test_location_semantics_are_explicit_in_turn_wire_contract(self) -> None:
        proposal = TurnProposal.model_validate(
            {
                "act": {
                    "kind": "create_plan",
                    "raw_constraints": {
                        "origin_text": "望京",
                        "planning_area_text": "朝阳",
                    },
                }
            }
        )
        self.assertEqual(proposal.act.raw_constraints.origin_text, "望京")
        self.assertEqual(proposal.act.raw_constraints.planning_area_text, "朝阳")

    def test_create_and_patch_compile_to_request_actions(self) -> None:
        create = TurnCompiler.compile(
            TurnProposal(
                act=CreatePlanProposal(
                    raw_constraints=RawConstraints(preferences=["浪漫"]),
                    time_proposals=(
                        PeriodProposal(
                            event="trip",
                            period=TimeScope.EVENING,
                            evidence="晚上",
                        ),
                    ),
                )
            ),
        )
        self.assertIsInstance(create.action, ApplyRequestPatch)
        self.assertEqual(create.action.mode, "create")
        self.assertEqual(create.interpretation.primary_intent, Intent.PLAN_OUTING)

        patch = TurnCompiler.compile(
            TurnProposal(
                act=PatchConstraintsProposal(
                    constraint_patch=ConstraintPatch(preferences=("安静",))
                )
            ),
            context=_planned_context(),
        )
        self.assertIsInstance(patch.action, ApplyRequestPatch)
        self.assertEqual(patch.action.mode, "update")
        self.assertEqual(patch.action.constraint_patch.preferences, ("安静",))

    def test_query_weather_and_chitchat_have_explicit_compiled_actions(self) -> None:
        weather = TurnCompiler.compile(
            TurnProposal(act=CheckWeatherProposal()),
        )
        self.assertIsInstance(weather.action, AnswerQuery)
        self.assertEqual(weather.action.query_kind, "weather")

        query = TurnCompiler.compile(
            TurnProposal(act=QueryPlanProposal(query="当前选中方案是什么？")),
            context=_planned_context(),
        )
        self.assertIsInstance(query.action, AnswerQuery)
        self.assertEqual(query.action.query_kind, "plan")

        chitchat = TurnCompiler.compile(
            TurnProposal(act=ChitchatProposal()),
        )
        self.assertEqual(chitchat.action.kind, "no_action")

    def test_replace_compiles_alias_index_and_unresolved_target(self) -> None:
        second = TurnCompiler.compile(
            TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(raw_text="第二站"),
                )
            ),
            context=_planned_context(),
        )
        self.assertIsInstance(second.action, ModifySelectedPlan)
        self.assertEqual(second.action.command.target.stop_index, 1)

        ambiguous = TurnCompiler.compile(
            TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(raw_text="那个地方"),
                )
            ),
            context=_planned_context(),
        )
        self.assertIsInstance(ambiguous.action, NeedsClarification)
        self.assertEqual(ambiguous.action.field, "target_reference")
        self.assertEqual(ambiguous.action.pending_modification.target_raw_text, "那个地方")

        no_selection = TurnCompiler.compile(
            TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(role=StopRole.ACTIVITY, raw_text="活动"),
                )
            ),
        )
        self.assertIsInstance(no_selection.action, NeedsClarification)
        self.assertEqual(no_selection.action.field, "selected_plan_id")

        blocked_empty = TurnCompiler.compile(
            TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(role=StopRole.ACTIVITY, raw_text="活动"),
                )
            ),
            context=DecisionContext(allowed_actions=("create_plan", "chitchat")),
        )
        self.assertIsInstance(blocked_empty.action, NoAction)
        self.assertEqual(blocked_empty.action.reason, "unsupported")

    def test_compiler_uses_context_allowlist_and_selected_stop_bounds(self) -> None:
        context = DecisionContext(
            current_request=RequestContextSummary(revision=2),
            selected_plan=SelectedPlanContextSummary(
                stops=(
                    StopContextSummary(
                        stop_index=0,
                        role="activity",
                        name="安静公园",
                    ),
                )
            ),
            allowed_actions=("patch_constraints", "replace_stop"),
        )

        blocked_create = TurnCompiler.compile(
            TurnProposal(act=CreatePlanProposal()),
            context=context,
        )
        self.assertIsInstance(blocked_create.action, NoAction)
        self.assertEqual(blocked_create.action.reason, "unsupported")

        out_of_bounds = TurnCompiler.compile(
            TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(stop_index=1, raw_text="第二站"),
                )
            ),
            context=context,
        )
        self.assertIsInstance(out_of_bounds.action, NeedsClarification)
        self.assertEqual(out_of_bounds.action.field, "target_reference")

    def test_patch_requires_current_request_in_context(self) -> None:
        compilation = TurnCompiler.compile(
            TurnProposal(
                act=PatchConstraintsProposal(
                    constraint_patch=ConstraintPatch(preferences=("安静",))
                )
            ),
            context=DecisionContext(allowed_actions=("create_plan",)),
        )
        self.assertIsInstance(compilation.action, NeedsClarification)
        self.assertEqual(compilation.action.field, "request_lifecycle")
        self.assertEqual(compilation.action.issue_kind, "action")

    def test_empty_session_retries_an_illegal_patch_as_create_once(self) -> None:
        model = FakeStructuredModel(
            [
                TurnProposal(
                    act=PatchConstraintsProposal(
                        constraint_patch=ConstraintPatch(preferences=("安静",))
                    )
                ),
                TurnProposal(
                    act=CreatePlanProposal(
                        raw_constraints=RawConstraints(preferences=["安静"])
                    )
                ),
            ]
        )
        result, runtime = TurnInterpreter(model).interpret_with_runtime(
            "还是安静点吧",
            RouterContext(
                current_date=date(2026, 8, 12),
                decision_context=DecisionContext(allowed_actions=("create_plan", "check_weather", "chitchat")),
            ),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertIsInstance(result, Interpretation)
        self.assertEqual(runtime.attempts, 2)
        self.assertEqual(runtime.fallback_reason, None)

    def test_empty_session_does_not_silently_convert_repeated_patch(self) -> None:
        model = FakeStructuredModel(
            [
                TurnProposal(act=PatchConstraintsProposal()),
                TurnProposal(act=PatchConstraintsProposal()),
            ]
        )
        result, runtime = TurnInterpreter(model).interpret_with_runtime(
            "预算再低一点",
            RouterContext(
                current_date=date(2026, 8, 12),
                decision_context=DecisionContext(allowed_actions=("create_plan", "check_weather", "chitchat")),
            ),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result.primary_intent, Intent.CLARIFY)
        self.assertTrue(result.requires_clarification)
        self.assertEqual(runtime.fallback_reason, "action_not_allowed")

    def test_deterministic_refine_adapter_preserves_unresolved_modification(self) -> None:
        compilation = TurnCompiler.compile(
            TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(raw_text="那个地方"),
                )
            ),
            context=_planned_context(),
        )
        self.assertIsInstance(compilation.action, NeedsClarification)
        self.assertEqual(compilation.action.field, "target_reference")

    def test_raw_only_target_is_resolved_or_becomes_needs_input(self) -> None:
        model = FakeStructuredModel(
            [
                {
                    "act": {
                        "kind": "replace_stop",
                        "target": {"raw_text": "那个地方"},
                    }
                }
            ]
        )
        result, runtime = TurnInterpreter(model).interpret_with_runtime(
            "把那个地方换一下",
            RouterContext(
                current_date=date(2026, 8, 12),
                has_plans=True,
                has_selected_plan=True,
            ),
        )
        self.assertIsInstance(result, Interpretation)
        self.assertEqual(runtime.diagnostic_code, "target_resolution_required")
        compiled, _ = TurnInterpreter._validate_with_diagnostic(
            {"act": {"kind": "replace_stop", "target": {"raw_text": "活动"}}},
            decision_context=_planned_context(),
        )
        self.assertIsInstance(compiled.action, ModifySelectedPlan)

    def test_runtime_reports_provider_token_usage_and_wire_version(self) -> None:
        expected = TurnProposal(
            act=CreatePlanProposal(raw_constraints=RawConstraints(preferences=["安静"]))
        )
        model = FakeStructuredModel(
            [
                {
                    "raw": SimpleNamespace(
                        usage_metadata={"input_tokens": 21, "output_tokens": 8}
                    ),
                    "parsed": expected,
                    "parsing_error": None,
                }
            ]
        )
        result, runtime = TurnInterpreter(model, model_name="fake-model").interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 9, 11)),
        )
        self.assertEqual(result.raw_constraints.preferences, ["安静"])
        self.assertEqual(runtime.input_tokens, 21)
        self.assertEqual(runtime.output_tokens, 8)
        self.assertEqual(runtime.wire_schema_version, "turn-proposal.v2")
        self.assertEqual(runtime.prompt_version, "turn-interpreter.v4")

    def test_prompt_contains_bounded_context_without_provider_facts(self) -> None:
        model = FakeStructuredModel([TurnProposal(act=ChitchatProposal())])
        TurnInterpreter(model).interpret_with_runtime(
            "你好",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        prompt = "\n".join(str(message.content) for message in model.calls[0])
        self.assertIn("2026-08-12", prompt)
        self.assertIn("TurnProposal", prompt)
        self.assertNotIn("天气事实", prompt)
        self.assertNotIn("resource_id", prompt)

    def test_retries_once_then_returns_safe_clarification(self) -> None:
        model = FakeStructuredModel([{"invalid": True}, {"still_invalid": True}])
        outcome = TurnInterpreter(model).interpret_with_runtime(
            "随便安排一下",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(outcome.interpretation.primary_intent, Intent.CLARIFY)
        self.assertTrue(outcome.interpretation.requires_clarification)
        self.assertIsInstance(outcome.action, NeedsClarification)
        self.assertEqual(outcome.action.field, "request_rephrase")
        self.assertEqual(outcome.action.issue_kind, "constraint")
        self.assertEqual(outcome.runtime.fallback_reason, "invalid_output")
        self.assertIn("TurnProposal", "\n".join(str(m.content) for m in model.calls[1]))

    def test_classifies_provider_failure_without_retry(self) -> None:
        model = FakeStructuredModel([ConnectionError("connection refused")])
        _, runtime = TurnInterpreter(model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        self.assertEqual(runtime.fallback_reason, "network_error")
        self.assertEqual(len(model.calls), 1)

    def test_invalid_legacy_wire_is_rejected_with_safe_path(self) -> None:
        model = FakeStructuredModel(
            [
                {"primary_intent": "plan_outing", "inferred_fields": ["party"]},
                {"primary_intent": "plan_outing", "inferred_fields": ["party"]},
            ]
        )
        _, runtime = TurnInterpreter(model).interpret_with_runtime(
            "明天和对象约会",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        self.assertEqual(runtime.diagnostic_code, "pydantic_validation_failed")
        self.assertIn("act", runtime.diagnostic_paths)
        self.assertNotIn("inferred field has no extracted value", runtime.model_dump_json())

    def test_structured_provider_parser_diagnostic_does_not_expose_payload(self) -> None:
        raw = SimpleNamespace(content="private", tool_calls=[], invalid_tool_calls=[])
        model = FakeStructuredModel(
            [
                {"raw": raw, "parsed": None, "parsing_error": ValueError("parser")},
                {"raw": raw, "parsed": None, "parsing_error": ValueError("parser")},
            ]
        )
        _, runtime = TurnInterpreter(model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        self.assertEqual(runtime.diagnostic_code, "missing_tool_call")
        self.assertNotIn("private", runtime.model_dump_json())


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.constraints import (
    CommandOperation,
    ConversationCommand,
    DateReference,
    Intent,
    Interpretation,
    RawConstraints,
    TargetReference,
    TimeWindow,
    TimeScope,
    Weekday,
)
from app.services import router_extractor as router_extractor_module
from app.services.router_extractor import (
    ConversationCommandProposal,
    LlmInterpretationProposal,
    RouterContext,
    RouterExtractor,
    build_default_turn_interpreter,
    compile_llm_interpretation_proposal,
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


class RouterExtractorTest(unittest.TestCase):
    def test_production_builder_uses_schema_aware_function_calling(self) -> None:
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
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})
        chat_openai.return_value.with_structured_output.assert_called_once_with(
            LlmInterpretationProposal,
            method="function_calling",
            include_raw=True,
        )

    def test_llm_wire_proposal_excludes_runtime_and_application_fields(self) -> None:
        fields = set(LlmInterpretationProposal.model_fields)
        self.assertNotIn("intent_scores", fields)
        self.assertNotIn("extraction_confidence", fields)
        self.assertNotIn("inferred_fields", fields)
        self.assertNotIn("reply", fields)
        self.assertNotIn("requires_clarification", fields)
        self.assertNotIn("target_reference", fields)

        command_fields = set(ConversationCommandProposal.model_fields)
        self.assertNotIn("base_plan_id", command_fields)
        self.assertNotIn("base_plan_version_id", command_fields)
        self.assertNotIn("confidence", command_fields)

    def test_llm_wire_proposal_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValueError):
            LlmInterpretationProposal.model_validate(
                {"primary_intent": "plan_outing", "reply": "不要从模型接收回复"}
            )
        with self.assertRaises(ValueError):
            ConversationCommandProposal.model_validate(
                {"operation": "replace", "base_plan_id": "server-owned"}
            )

    def test_llm_wire_proposal_compiles_party_and_command_without_anchors(self) -> None:
        proposal = LlmInterpretationProposal.model_validate(
            {
                "primary_intent": "refine_plan",
                "raw_constraints": {"members": ["女朋友"]},
                "conversation_command": {
                    "operation": "replace",
                    "target": {"role": "activity", "raw_text": "活动"},
                    "locked_targets": [
                        {"resource_type": "restaurant", "raw_text": "餐厅"}
                    ],
                    "constraint_patch": {"prefer_shorter_travel": True},
                    "evidence": {"replace": "活动换近一点"},
                },
                "evidence_map": {"party": "女朋友"},
            }
        )
        compiled = compile_llm_interpretation_proposal(proposal)
        self.assertEqual(compiled.raw_constraints.members, ["女朋友"])
        self.assertEqual(compiled.intent_scores, {Intent.REFINE_PLAN: 1.0})
        self.assertEqual(compiled.extraction_confidence, {})
        self.assertEqual(compiled.inferred_fields, set())
        self.assertEqual(compiled.conversation_command.base_plan_id, None)
        self.assertEqual(compiled.conversation_command.base_plan_version_id, None)
        self.assertEqual(compiled.conversation_command.target.resource_id, None)

    def test_plain_date_evidence_compiles_without_model_confidence(self) -> None:
        proposal = LlmInterpretationProposal.model_validate(
            {
                "primary_intent": "plan_outing",
                "raw_constraints": {
                    "date_reference": "tomorrow",
                    "date_text": "明天",
                },
                "evidence_map": {"date_reference": "明天"},
            }
        )
        compiled = compile_llm_interpretation_proposal(proposal)
        self.assertEqual(compiled.raw_constraints.date_reference.value, "tomorrow")

    def test_runtime_reports_provider_token_usage(self) -> None:
        expected = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.97},
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

        result, runtime = RouterExtractor(model, model_name="fake-model").interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 9, 11)),
        )

        self.assertEqual(result, expected)
        self.assertEqual(runtime.input_tokens, 21)
        self.assertEqual(runtime.output_tokens, 8)
        self.assertEqual(runtime.wire_schema_version, "llm-interpretation-proposal.v1")
        self.assertEqual(runtime.prompt_version, "turn-interpreter.v2")

    def test_returns_structured_interpretation_without_environment_facts(self) -> None:
        expected = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.97},
            raw_constraints=RawConstraints(
                date_text="今天",
                time_text="下午",
                max_distance_text="别太远",
            ),
            evidence_map={"date_text": "今天", "time_text": "下午"},
        )
        model = FakeStructuredModel([expected])
        router = RouterExtractor(model)

        result = router.interpret(
            "今天下午出去玩，别太远",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(result, expected)
        prompt = "\n".join(str(message.content) for message in model.calls[0])
        self.assertIn("2026-08-12", prompt)
        self.assertNotIn("天气", prompt)
        self.assertNotIn("默认位置", prompt)
        self.assertNotIn("默认预算", prompt)

    def test_retries_once_then_returns_explicit_clarification(self) -> None:
        model = FakeStructuredModel([{"invalid": True}, {"still_invalid": True}])
        router = RouterExtractor(model)

        result = router.interpret(
            "随便安排一下",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result.primary_intent, Intent.CLARIFY)
        self.assertTrue(result.requires_clarification)
        self.assertIn("重新描述", result.reply)
        retry_prompt = "\n".join(str(message.content) for message in model.calls[1])
        self.assertIn("invalid_output", retry_prompt)

    def test_classifies_provider_failure_separately_from_invalid_output(self) -> None:
        model = FakeStructuredModel([ConnectionError("connection refused")])
        _, runtime = RouterExtractor(model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(runtime.fallback_reason, "network_error")
        self.assertEqual(len(model.calls), 1)

    def test_classifies_missing_tool_call_without_exposing_provider_payload(self) -> None:
        model = FakeStructuredModel(
            [
                {"raw": SimpleNamespace(content="not a tool call"), "parsed": None, "parsing_error": ValueError("parser")},
                {"raw": SimpleNamespace(content="still not a tool call"), "parsed": None, "parsing_error": ValueError("parser")},
            ]
        )

        _, runtime = RouterExtractor(model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(runtime.fallback_reason, "invalid_output")
        self.assertEqual(runtime.diagnostic_code, "missing_tool_call")
        self.assertEqual(runtime.diagnostic_paths, ())
        self.assertEqual(runtime.diagnostic_error_types, ())

    def test_classifies_invalid_json_tool_arguments(self) -> None:
        raw = SimpleNamespace(
            content="",
            tool_calls=[],
            invalid_tool_calls=[{"name": "Interpretation", "args": "{"}],
        )
        model = FakeStructuredModel(
            [
                {"raw": raw, "parsed": None, "parsing_error": ValueError("invalid json")},
                {"raw": raw, "parsed": None, "parsing_error": ValueError("invalid json")},
            ]
        )

        _, runtime = RouterExtractor(model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(runtime.diagnostic_code, "invalid_json_arguments")
        self.assertNotIn("invalid json", runtime.model_dump_json())

    def test_classifies_provider_parser_error_when_tool_call_exists(self) -> None:
        raw = SimpleNamespace(content="", tool_calls=[{"name": "Interpretation"}], invalid_tool_calls=[])
        model = FakeStructuredModel(
            [
                {"raw": raw, "parsed": None, "parsing_error": RuntimeError("parser")},
                {"raw": raw, "parsed": None, "parsing_error": RuntimeError("parser")},
            ]
        )

        _, runtime = RouterExtractor(model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(runtime.diagnostic_code, "provider_parsing_error")

    def test_classifies_field_and_cross_field_pydantic_errors_safely(self) -> None:
        field_model = FakeStructuredModel(
            [
                {"primary_intent": "not-an-intent", "intent_scores": {}},
                {"primary_intent": "still-not-an-intent", "intent_scores": {}},
            ]
        )
        _, field_runtime = RouterExtractor(field_model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        self.assertEqual(field_runtime.diagnostic_code, "pydantic_validation_failed")
        self.assertIn("primary_intent", field_runtime.diagnostic_paths)
        self.assertTrue(field_runtime.diagnostic_error_types)

        cross_model = FakeStructuredModel(
            [
                {
                    "primary_intent": "plan_outing",
                    "intent_scores": {"plan_outing": 1.0},
                    "raw_constraints": {"date_reference": "weekday"},
                },
                {
                    "primary_intent": "plan_outing",
                    "intent_scores": {"plan_outing": 1.0},
                    "raw_constraints": {"date_reference": "weekday"},
                },
            ]
        )
        _, cross_runtime = RouterExtractor(cross_model).interpret_with_runtime(
            "明天出去玩",
            RouterContext(current_date=date(2026, 8, 12)),
        )
        self.assertEqual(cross_runtime.diagnostic_code, "weekday_missing")
        self.assertIn("raw_constraints", cross_runtime.diagnostic_paths)
        repair_prompt = "\n".join(
            str(message.content) for message in cross_model.calls[1]
        )
        self.assertIn("诊断码=weekday_missing", repair_prompt)
        self.assertIn("字段路径=raw_constraints", repair_prompt)
        self.assertNotIn("Pydantic错误类型", repair_prompt)
        self.assertNotIn("date_reference=weekday requires weekday", repair_prompt)

    def test_cross_field_rules_expose_stable_codes(self) -> None:
        cases = [
            (
                "weekday_reference_mismatch",
                lambda: RawConstraints(
                    date_reference=DateReference.TOMORROW,
                    weekday=Weekday.SATURDAY,
                ),
            ),
            (
                "weekday_missing",
                lambda: RawConstraints(date_reference=DateReference.WEEKDAY),
            ),
            (
                "absolute_date_reference_mismatch",
                lambda: RawConstraints(
                    date_reference=DateReference.TOMORROW,
                    absolute_date=date(2026, 9, 20),
                ),
            ),
            (
                "absolute_date_missing",
                lambda: RawConstraints(date_reference=DateReference.ABSOLUTE),
            ),
            (
                "explicit_time_window_order_invalid",
                lambda: RawConstraints(
                    explicit_time_window=TimeWindow(start="18:00", end="17:00")
                ),
            ),
            (
                "replace_target_missing",
                lambda: ConversationCommand(operation=CommandOperation.REPLACE),
            ),
            (
                "target_reference_missing",
                lambda: TargetReference(raw_text="那个地方"),
            ),
        ]

        for expected_code, builder in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ValueError) as context:
                    builder()
                errors = context.exception.errors(
                    include_url=False,
                    include_context=False,
                )
                self.assertEqual(errors[0]["type"], expected_code)

    def test_inferred_and_temporal_contracts_have_stable_codes(self) -> None:
        inferred_cases = [
            (
                "inferred_value_missing",
                {"party"},
                {},
                {},
            ),
            (
                "inferred_evidence_missing",
                {"date_reference"},
                {},
                {"date_reference": 0.9},
            ),
            (
                "inferred_confidence_missing",
                {"date_reference"},
                {"date_reference": "明天"},
                {},
            ),
        ]
        for expected_code, inferred, evidence, confidence in inferred_cases:
            with self.subTest(expected_code=expected_code):
                kwargs = {
                    "primary_intent": Intent.PLAN_OUTING,
                    "intent_scores": {Intent.PLAN_OUTING: 1.0},
                    "inferred_fields": inferred,
                    "evidence_map": evidence,
                    "extraction_confidence": confidence,
                }
                if expected_code != "inferred_value_missing":
                    kwargs["raw_constraints"] = RawConstraints(
                        date_reference=DateReference.TOMORROW,
                        date_text="明天",
                    )
                with self.assertRaises(ValueError) as context:
                    Interpretation(**kwargs)
                self.assertEqual(
                    context.exception.errors(
                        include_url=False,
                        include_context=False,
                    )[0]["type"],
                    expected_code,
                )

        with self.assertRaises(ValueError) as context:
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                inferred_fields={"party"},
            )
        error = context.exception.errors(
            include_url=False,
            include_context=True,
        )[0]
        self.assertEqual(error["type"], "inferred_value_missing")
        self.assertEqual(error["ctx"]["field"], "party")

    def test_inferred_contract_diagnostic_exposes_only_safe_field_path(self) -> None:
        model = FakeStructuredModel(
            [
                {
                    "primary_intent": "plan_outing",
                    "intent_scores": {"plan_outing": 1.0},
                    "inferred_fields": ["party"],
                },
                {
                    "primary_intent": "plan_outing",
                    "intent_scores": {"plan_outing": 1.0},
                    "inferred_fields": ["party"],
                },
            ]
        )

        _, runtime = RouterExtractor(model).interpret_with_runtime(
            "明天和对象约会",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(runtime.diagnostic_code, "inferred_value_missing")
        self.assertEqual(runtime.diagnostic_paths, ("inferred_fields.party",))
        self.assertNotIn("inferred field has no extracted value", runtime.model_dump_json())

    def test_inferred_party_accepts_member_relationship_without_count(self) -> None:
        for member, evidence in (("女朋友", "跟女朋友约会"), ("父母", "带父母出去")):
            with self.subTest(member=member):
                interpretation = Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    raw_constraints=RawConstraints(members=[member]),
                    inferred_fields={"party"},
                    evidence_map={"party": evidence},
                    extraction_confidence={"party": 0.8},
                )
                self.assertEqual(interpretation.raw_constraints.adults, None)
                self.assertEqual(interpretation.raw_constraints.members, [member])

    def test_inferred_party_still_requires_a_value(self) -> None:
        with self.assertRaises(ValueError) as context:
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                inferred_fields={"party"},
                evidence_map={"party": "约会"},
                extraction_confidence={"party": 0.8},
            )
        error = context.exception.errors(include_url=False, include_context=True)[0]
        self.assertEqual(error["type"], "inferred_value_missing")
        self.assertEqual(error["ctx"]["field"], "party")

    def test_inferred_party_with_members_requires_evidence(self) -> None:
        with self.assertRaises(ValueError) as context:
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(members=["女朋友"]),
                inferred_fields={"party"},
                extraction_confidence={"party": 0.8},
            )
        error = context.exception.errors(include_url=False, include_context=True)[0]
        self.assertEqual(error["type"], "inferred_evidence_missing")
        self.assertEqual(error["ctx"]["field"], "party")

    def test_inferred_party_with_members_requires_confidence(self) -> None:
        with self.assertRaises(ValueError) as context:
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(members=["女朋友"]),
                inferred_fields={"party"},
                evidence_map={"party": "女朋友"},
            )
        error = context.exception.errors(include_url=False, include_context=True)[0]
        self.assertEqual(error["type"], "inferred_confidence_missing")
        self.assertEqual(error["ctx"]["field"], "party")

        with self.assertRaises(ValueError) as context:
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_reference=DateReference.TOMORROW,
                ),
                extraction_confidence={"date_reference": 0.9},
            )
        self.assertEqual(
            context.exception.errors(
                include_url=False,
                include_context=False,
            )[0]["type"],
            "temporal_evidence_missing",
        )

        temporal_without_confidence = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_reference=DateReference.TOMORROW,
            ),
            evidence_map={"date_reference": "明天"},
            extraction_confidence={},
        )
        self.assertEqual(
            temporal_without_confidence.raw_constraints.date_reference,
            DateReference.TOMORROW,
        )

    def test_runtime_trace_keeps_only_stable_diagnostic_details(self) -> None:
        model = FakeStructuredModel(
            [
                {
                    "primary_intent": "plan_outing",
                    "intent_scores": {"plan_outing": 1.0},
                    "raw_constraints": {"date_reference": "weekday"},
                },
                {
                    "primary_intent": "plan_outing",
                    "intent_scores": {"plan_outing": 1.0},
                    "raw_constraints": {"date_reference": "weekday"},
                },
            ]
        )
        _, runtime = RouterExtractor(model).interpret_with_runtime(
            "用户私密原话不应进入诊断",
            RouterContext(current_date=date(2026, 8, 12)),
        )

        self.assertEqual(runtime.diagnostic_code, "weekday_missing")
        self.assertIn("raw_constraints", runtime.diagnostic_paths)
        self.assertIn("weekday_missing", runtime.diagnostic_error_types)
        serialized = runtime.model_dump_json()
        self.assertNotIn("用户私密原话", serialized)
        self.assertNotIn("date_reference=weekday requires weekday", serialized)
        self.assertNotIn("primary_intent", serialized)

    def test_decodes_provider_json_string_for_nested_command_then_validates_domain(self) -> None:
        command = {
            "operation": "replace",
            "target": {"role": "activity", "raw_text": "活动"},
            "constraint_patch": {"prefer_shorter_travel": True},
        }
        model = FakeStructuredModel(
            [
                {
                    "primary_intent": "refine_plan",
                    "intent_scores": {"refine_plan": 1.0},
                    "conversation_command": json.dumps(command, ensure_ascii=False),
                }
            ]
        )

        result, runtime = RouterExtractor(model).interpret_with_runtime(
            "餐厅保留，只把活动换近一点",
            RouterContext(
                current_date=date(2026, 8, 12),
                has_plans=True,
                has_selected_plan=True,
            ),
        )

        self.assertIsNone(runtime.fallback_reason)
        self.assertEqual(result.conversation_command.operation.value, "replace")
        self.assertTrue(result.conversation_command.constraint_patch.prefer_shorter_travel)

    def test_structured_temporal_values_require_evidence_and_confidence(self) -> None:
        with self.assertRaises(ValueError):
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(
                    date_reference=DateReference.TODAY,
                    time_scope=TimeScope.EVENING,
                ),
            )

        valid = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_text="今晚",
                date_reference=DateReference.TODAY,
                time_text="今晚",
                time_scope=TimeScope.EVENING,
            ),
            evidence_map={"date_text": "今晚", "time_text": "今晚"},
            extraction_confidence={"date_text": 1.0, "time_text": 1.0},
        )
        self.assertEqual(valid.raw_constraints.date_reference, DateReference.TODAY)


if __name__ == "__main__":
    unittest.main()

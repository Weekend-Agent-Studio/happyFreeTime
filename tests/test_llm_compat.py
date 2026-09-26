import unittest

from app.domain.constraints import Interpretation
from app.services.llm_compat import thinking_extra_body, structured_output_schema


class LlmCompatTest(unittest.TestCase):
    def test_qwen_uses_qwen_thinking_switch(self) -> None:
        self.assertEqual(
            thinking_extra_body("Qwen3.7-Flash"),
            {"enable_thinking": False},
        )

    def test_non_qwen_keeps_existing_thinking_contract(self) -> None:
        self.assertEqual(
            thinking_extra_body("deepseek-v4-flash"),
            {"thinking": {"type": "disabled"}},
        )

    def test_model_name_matching_is_whitespace_and_case_insensitive(self) -> None:
        self.assertEqual(
            thinking_extra_body("  QWEN3.7-FLASH  "),
            {"enable_thinking": False},
        )

    def test_qwen_wire_schema_removes_only_unsupported_keywords(self) -> None:
        schema = structured_output_schema("qwen3.7-flash", Interpretation)

        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "Interpretation")

        def walk(value: object) -> list[str]:
            if isinstance(value, dict):
                keys = list(value)
                for item in value.values():
                    keys.extend(walk(item))
                return keys
            if isinstance(value, list):
                keys: list[str] = []
                for item in value:
                    keys.extend(walk(item))
                return keys
            return []

        self.assertFalse(
            set(walk(schema))
            & {"propertyNames", "patternProperties", "uniqueItems", "contains", "minContains", "maxContains"}
        )

    def test_non_qwen_keeps_the_pydantic_schema_object(self) -> None:
        self.assertIs(
            structured_output_schema("deepseek-v4-flash", Interpretation),
            Interpretation,
        )

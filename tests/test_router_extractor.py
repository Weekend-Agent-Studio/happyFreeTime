import unittest
from datetime import date

from app.domain.constraints import Intent, Interpretation, RawConstraints
from app.services.router_extractor import RouterContext, RouterExtractor


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
        model = FakeStructuredModel([ValueError("invalid output"), ValueError("still invalid")])
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
        self.assertIn("invalid output", retry_prompt)


if __name__ == "__main__":
    unittest.main()

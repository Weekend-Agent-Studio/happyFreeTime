import unittest
from datetime import date

from app.domain.constraints import DateReference, Intent, StopRole, TimeScope, Weekday
from app.services.demo_router import DemoRouter
from app.services.router_extractor import RouterContext


class DemoRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = DemoRouter()
        self.context = RouterContext(current_date=date(2026, 8, 12))

    def test_extracts_demo_constraints_deterministically(self) -> None:
        result = self.router.interpret(
            "今天下午和朋友出去玩，人均150，别太远",
            self.context,
        )

        self.assertEqual(result.primary_intent, Intent.PLAN_OUTING)
        self.assertIsNone(result.raw_constraints.adults)
        self.assertEqual(result.raw_constraints.budget_per_person, 150)
        self.assertEqual(result.raw_constraints.max_distance_text, "别太远")

    def test_follow_up_budget_is_extracted_from_combined_turn(self) -> None:
        result = self.router.interpret(
            "今天下午出去玩，别超预算\n用户补充：人均200",
            self.context,
        )

        self.assertTrue(result.raw_constraints.strict_budget)
        self.assertEqual(result.raw_constraints.budget_per_person, 200)

    def test_extracts_return_by_deadline(self) -> None:
        result = self.router.interpret(
            "下午出去玩，最晚18:00到家",
            self.context,
        )

        self.assertEqual(result.raw_constraints.return_by, "18:00")
        self.assertIsNotNone(result.raw_constraints.return_by_text)

    def test_preserves_chinese_return_deadline_for_enrichment(self) -> None:
        result = self.router.interpret(
            "明天下午三点二十准时出发，晚上八点前回来",
            self.context,
        )

        self.assertEqual(result.raw_constraints.return_by_text, "晚上八点前回来")
        self.assertIsNone(result.raw_constraints.return_by)

    def test_extracts_explicit_availability_confirmation_requirement(self) -> None:
        result = self.router.interpret(
            "只安排一家晚饭，必须营业且确认有位",
            self.context,
        )

        self.assertTrue(result.raw_constraints.require_availability_confirmation)
        self.assertEqual(
            result.evidence_map["require_availability_confirmation"],
            "确认有位",
        )

    def test_keeps_departure_evidence_separate_from_return_deadline(self) -> None:
        result = self.router.interpret(
            "明天下午两点半准时出发，18:00 前回家",
            self.context,
        )

        self.assertEqual(result.raw_constraints.departure_at_text, "下午两点半准时出发")
        self.assertIsNone(result.raw_constraints.departure_at)
        self.assertEqual(result.raw_constraints.return_by, "18:00")

    def test_extracts_total_distance_with_its_original_evidence(self) -> None:
        result = self.router.interpret(
            "今天下午出去玩，全程不超过10公里",
            self.context,
        )

        self.assertEqual(result.raw_constraints.total_distance_km, 10.0)
        self.assertEqual(result.raw_constraints.total_distance_text, "全程不超过10公里")

    def test_preserves_unresolved_return_deadline_for_the_question_gate(self) -> None:
        result = self.router.interpret(
            "下午出去玩，最晚十八点回家",
            self.context,
        )

        self.assertEqual(result.raw_constraints.return_by_text, "最晚十八点回家")
        self.assertIsNone(result.raw_constraints.return_by)

    def test_preserves_unresolved_date_phrase_for_the_question_gate(self) -> None:
        result = self.router.interpret(
            "等忙完那天带家里人出去",
            self.context,
        )

        self.assertEqual(result.raw_constraints.date_text, "等忙完那天")
        self.assertIsNone(result.raw_constraints.date_reference)
        self.assertEqual(result.evidence_map["date_text"], "等忙完那天")

    def test_parses_chinese_strict_budget_amount_without_inventing_party_size(self) -> None:
        result = self.router.interpret(
            "人均最多十元而且绝对不能超，安排活动和晚饭",
            self.context,
        )

        self.assertEqual(result.raw_constraints.budget_per_person, 10)
        self.assertTrue(result.raw_constraints.strict_budget)
        self.assertEqual(result.raw_constraints.members, [])

    def test_extracts_dinner_only_structure_with_evidence(self) -> None:
        for text in (
            "明天只安排一家晚饭",
            "明天就吃个晚饭",
            "明天只去一家餐厅吃晚饭",
        ):
            result = self.router.interpret(text, self.context)

            self.assertEqual(result.raw_constraints.exact_stop_count, 1)
            self.assertEqual(result.raw_constraints.required_stop_roles, (StopRole.DINNER,))
            self.assertTrue(result.evidence_map["exact_stop_count"])
            self.assertIn("晚", result.evidence_map["required_stop_roles"])

    def test_extracts_lunch_only_structure_with_evidence(self) -> None:
        for text in (
            "明天只安排一顿午饭",
            "明天就吃个午餐",
            "明天只去一家餐厅吃午饭",
        ):
            result = self.router.interpret(text, self.context)

            self.assertEqual(result.raw_constraints.exact_stop_count, 1)
            self.assertEqual(result.raw_constraints.required_stop_roles, (StopRole.LUNCH,))
            self.assertTrue(result.evidence_map["exact_stop_count"])
            self.assertIn("午", result.evidence_map["required_stop_roles"])

    def test_extracts_activity_only_with_explicit_quantity_or_no_meal(self) -> None:
        for text in (
            "下午只去一个活动，不安排吃饭",
            "只看一个展",
            "只安排活动，不安排吃饭",
        ):
            result = self.router.interpret(text, self.context)

            self.assertEqual(result.raw_constraints.exact_stop_count, 1)
            self.assertEqual(result.raw_constraints.required_stop_roles, (StopRole.ACTIVITY,))
            self.assertTrue(result.evidence_map["exact_stop_count"])
            self.assertTrue(result.evidence_map["required_stop_roles"])

    def test_dinner_words_without_exclusivity_do_not_become_dinner_only(self) -> None:
        for text in (
            "想吃晚饭",
            "晚饭后散散步",
            "只安排看展，晚饭自己解决",
        ):
            result = self.router.interpret(text, self.context)

            self.assertIsNone(result.raw_constraints.exact_stop_count)
            self.assertEqual(result.raw_constraints.required_stop_roles, ())

    def test_extracts_explicit_multi_role_structure_and_exclusive_count(self) -> None:
        for text, expected_count in (
            ("安排活动和晚饭", None),
            ("先逛展再吃晚饭", None),
            ("只安排活动和晚饭", 2),
            ("只安排一顿午饭和活动", 2),
        ):
            result = self.router.interpret(text, self.context)

            self.assertEqual(result.raw_constraints.exact_stop_count, expected_count)
            expected_roles = (
                (StopRole.LUNCH, StopRole.ACTIVITY)
                if "午饭" in text
                else (StopRole.ACTIVITY, StopRole.DINNER)
            )
            self.assertEqual(
                result.raw_constraints.required_stop_roles,
                expected_roles,
            )
            self.assertTrue(
                any(
                    term in result.evidence_map["required_stop_roles"]
                    for term in ("活动", "逛展", "看展")
                )
            )
            if "午饭" in text:
                self.assertIn("午饭", result.evidence_map["required_stop_roles"])
            else:
                self.assertIn("晚饭", result.evidence_map["required_stop_roles"])

    def test_negated_or_temporal_meal_mentions_are_not_required_roles(self) -> None:
        for text in (
            "不安排活动和晚饭",
            "不想安排活动和晚饭",
            "安排活动，晚饭前后一定要回家",
            "安排活动，晚饭后散散步",
        ):
            result = self.router.interpret(text, self.context)

            self.assertEqual(result.raw_constraints.required_stop_roles, ())
            self.assertIsNone(result.raw_constraints.exact_stop_count)

    def test_all_day_language_is_preserved_as_a_finite_time_scope(self) -> None:
        result = self.router.interpret(
            "明天和女朋友玩一整天，不希望太累",
            self.context,
        )

        self.assertEqual(result.raw_constraints.time_text, "一整天")
        self.assertEqual(result.raw_constraints.time_scope.value, "all_day")

    def test_extracts_bounded_temporal_contract_for_compound_phrases(self) -> None:
        result = self.router.interpret("今晚只安排一家晚饭", self.context)

        raw = result.raw_constraints
        self.assertEqual(raw.date_text, "今晚")
        self.assertEqual(raw.date_reference, DateReference.TODAY)
        self.assertEqual(raw.time_text, "今晚")
        self.assertEqual(raw.time_scope, TimeScope.EVENING)
        self.assertEqual(result.evidence_map["date_reference"], "今晚")
        self.assertIn("date_reference", result.extraction_confidence)

    def test_extracts_weekday_offset_and_keeps_plain_weekday_distinct(self) -> None:
        for text, expected_offset in (("本周六", 0), ("下周六", 1), ("周六", None)):
            with self.subTest(text=text):
                raw = self.router.interpret(text, self.context).raw_constraints
                self.assertEqual(raw.date_reference, DateReference.WEEKDAY)
                self.assertEqual(raw.weekday, Weekday.SATURDAY)
                self.assertEqual(raw.week_offset, expected_offset)

    def test_extracts_explicit_numeric_time_window(self) -> None:
        result = self.router.interpret("明天 10:00–16:00 出去玩", self.context)

        raw = result.raw_constraints
        self.assertEqual(raw.date_reference, DateReference.TOMORROW)
        self.assertEqual(raw.time_scope, TimeScope.EXPLICIT_RANGE)
        self.assertEqual(
            raw.explicit_time_window.model_dump(),
            {"start": "10:00", "end": "16:00"},
        )
        self.assertIn("explicit_time_window", result.evidence_map)


if __name__ == "__main__":
    unittest.main()

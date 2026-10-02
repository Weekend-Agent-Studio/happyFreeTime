from datetime import date
import unittest

from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    PlanRequest,
    RequestPatch,
)
from app.services.constraint_engine import (
    ConflictedRequest,
    ConstraintEngine,
    NeedsClarification,
    ResolvedRequest,
)


class ConstraintEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ConstraintEngine()

    def test_applies_multiple_fields_atomically_and_preserves_provenance(self) -> None:
        request = PlanRequest(
            budget_per_person=ConstraintValue[int](
                value=500,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="budget.default.test",
            ),
            total_distance_km=ConstraintValue[float](
                value=20.0,
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )

        result = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={"budget_per_person": 300},
                clear_fields=("total_distance_km",),
                source=ConstraintSource.USER_EXPLICIT,
                evidence={"budget_per_person": "人均三百元"},
            ),
        )

        self.assertIsInstance(result, ResolvedRequest)
        self.assertEqual(result.request.revision, 1)
        self.assertEqual(result.changed_fields, ("budget_per_person", "total_distance_km"))
        self.assertEqual(result.request.budget_per_person.value, 300)
        self.assertEqual(result.request.budget_per_person.source, ConstraintSource.USER_EXPLICIT)
        self.assertEqual(result.request.budget_per_person.raw_text, "人均三百元")
        self.assertIsNone(result.request.total_distance_km)
        self.assertEqual(request.revision, 0)
        self.assertEqual(request.budget_per_person.value, 500)
        self.assertIsNotNone(request.total_distance_km)

    def test_default_source_is_retained_and_provenance_change_advances_revision(self) -> None:
        request = PlanRequest()
        defaulted = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={"date": date(2026, 10, 3)},
                source=ConstraintSource.DEFAULT_RULE,
            ),
        )
        self.assertIsInstance(defaulted, ResolvedRequest)
        self.assertEqual(defaulted.request.date.source, ConstraintSource.DEFAULT_RULE)

        confirmed = self.engine.apply(
            defaulted.request,
            RequestPatch(
                base_revision=1,
                set_fields={"date": date(2026, 10, 3)},
                source=ConstraintSource.USER_EXPLICIT,
                evidence={"date": "周六"},
            ),
        )
        self.assertIsInstance(confirmed, ResolvedRequest)
        self.assertEqual(confirmed.request.revision, 2)
        self.assertEqual(confirmed.request.date.source, ConstraintSource.USER_EXPLICIT)
        self.assertEqual(confirmed.request.date.raw_text, "周六")

    def test_empty_or_repeated_patch_is_noop(self) -> None:
        request = PlanRequest(preferences=["安静"])
        empty = self.engine.apply(
            request,
            RequestPatch(base_revision=0, source=ConstraintSource.USER_EXPLICIT),
        )
        repeated = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={"preferences": ["安静"]},
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )

        self.assertIsInstance(empty, ResolvedRequest)
        self.assertIsInstance(repeated, ResolvedRequest)
        self.assertIs(empty.request, request)
        self.assertIs(repeated.request, request)
        self.assertEqual(empty.request.revision, 0)
        self.assertEqual(repeated.changed_fields, ())

    def test_stale_revision_is_rejected(self) -> None:
        result = self.engine.apply(
            PlanRequest(revision=2),
            RequestPatch(
                base_revision=1,
                set_fields={"preferences": ["安静"]},
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "STALE_REQUEST_REVISION")

    def test_does_not_silently_drop_evidence_for_unwrapped_fields(self) -> None:
        result = self.engine.apply(
            PlanRequest(),
            RequestPatch(
                base_revision=0,
                set_fields={"preferences": ["安静"]},
                source=ConstraintSource.USER_EXPLICIT,
                evidence={"preferences": "希望安静聊天"},
            ),
        )
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "FIELD_PROVENANCE_UNSUPPORTED")
        self.assertEqual(result.conflict.fields, ["preferences"])

    def test_invalid_multi_field_patch_does_not_partially_apply(self) -> None:
        request = PlanRequest(preferences=["轻松"])
        result = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={
                    "preferences": ["安静"],
                    "departure_at": "25:90",
                },
                source=ConstraintSource.USER_EXPLICIT,
                evidence={"departure_at": "凌晨二十五点九十出发"},
            ),
        )

        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "INVALID_REQUEST_PATCH")
        self.assertIn("departure_at", result.conflict.fields)
        self.assertEqual(request.preferences, ["轻松"])
        self.assertEqual(request.revision, 0)

    def test_departure_must_precede_return_deadline(self) -> None:
        result = self.engine.apply(
            PlanRequest(),
            RequestPatch(
                base_revision=0,
                set_fields={"departure_at": "20:30", "return_by": "20:00"},
                source=ConstraintSource.USER_EXPLICIT,
                evidence={
                    "departure_at": "晚上八点半出发",
                    "return_by": "晚上八点前回家",
                },
            ),
        )
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "DEPARTURE_NOT_BEFORE_RETURN_BY")
        self.assertEqual(result.conflict.fields, ["departure_at", "return_by"])

    def test_strict_budget_without_amount_requests_clarification_atomically(self) -> None:
        request = PlanRequest(preferences=["清淡"])
        result = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={"strict_budget": True, "preferences": ["清淡", "安静"]},
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )
        self.assertIsInstance(result, NeedsClarification)
        self.assertEqual(result.issue.field, "budget_per_person")
        self.assertEqual(result.issue.code, "STRICT_BUDGET_AMOUNT_REQUIRED")
        self.assertEqual(result.issue.expected_value_type, "integer")
        self.assertEqual(result.issue.request_revision, 0)
        self.assertTrue(result.issue.allow_free_text)
        self.assertFalse(request.strict_budget)
        self.assertEqual(request.preferences, ["清淡"])

    def test_invalid_time_range_is_a_conflict(self) -> None:
        result = self.engine.apply(
            PlanRequest(),
            RequestPatch(
                base_revision=0,
                set_fields={"time_window": {"start": "20:00", "end": "18:00"}},
                source=ConstraintSource.USER_EXPLICIT,
                evidence={"time_window": "晚上八点到六点"},
            ),
        )
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "INVALID_TIME_WINDOW_ORDER")
        self.assertEqual(result.conflict.fields, ["time_window.start", "time_window.end"])


if __name__ == "__main__":
    unittest.main()

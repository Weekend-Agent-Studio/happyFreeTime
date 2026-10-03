from datetime import date
from datetime import date
import unittest

from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    PlanRequest,
    PlanningWindow,
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

    @staticmethod
    def _ready_request() -> PlanRequest:
        return PlanRequest(
            planning_window=PlanningWindow(
                date=ConstraintValue[date](
                    value=date(2026, 10, 3),
                    source=ConstraintSource.DEFAULT_RULE,
                ),
                start_at=ConstraintValue[str](
                    value="14:00",
                    source=ConstraintSource.DEFAULT_RULE,
                ),
                end_at=ConstraintValue[str](
                    value="18:00",
                    source=ConstraintSource.DEFAULT_RULE,
                ),
            ),
            location=ConstraintValue[GeoLocation](
                value=GeoLocation(
                    city="北京市",
                    district="朝阳区",
                    address="北京市朝阳区",
                    latitude=39.9,
                    longitude=116.4,
                ),
                source=ConstraintSource.SYSTEM_CONTEXT,
            ),
        )

    def test_applies_multiple_fields_atomically_and_preserves_provenance(self) -> None:
        request = self._ready_request().model_copy(update={
            "budget_per_person": ConstraintValue[int](
                value=500,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="budget.default.test",
            ),
            "total_distance_km": ConstraintValue[float](
                value=20.0,
                source=ConstraintSource.USER_EXPLICIT,
            ),
        })

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
        request = self._ready_request().model_copy(
            update={
                "planning_window": self._ready_request().planning_window.model_copy(
                    update={"date": None}
                )
            }
        )
        defaulted = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={
                    "planning_window.date": ConstraintValue[date](
                        value=date(2026, 10, 3),
                        source=ConstraintSource.DEFAULT_RULE,
                    )
                },
                source=ConstraintSource.DEFAULT_RULE,
            ),
        )
        self.assertIsInstance(defaulted, ResolvedRequest)
        self.assertEqual(
            defaulted.request.planning_window.date.source,
            ConstraintSource.DEFAULT_RULE,
        )

        confirmed = self.engine.apply(
            defaulted.request,
            RequestPatch(
                base_revision=1,
                set_fields={
                    "planning_window.date": ConstraintValue[date](
                        value=date(2026, 10, 3),
                        source=ConstraintSource.USER_EXPLICIT,
                        raw_text="周六",
                    )
                },
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )
        self.assertIsInstance(confirmed, ResolvedRequest)
        self.assertEqual(confirmed.request.revision, 2)
        self.assertEqual(
            confirmed.request.planning_window.date.source,
            ConstraintSource.USER_EXPLICIT,
        )
        self.assertEqual(confirmed.request.planning_window.date.raw_text, "周六")

    def test_nested_constraint_values_survive_request_patch_boundary(self) -> None:
        request = PlanRequest()
        result = self.engine.apply(
            request,
            RequestPatch(
                base_revision=0,
                set_fields={
                    "location": ConstraintValue(
                        value={
                            "city": "北京市",
                            "district": "朝阳区",
                            "address": "北京市朝阳区",
                            "latitude": 39.9,
                            "longitude": 116.4,
                        },
                        source=ConstraintSource.SYSTEM_CONTEXT,
                        rule_id="location.default.test",
                    ),
                    "party": ConstraintValue(
                        value={"adults": 2, "children": 0, "members": []},
                        source=ConstraintSource.DEFAULT_RULE,
                    ),
                    "planning_window.date": ConstraintValue[date](
                        value=date(2026, 10, 10),
                        source=ConstraintSource.USER_EXPLICIT,
                        raw_text="周六",
                    ),
                    "planning_window.start_at": ConstraintValue[str](
                        value="14:00",
                        source=ConstraintSource.DERIVED,
                    ),
                    "planning_window.end_at": ConstraintValue[str](
                        value="18:00",
                        source=ConstraintSource.DERIVED,
                    ),
                    "budget_per_person": ConstraintValue[int](
                        value=200,
                        source=ConstraintSource.USER_EXPLICIT,
                    ),
                    "max_distance_km": ConstraintValue[float](
                        value=8.0,
                        source=ConstraintSource.DEFAULT_RULE,
                    ),
                    "strict_budget": True,
                },
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )

        self.assertIsInstance(result, ResolvedRequest)
        self.assertEqual(result.request.revision, 1)
        self.assertEqual(result.request.location.source, ConstraintSource.SYSTEM_CONTEXT)
        self.assertEqual(result.request.party.value.adults, 2)
        self.assertEqual(result.request.planning_window.date.raw_text, "周六")
        self.assertEqual(result.request.planning_window.start_at.value, "14:00")
        self.assertEqual(result.request.budget_per_person.value, 200)
        self.assertTrue(result.request.strict_budget)

    def test_empty_or_repeated_patch_is_noop(self) -> None:
        request = self._ready_request().model_copy(update={"preferences": ["安静"]})
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
                    "planning_window": {
                        "start_at": {"value": "25:90", "source": "user_explicit"},
                        "end_at": {"value": "20:00", "source": "default_rule"},
                    },
                },
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )

        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "INVALID_REQUEST_PATCH")
        self.assertTrue(
            any(field.startswith("planning_window.start_at") for field in result.conflict.fields)
        )
        self.assertEqual(request.preferences, ["轻松"])
        self.assertEqual(request.revision, 0)

    def test_departure_must_precede_return_deadline(self) -> None:
        result = self.engine.apply(
            self._ready_request(),
            RequestPatch(
                base_revision=0,
                set_fields={
                    "planning_window.start_at": ConstraintValue[str](
                        value="20:30",
                        source=ConstraintSource.USER_EXPLICIT,
                    ),
                    "planning_window.end_at": ConstraintValue[str](
                        value="20:00",
                        source=ConstraintSource.USER_EXPLICIT,
                    ),
                    "planning_window.start_kind": "departure",
                    "planning_window.end_kind": "return_deadline",
                },
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "DEPARTURE_NOT_BEFORE_RETURN_BY")
        self.assertEqual(result.conflict.fields, ["departure_at", "return_by"])

    def test_strict_budget_without_amount_requests_clarification_atomically(self) -> None:
        request = self._ready_request().model_copy(update={"preferences": ["清淡"]})
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
        self.assertEqual(result.issue.request_revision, 1)
        self.assertIsNotNone(result.request)
        self.assertEqual(result.request.revision, 1)
        self.assertTrue(result.request.strict_budget)
        self.assertEqual(result.request.preferences, ["清淡", "安静"])
        self.assertTrue(result.issue.allow_free_text)
        self.assertFalse(request.strict_budget)
        self.assertEqual(request.preferences, ["清淡"])

    def test_incomplete_request_never_returns_resolved(self) -> None:
        outcome = self.engine.apply(
            PlanRequest(),
            RequestPatch(base_revision=0, source=ConstraintSource.USER_EXPLICIT),
        )
        self.assertIsInstance(outcome, NeedsClarification)
        self.assertEqual(outcome.issue.field, "location")
        self.assertIsNotNone(outcome.request)

    def test_invalid_time_range_is_a_conflict(self) -> None:
        result = self.engine.apply(
            PlanRequest(),
            RequestPatch(
                base_revision=0,
                set_fields={
                    "planning_window": {
                        "start_at": {"value": "20:00", "source": "user_explicit"},
                        "end_at": {"value": "18:00", "source": "user_explicit"},
                    }
                },
                source=ConstraintSource.USER_EXPLICIT,
            ),
        )
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(result.conflict.code, "INVALID_TIME_WINDOW_ORDER")
        self.assertEqual(
            result.conflict.fields,
            ["planning_window.start_at", "planning_window.end_at"],
        )


if __name__ == "__main__":
    unittest.main()

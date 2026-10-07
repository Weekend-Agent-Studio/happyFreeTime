import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.domain.run_trace import (
    PlanningRunEvent,
    PlanningRunTrace,
    RunEventStatus,
    RunStage,
)


class PlanningRunTraceContractTest(unittest.TestCase):
    def _event(self, sequence: int = 1, **kwargs: object) -> PlanningRunEvent:
        return PlanningRunEvent(
            run_id="run-1",
            sequence=sequence,
            stage=RunStage.UNDERSTAND,
            status=RunEventStatus.COMPLETED,
            message_key="understand.completed",
            public_message="已理解规划需求",
            occurred_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
            **kwargs,
        )

    def test_trace_requires_contiguous_events_for_the_same_run(self) -> None:
        trace = PlanningRunTrace(
            run_id="run-1",
            events=(self._event(), self._event(2)),
        )

        self.assertEqual(trace.events[1].sequence, 2)
        self.assertEqual(trace.model_dump(mode="json")["schema_version"], "planning-run-trace.v1")

    def test_trace_rejects_gaps_and_cross_run_events(self) -> None:
        with self.assertRaises(ValidationError):
            PlanningRunTrace(run_id="run-1", events=(self._event(2),))
        with self.assertRaises(ValidationError):
            PlanningRunTrace(
                run_id="run-1",
                events=(self._event(), self._event(2).model_copy(update={"run_id": "run-2"})),
            )

    def test_public_details_are_allowlisted_and_json_safe(self) -> None:
        event = self._event(public_details={"candidate_count": 12, "mode": "hybrid"})
        self.assertEqual(event.public_details["candidate_count"], 12)

        with self.assertRaises(ValidationError):
            self._event(public_details={"prompt": "do not expose this"})
        with self.assertRaises(ValidationError):
            self._event(public_details={"unbounded_internal_field": "no"})

    def test_event_contract_forbids_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(unexpected="not allowed")

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.domain.run_trace import (
    InMemoryRunObserver,
    PlanningRunEvent,
    PlanningRunEventDraft,
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
        with self.assertRaises(ValidationError):
            self._event(public_details={"mode": "x" * 161})
        with self.assertRaises(ValidationError):
            self._event(public_details={"mode": "unsafe\nvalue"})

    def test_event_contract_forbids_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(unexpected="not allowed")

    def test_observer_assigns_sequence_and_run_identity(self) -> None:
        observer = InMemoryRunObserver("run-2")
        observer.record(
            PlanningRunEventDraft(
                stage=RunStage.RETRIEVE,
                status=RunEventStatus.STARTED,
                message_key="retrieve.started",
                public_message="正在检索候选地点",
            )
        )
        observer.record(
            PlanningRunEventDraft(
                stage=RunStage.RETRIEVE,
                status=RunEventStatus.COMPLETED,
                message_key="retrieve.completed",
                public_message="候选地点检索完成",
                public_details={"candidate_count": 8},
            )
        )

        trace = observer.snapshot()
        self.assertEqual(trace.run_id, "run-2")
        self.assertEqual([item.sequence for item in trace.events], [1, 2])
        self.assertEqual(trace.events[1].public_details["candidate_count"], 8)
        self.assertIsNotNone(trace.events[1].duration_ms)
        self.assertGreaterEqual(trace.events[1].duration_ms or 0, 0)

    def test_observer_rejects_duplicate_stage_lifecycle_events(self) -> None:
        observer = InMemoryRunObserver("run-3")
        started = PlanningRunEventDraft(
            stage=RunStage.STRUCTURE,
            status=RunEventStatus.STARTED,
            message_key="structure.started",
            public_message="开始组织结构",
        )
        observer.record(started)
        with self.assertRaises(ValueError):
            observer.record(started)

        observer.record(
            PlanningRunEventDraft(
                stage=RunStage.STRUCTURE,
                status=RunEventStatus.COMPLETED,
                message_key="structure.completed",
                public_message="结构完成",
            )
        )
        with self.assertRaises(ValueError):
            observer.record(
                PlanningRunEventDraft(
                    stage=RunStage.STRUCTURE,
                    status=RunEventStatus.COMPLETED,
                    message_key="structure.completed_again",
                    public_message="结构再次完成",
                )
            )

    def test_observer_closes_open_stages_with_safe_failure(self) -> None:
        observer = InMemoryRunObserver("run-4")
        observer.record(
            PlanningRunEventDraft(
                stage=RunStage.VERIFY,
                status=RunEventStatus.STARTED,
                message_key="verify.started",
                public_message="开始核验",
            )
        )
        failed = observer.fail_open_stages()
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].status, RunEventStatus.FAILED)
        self.assertEqual(failed[0].public_details, {"reason_code": "internal_error"})

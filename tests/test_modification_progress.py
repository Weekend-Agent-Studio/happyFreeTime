import pytest

from app.domain.planning import CandidateSet
from app.domain.run_trace import InMemoryRunObserver, PlanningRunEventDraft, RunEventStatus, RunStage
from app.orchestration.entry_graph import build_entry_graph
from app.orchestration.workflows import ModificationWorkflow, WorkflowOutcome, WorkflowResult, WorkflowRoute
from app.services.demo_router import DemoRouter
from app.services.planning import PlanningService
from tests import test_plan_modification as modification_fixture
from tests.test_planning import planning_constraints


@pytest.mark.parametrize("recovering", [True, False])
def test_recovered_modification_success_closes_recovery_trace(monkeypatch, recovering):
    plans = PlanningService().plan(planning_constraints(budget=1000, max_distance_km=30)).plans
    assert plans
    monkeypatch.setattr(ModificationWorkflow, "execute", lambda *args, **kwargs: WorkflowResult(
        updates={"candidate_set": CandidateSet(plans=plans)},
        route=WorkflowRoute.END, outcome=WorkflowOutcome.MODIFICATION))
    actor = modification_fixture.PlanModificationTest._patch_actor()
    observer = InMemoryRunObserver("modification-recovery")
    for stage, status in ([(RunStage.MODIFY, RunEventStatus.STARTED), (RunStage.MODIFY, RunEventStatus.COMPLETED),
                          (RunStage.RECOVERY, RunEventStatus.STARTED), (RunStage.AUTO_RECOVERY, RunEventStatus.STARTED),
                          (RunStage.AUTO_RECOVERY, RunEventStatus.COMPLETED)] if recovering else []):
        observer.record(PlanningRunEventDraft(stage=stage, status=status, message_key="test.event", public_message="test"))
    config = {"configurable": {"thread_id": actor.session_id, "run_observer": observer}}
    graph = build_entry_graph(router=DemoRouter(), environment_provider=lambda _: modification_fixture.PlanModificationTest._patch_environment())
    graph.update_state(config, {"actor": actor, "workflow_route": "modify_plan"}, as_node="apply_recovery_action")
    result = graph.invoke(None, config=config)
    assert result["candidate_set"].plans
    recovery = [event for event in observer.snapshot().events if event.stage == RunStage.RECOVERY]
    assert [event.status for event in recovery] == ([RunEventStatus.STARTED, RunEventStatus.COMPLETED] if recovering else [])
    assert not observer.fail_open_stages()

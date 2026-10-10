import unittest
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.domain.constraints import (
    ActorContext,
    ClarificationAction,
    CommandOperation,
    ConstraintPatch,
    ConstraintSource,
    ConstraintValue,
    EventClockProposal,
    ConversationCommand,
    GeoLocation,
    IdentityType,
    Intent,
    Interpretation,
    RequestPatch,
    RawConstraints,
    StopRole,
    PeriodProposal,
    TimeScope,
)
from app.domain.catalog import ResourceType
from app.domain.providers import GeoPoint
from app.orchestration.entry_graph import (
    build_entry_graph,
    checkpoint_config,
    checkpoint_serializer,
)
from app.orchestration.workflows import RequestWorkflow, WorkflowRoute
from app.services.demo_router import DemoRouter
from app.services.enrichment import EnvironmentContext
from app.providers.geocoding import MockGeocodingProvider
from app.services.catalog import InMemoryCatalog
from app.services.constraint_engine import ConstraintEngine
from app.services.enrichment import EnrichmentService
from app.services.question_policy import QuestionPolicy
from app.services.request_patch_compiler import RequestPatchProposalCompiler
from app.services.request_patch_update import RequestPatchUpdateCompiler
from app.services.router_extractor import RouterContext
from app.domain.planning import PlanSlotProposal, PlanStructureProposal
from app.domain.planning import ConstraintConflict
from app.domain.recovery import (
    OpenConstraintEditorAction,
    RecoveryChoiceInteraction,
    RecoveryActionResponse,
    RecoveryDiagnostics,
    RecoveryKind,
    RecoveryReason,
    RecoveryStage,
)
from tests.test_planning import planning_constraints
from tests.test_native_planning import candidate
from tests.router_support import InterpretationRouter


def _afternoon() -> PeriodProposal:
    return PeriodProposal(
        event="trip", period=TimeScope.AFTERNOON, evidence="下午"
    )


class FollowUpRouter(InterpretationRouter):
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        self.inputs.append(user_input)
        if "200" in user_input:
            return Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 0.99},
                time_proposals=(_afternoon(),),
                raw_constraints=RawConstraints(
                    date_text="今天",
                    budget_text="人均200",
                    budget_per_person=200,
                    strict_budget=True,
                ),
            )
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.99},
            time_proposals=(_afternoon(),),
            raw_constraints=RawConstraints(
                date_text="今天",
                budget_text="千万别超预算",
                strict_budget=True,
            ),
        )


class ExplicitLocationRouter(InterpretationRouter):
    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            time_proposals=(_afternoon(),),
            raw_constraints=RawConstraints(
                date_text="今天", origin_text="不存在地标"
            ),
        )


class EntryGraphTest(unittest.TestCase):
    def test_recovered_field_answer_continues_the_original_modification_path(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        workflow = RequestWorkflow(
            environment_provider=lambda _: environment,
            enrichment_service=EnrichmentService(),
            proposal_compiler=RequestPatchProposalCompiler(),
            update_compiler=RequestPatchUpdateCompiler(),
            constraint_engine=ConstraintEngine(),
            question_policy=QuestionPolicy(),
            prepare_question=lambda decision: decision,
        )
        request = planning_constraints()
        result = workflow.apply_patch(
            {"recovery_continuation": "modify", "has_plans": True},
            request=request,
            patch=RequestPatch(
                base_revision=request.revision,
                set_fields={
                    "budget_per_person": ConstraintValue[int](
                        value=200,
                        source=ConstraintSource.USER_EXPLICIT,
                    )
                },
                source=ConstraintSource.USER_EXPLICIT,
            ),
            resumed=True,
        )

        self.assertEqual(result.route, WorkflowRoute.MODIFY_PLAN)
        self.assertEqual(result.updates["active_request"].revision, request.revision + 1)
        self.assertIsNone(result.updates["recovery_continuation"])

    def test_checkpoint_namespace_versions_the_development_state(self) -> None:
        self.assertEqual(
            checkpoint_config("session-1"),
            {
                "configurable": {
                    "thread_id": "planner-hitl1-v1:session-1",
                }
            },
        )

    def test_plan_structure_proposal_is_checkpoint_serializable(self) -> None:
        proposal = PlanStructureProposal(
            slots=(PlanSlotProposal(role=StopRole.ACTIVITY, inclusion="core"),)
        )
        serializer = checkpoint_serializer()
        encoded = serializer.dumps_typed(proposal)
        restored = serializer.loads_typed(encoded)

        self.assertEqual(restored, proposal)

    def test_constraint_conflict_is_checkpoint_serializable(self) -> None:
        conflict = ConstraintConflict(
            code="DEPARTURE_NOT_BEFORE_RETURN_BY",
            message="出发时间必须早于返程时间。",
            fields=["departure_at", "return_by"],
        )
        serializer = checkpoint_serializer()
        restored = serializer.loads_typed(serializer.dumps_typed(conflict))

        self.assertEqual(restored, conflict)

    def test_recovery_choice_and_actions_are_checkpoint_serializable(self) -> None:
        reason = RecoveryReason(
            code="NO_CANDIDATES_AFTER_HARD_FILTER",
            kind=RecoveryKind.NO_FEASIBLE_PLAN,
            stage=RecoveryStage.RETRIEVAL,
            fields=("max_distance_km",),
            request_revision=3,
            public_summary="当前条件下暂未找到可行方案。",
            diagnostics=RecoveryDiagnostics(current_max_distance_km=8),
        )
        interaction = RecoveryChoiceInteraction(
            interaction_id="recovery-1",
            request_revision=3,
            reason=reason,
            actions=(
                OpenConstraintEditorAction(
                    action_id="edit-constraints-r3",
                    label="手动调整条件",
                    request_revision=3,
                    continuation="finish",
                ),
            ),
        )

        serializer = checkpoint_serializer()
        restored = serializer.loads_typed(serializer.dumps_typed(interaction))

        self.assertEqual(restored, interaction)

    def test_typed_trip_scope_is_checkpoint_serializable(self) -> None:
        request = planning_constraints().model_copy(
            update={
                "trip_time_scope": ConstraintValue(
                    value=TimeScope.ALL_DAY,
                    source=ConstraintSource.USER_INFERRED,
                    raw_text="一整天",
                    rule_id="time.trip.scope.all_day.v1",
                )
            }
        )
        serializer = checkpoint_serializer()
        restored = serializer.loads_typed(serializer.dumps_typed(request))

        self.assertEqual(restored, request)

    def test_active_plan_time_supplement_is_not_misclassified_as_replacement(self) -> None:
        interpretation = DemoRouter().interpret(
            "补充一下，想玩一整天",
            RouterContext(
                current_date=date(2026, 8, 12),
                has_plans=True,
            ),
        )
        self.assertIsNotNone(interpretation.conversation_command)
        self.assertEqual(
            interpretation.conversation_command.operation.value,
            "patch_constraints",
        )
        self.assertEqual(
            interpretation.conversation_command.constraint_patch.time_window_text,
            "补充一下，想玩一整天",
        )

    def test_role_preservation_diet_request_stays_replacement(self) -> None:
        interpretation = DemoRouter().interpret(
            "活动保留，晚餐希望少辣",
            RouterContext(
                current_date=date(2026, 8, 12),
                has_plans=True,
                has_selected_plan=True,
            ),
        )
        command = interpretation.conversation_command
        self.assertIsNotNone(command)
        self.assertEqual(command.operation, CommandOperation.REPLACE)
        self.assertEqual(command.target.role, StopRole.DINNER)
        self.assertEqual(command.locked_targets[0].role, StopRole.ACTIVITY)

    def test_patch_question_resumes_into_compiler_without_reasking(self) -> None:
        class PatchRouter(InterpretationRouter):
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                self.inputs.append(user_input)
                return Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    conversation_command=ConversationCommand(
                        operation=CommandOperation.PATCH_CONSTRAINTS,
                        constraint_patch=ConstraintPatch(departure_at_text="早上出门吧"),
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-patch-resume",
            identity_type=IdentityType.DEMO,
        )
        router = PatchRouter()
        graph = build_entry_graph(
            router=router,
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([]),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {
                "user_input": "改成早上出发",
                "actor": actor,
                "has_plans": True,
                "active_request": planning_constraints(),
            },
            config=config,
        )
        question = first["__interrupt__"][0].value
        self.assertEqual(question["field"], "departure_at")
        self.assertEqual(question["issue_kind"], "constraint")
        self.assertEqual(question["request_revision"], 0)

        resumed = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": "answer",
                    "value": "早上九点",
                }
            ),
            config=config,
        )
        # The clarification is resolved and planning runs. This test graph has
        # an empty catalog, so the expected next interaction is recovery from
        # the planning failure rather than a repeated departure-time question.
        self.assertEqual(
            resumed["__interrupt__"][0].value["kind"],
            "recovery_choice",
        )
        self.assertEqual(
            resumed["active_request"].planning_window.start_at.value,
            "09:00",
        )

        recovery = resumed["__interrupt__"][0].value
        editor = next(
            item
            for item in recovery["actions"]
            if item["kind"] == "open_constraint_editor"
        )
        finished = graph.invoke(
            Command(
                resume={
                    "interaction_id": recovery["interaction_id"],
                    "action_id": editor["action_id"],
                    "request_revision": editor["request_revision"],
                    "plan_version_id": editor["plan_version_id"],
                }
            ),
            config=config,
        )
        self.assertNotIn("__interrupt__", finished)
        self.assertEqual(finished["recovery_resolution"], "open_constraint_editor")
        self.assertEqual(
            finished["active_request"].planning_window.start_at.value,
            "09:00",
        )
        self.assertEqual(router.inputs, ["改成早上出发"])

    def test_recovery_start_new_request_drops_previous_plan_context(self) -> None:
        class RecoveryRouter(InterpretationRouter):
            def __init__(self) -> None:
                self.contexts: list[RouterContext] = []

            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                self.contexts.append(context)
                return Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    conversation_command=ConversationCommand(
                        operation=CommandOperation.PATCH_CONSTRAINTS,
                        constraint_patch=ConstraintPatch(
                            departure_at_text="早上出门吧"
                        ),
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-recovery-new-request",
            identity_type=IdentityType.DEMO,
        )
        router = RecoveryRouter()
        graph = build_entry_graph(
            router=router,
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([]),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {
                "user_input": "改成早上出发",
                "actor": actor,
                "has_plans": True,
                "active_request": planning_constraints(),
                "active_plan_version_id": "old-version",
            },
            config=config,
        )
        question = first["__interrupt__"][0].value
        recovery = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": "answer",
                    "value": "早上九点",
                }
            ),
            config=config,
        )["__interrupt__"][0].value
        new_request_action = next(
            item for item in recovery["actions"] if item["kind"] == "start_new_request"
        )
        self.assertEqual(
            new_request_action["plan_version_id"], recovery["plan_version_id"]
        )
        graph.invoke(
            Command(
                resume=RecoveryActionResponse(
                    interaction_id=recovery["interaction_id"],
                    action_id=new_request_action["action_id"],
                    request_revision=recovery["request_revision"],
                    plan_version_id=recovery["plan_version_id"],
                ).model_dump(mode="json")
            ),
            config=config,
        )

        next_turn = graph.invoke(
            {
                "user_input": "今天去看展",
                "actor": actor,
                "has_plans": True,
                "active_request": planning_constraints(),
                "active_plan_version_id": "old-version",
            },
            config=config,
        )

        self.assertIsNone(next_turn.get("recovery_resolution"))
        context = router.contexts[-1]
        self.assertFalse(context.has_plans)
        self.assertFalse(context.has_selected_plan)
        self.assertEqual(context.decision_context.request_lifecycle.value, "empty")
        self.assertIsNone(context.decision_context.current_request)
        self.assertIsNone(context.decision_context.selected_plan)
        self.assertIsNone(context.previous_user_act)

    def test_default_distance_auto_recovers_once_then_uses_the_same_planner(self) -> None:
        class DistanceRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    time_proposals=(_afternoon(),),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        exact_stop_count=1,
                        required_stop_roles=(StopRole.ACTIVITY,),
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        far_activity = candidate(
            "far-activity", ResourceType.ACTIVITY, "远处公园", ["公园"]
        ).model_copy(update={"location": GeoPoint(latitude=40.0119, longitude=116.4436)})
        actor = ActorContext(
            user_id="demo-user", session_id="session-default-distance-recovery",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DistanceRouter(),
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([far_activity]),
        )

        result = graph.invoke(
            {"user_input": "今天下午出去玩", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(len(result["candidate_set"].plans), 1)
        self.assertEqual(result["active_request"].max_distance_km.value, 12.0)
        self.assertEqual(
            result["active_request"].max_distance_km.source,
            ConstraintSource.DERIVED,
        )
        self.assertTrue(result["auto_recovery_attempted"])
        self.assertNotIn("__interrupt__", result)

    def test_explicit_distance_failure_interrupts_without_auto_relaxation(self) -> None:
        class ExplicitDistanceRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    time_proposals=(_afternoon(),),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        exact_stop_count=1,
                        required_stop_roles=(StopRole.ACTIVITY,),
                        max_distance_text="八公里内",
                        max_distance_km=8.0,
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        far_activity = candidate(
            "far-explicit-activity", ResourceType.ACTIVITY, "远处公园", ["公园"]
        ).model_copy(update={"location": GeoPoint(latitude=40.0119, longitude=116.4436)})
        actor = ActorContext(
            user_id="demo-user", session_id="session-explicit-distance-recovery",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitDistanceRouter(),
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([far_activity]),
        )

        result = graph.invoke(
            {"user_input": "今天下午八公里内出去玩", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        interaction = result["__interrupt__"][0].value
        self.assertEqual(interaction["kind"], "recovery_choice")
        self.assertFalse(result["auto_recovery_attempted"])
        self.assertEqual(result["active_request"].max_distance_km.value, 8.0)
        self.assertEqual(
            result["active_request"].max_distance_km.source,
            ConstraintSource.USER_EXPLICIT,
        )
        self.assertTrue(
            any(
                item["kind"] == "apply_request_patch"
                and item["patch"]["set_fields"]["max_distance_km"] == 12.0
                for item in interaction["actions"]
            )
        )

    def test_hard_time_conflict_interrupt_can_be_corrected_and_resume_planning(self) -> None:
        class ConflictRouter(InterpretationRouter):
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                self.inputs.append(user_input)
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    time_proposals=(
                        EventClockProposal(event="departure", clock="21:00", evidence="晚上九点出发"),
                        EventClockProposal(event="return", clock="20:00", evidence="晚上八点前回家"),
                    ),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        exact_stop_count=1,
                        required_stop_roles=(StopRole.ACTIVITY,),
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        nearby_activity = candidate(
            "nearby-activity", ResourceType.ACTIVITY, "附近公园", ["公园"]
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-time-conflict-recovery",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ConflictRouter(),
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([nearby_activity]),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "今天九点出发，八点前回家", "actor": actor},
            config=config,
        )
        interaction = first["__interrupt__"][0].value
        self.assertEqual(interaction["kind"], "recovery_choice")
        fix_departure = next(
            item
            for item in interaction["actions"]
            if item["kind"] == "request_field" and item["field"] == "departure_at"
        )

        result = graph.invoke(
            Command(
                resume=RecoveryActionResponse(
                    interaction_id=interaction["interaction_id"],
                    action_id=fix_departure["action_id"],
                    request_revision=interaction["request_revision"],
                    plan_version_id=interaction["plan_version_id"],
                    field_value="09:00",
                ).model_dump(mode="json")
            ),
            config=config,
        )

        self.assertNotIn("__interrupt__", result)
        self.assertEqual(len(result["candidate_set"].plans), 1)
        self.assertEqual(result["active_request"].planning_window.start_at.value, "09:00")

        # Choosing manual editing must expose the parsed draft (including the
        # conflicting values) to the top bar without planning or rewriting it.
        editor_config = {"configurable": {"thread_id": "session-time-conflict-editor"}}
        editor_first = graph.invoke(
            {"user_input": "今天九点出发，八点前回家", "actor": actor},
            config=editor_config,
        )
        editor_interaction = editor_first["__interrupt__"][0].value
        editor_action = next(
            item
            for item in editor_interaction["actions"]
            if item["kind"] == "open_constraint_editor"
        )
        editor_result = graph.invoke(
            Command(
                resume=RecoveryActionResponse(
                    interaction_id=editor_interaction["interaction_id"],
                    action_id=editor_action["action_id"],
                    request_revision=editor_interaction["request_revision"],
                    plan_version_id=editor_interaction["plan_version_id"],
                ).model_dump(mode="json")
            ),
            config=editor_config,
        )
        self.assertNotIn("__interrupt__", editor_result)
        self.assertEqual(
            editor_result["recovery_resolution"], "open_constraint_editor"
        )
        self.assertIsNone(editor_result["active_request"].planning_window.start_at)
        self.assertIsNone(editor_result["active_request"].planning_window.end_at)
        self.assertEqual(
            editor_result["recovery_base_request"].planning_window.start_at.value,
            "21:00",
        )
        self.assertEqual(
            editor_result["recovery_base_request"].planning_window.end_at.value,
            "20:00",
        )
        self.assertIsNone(editor_result["candidate_set"])

    def test_invalid_recovery_field_value_can_be_corrected_in_new_interaction(self) -> None:
        class ConflictRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    time_proposals=(
                        EventClockProposal(event="departure", clock="21:00", evidence="晚上九点出发"),
                        EventClockProposal(event="return", clock="20:00", evidence="晚上八点前回家"),
                    ),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        exact_stop_count=1,
                        required_stop_roles=(StopRole.ACTIVITY,),
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-recovery-field-retry",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ConflictRouter(),
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([]),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "今天九点出发，八点前回家", "actor": actor},
            config=config,
        )
        first_interaction = first["__interrupt__"][0].value
        departure_action = next(
            item for item in first_interaction["actions"]
            if item["kind"] == "request_field" and item["field"] == "departure_at"
        )

        retried = graph.invoke(
            Command(resume=RecoveryActionResponse(
                interaction_id=first_interaction["interaction_id"],
                action_id=departure_action["action_id"],
                request_revision=first_interaction["request_revision"],
                plan_version_id=first_interaction["plan_version_id"],
                field_value="不是时间",
            ).model_dump(mode="json")),
            config=config,
        )
        retry_interaction = retried["__interrupt__"][0].value
        self.assertEqual(retry_interaction["kind"], "field_clarification")
        self.assertNotEqual(
            retry_interaction["clarification_id"],
            first_interaction["interaction_id"],
        )
        self.assertEqual(retry_interaction["field"], "departure_at")
        self.assertIn("具体时间", retry_interaction["question"])
        self.assertEqual(retry_interaction["max_attempts"], 2)
        corrected = graph.invoke(
            Command(resume={
                "clarification_id": retry_interaction["clarification_id"],
                "action": "answer",
                "value": "09:00",
                "request_revision": retry_interaction["request_revision"],
            }),
            config=config,
        )
        corrected_interaction = corrected["__interrupt__"][0].value
        self.assertEqual(corrected_interaction["kind"], "recovery_choice")
        self.assertEqual(
            corrected["active_request"].planning_window.start_at.value,
            "09:00",
        )

    def test_recovery_choice_resumes_after_sqlite_checkpointer_reopen(self) -> None:
        class ConflictRouter(InterpretationRouter):
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                self.inputs.append(user_input)
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    time_proposals=(
                        EventClockProposal(event="departure", clock="21:00", evidence="晚上九点出发"),
                        EventClockProposal(event="return", clock="20:00", evidence="晚上八点前回家"),
                    ),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        exact_stop_count=1,
                        required_stop_roles=(StopRole.ACTIVITY,),
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        nearby_activity = candidate(
            "sqlite-recovery-activity", ResourceType.ACTIVITY, "附近公园", ["公园"]
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-sqlite-recovery-resume",
            identity_type=IdentityType.DEMO,
        )
        router = ConflictRouter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "recovery-checkpoint.db"
            first_connection = sqlite3.connect(database_path, check_same_thread=False)
            first_saver = SqliteSaver(
                first_connection, serde=checkpoint_serializer()
            )
            first_saver.setup()
            first_graph = build_entry_graph(
                router=router,
                environment_provider=lambda _: environment,
                catalog=InMemoryCatalog([nearby_activity]),
                checkpointer=first_saver,
            )
            config = {"configurable": {"thread_id": actor.session_id}}
            first = first_graph.invoke(
                {"user_input": "今天九点出发，八点前回家", "actor": actor},
                config=config,
            )
            interaction = first["__interrupt__"][0].value
            fix_departure = next(
                item
                for item in interaction["actions"]
                if item["kind"] == "request_field" and item["field"] == "departure_at"
            )
            first_connection.close()

            second_connection = sqlite3.connect(database_path, check_same_thread=False)
            second_saver = SqliteSaver(
                second_connection, serde=checkpoint_serializer()
            )
            second_saver.setup()
            resumed_graph = build_entry_graph(
                router=router,
                environment_provider=lambda _: environment,
                catalog=InMemoryCatalog([nearby_activity]),
                checkpointer=second_saver,
            )
            resumed = resumed_graph.invoke(
                Command(
                    resume=RecoveryActionResponse(
                        interaction_id=interaction["interaction_id"],
                        action_id=fix_departure["action_id"],
                        request_revision=interaction["request_revision"],
                        plan_version_id=interaction["plan_version_id"],
                        field_value="09:00",
                    ).model_dump(mode="json")
                ),
                config=config,
            )
            second_connection.close()

        self.assertNotIn("__interrupt__", resumed)
        self.assertEqual(len(resumed["candidate_set"].plans), 1)
        self.assertEqual(resumed["active_request"].planning_window.start_at.value, "09:00")
        self.assertEqual(router.inputs, ["今天九点出发，八点前回家"])

    def test_demo_departure_period_patch_uses_the_shared_resume_contract(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-demo-departure-period",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
            catalog=InMemoryCatalog([]),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {
                "user_input": "补充一下，早上出发",
                "actor": actor,
                "has_plans": True,
                "active_request": planning_constraints(),
            },
            config=config,
        )
        question = first["__interrupt__"][0].value
        self.assertEqual(question["field"], "departure_at")

        resumed = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": "answer",
                    "value": "早上九点",
                }
            ),
            config=config,
        )

        # The field-level answer is compiled once; planning then interrupts on
        # the empty test catalog, with a distinct recovery interaction.
        self.assertEqual(
            resumed["__interrupt__"][0].value["kind"],
            "recovery_choice",
        )
        self.assertEqual(
            resumed["active_request"].planning_window.start_at.value,
            "09:00",
        )

    def test_refine_plan_without_resolved_command_asks_without_planning(self) -> None:
        class UnresolvedModificationRouter(InterpretationRouter):
            def interpret(
                self,
                user_input: str,
                context: RouterContext,
            ) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    conversation_command=None,
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-unresolved-modification",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=UnresolvedModificationRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {
                "user_input": "把那个地方换一下",
                "actor": actor,
                "has_plans": True,
            },
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(
            result["pending_issue"].field,
            "target_reference",
        )
        self.assertTrue(result["pending_issue"].need_question)
        self.assertIsNone(result["candidate_set"])
        self.assertEqual(result["plan_diffs"], ())

    def test_replace_without_selected_plan_asks_before_planning(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-no-selection",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {
                "user_input": "餐厅保留，只把活动换近一点",
                "actor": actor,
                "has_plans": True,
                "active_plan_version_id": "version-1",
                "active_request": None,
                "selected_plan": None,
            },
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(result["pending_issue"].field, "selected_plan_id")
        self.assertIsNone(result["candidate_set"])

    def test_unresolvable_explicit_location_interrupts_without_falling_back_to_default(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )
        actor = ActorContext(user_id="demo-user", session_id="session-geo-gate", identity_type=IdentityType.DEMO)
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )

        result = graph.invoke({"user_input": "不存在地标今天下午出去玩", "actor": actor}, config={"configurable": {"thread_id": actor.session_id}})

        self.assertEqual(result["__interrupt__"][0].value["field"], "location")

    def test_location_question_can_use_default_without_reentering_router(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-location-default",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        question = first["__interrupt__"][0].value
        self.assertIn("use-default-location", {item["id"] for item in question["options"]})
        final = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": ClarificationAction.USE_DEFAULT.value,
                }
            ),
            config=config,
        )
        self.assertTrue(final["ready_for_planning"])
        self.assertEqual(
            final["active_request"].location.source.value,
            "system_context",
        )
        default_assumption = next(
            item for item in final["assumptions"]
            if item.rule_id == "clarification.default.location.v1"
        )
        self.assertEqual(default_assumption.value, environment.default_location)

    def test_stale_clarification_revision_is_rejected(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-stale-clarification",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        question = first["__interrupt__"][0].value

        resumed = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"] + 1,
                    "action": ClarificationAction.ANSWER.value,
                    "value": "朝阳公园",
                }
            ),
            config=config,
        )

        self.assertEqual(resumed["clarification_resolution"], "stale_rejected")
        self.assertEqual(resumed["candidate_set"].conflict.code, "STALE_CLARIFICATION_REVISION")
        self.assertEqual(
            resumed["candidate_set"].recovery_reason.code,
            "STALE_CLARIFICATION_REVISION",
        )
        self.assertEqual(
            resumed["candidate_set"].recovery_reason.request_revision,
            resumed["active_request"].revision,
        )
        self.assertIsNone(resumed["pending_issue"])
        self.assertFalse(resumed["ready_for_planning"])

    def test_invalid_clarification_reaches_cap_without_repeating_free_text_forever(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-location-cap",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        current = first["__interrupt__"][0].value
        for _ in range(2):
            result = graph.invoke(
                Command(
                    resume={
                        "clarification_id": current["clarification_id"],
                        "request_revision": current["request_revision"],
                        "action": ClarificationAction.ANSWER.value,
                        "value": "?",
                    }
                ),
                config=config,
            )
            current = result["__interrupt__"][0].value
        self.assertEqual(current["attempt"], 2)
        self.assertFalse(current["allow_free_text"])

    def test_clarification_cancel_returns_without_planning(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-location-cancel",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=ExplicitLocationRouter(),
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "不存在地标今天下午出去玩", "actor": actor},
            config=config,
        )
        question = first["__interrupt__"][0].value
        cancelled = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": ClarificationAction.CANCEL.value,
                }
            ),
            config=config,
        )
        self.assertIsNone(cancelled["candidate_set"])
        self.assertIn("取消", cancelled["interpretation"].reply)

    def test_new_request_during_clarification_does_not_carry_old_interpretation(self) -> None:
        class NewRequestRouter(InterpretationRouter):
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                self.inputs.append(user_input)
                if user_input.startswith("旧需求"):
                    return Interpretation(
                        primary_intent=Intent.PLAN_OUTING,
                        intent_scores={Intent.PLAN_OUTING: 1.0},
                        time_proposals=(_afternoon(),),
                        raw_constraints=RawConstraints(
                            date_text="今天", origin_text="不存在地标",
                            preferences=["旧偏好"],
                        ),
                    )
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 1.0},
                    time_proposals=(_afternoon(),),
                    raw_constraints=RawConstraints(date_text="今天"),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user", session_id="session-new-request",
            identity_type=IdentityType.DEMO,
        )
        router = NewRequestRouter()
        graph = build_entry_graph(
            router=router,
            environment_provider=lambda _: environment,
            geocoding_provider=MockGeocodingProvider.from_locations({}),
        )
        config = {"configurable": {"thread_id": actor.session_id}}
        first = graph.invoke(
            {"user_input": "旧需求今天下午出去玩", "actor": actor}, config=config
        )
        question = first["__interrupt__"][0].value
        resumed = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": ClarificationAction.NEW_REQUEST.value,
                    "value": "新需求今天下午看展",
                }
            ),
            config=config,
        )
        self.assertTrue(resumed["ready_for_planning"])
        self.assertEqual(router.inputs, ["旧需求今天下午出去玩", "新需求今天下午看展"])
        self.assertNotIn("旧偏好", resumed["interpretation"].raw_constraints.preferences)
    def test_interrupts_for_blocking_field_and_resumes_through_router(self) -> None:
        router = FollowUpRouter()
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(router=router, environment_provider=lambda _: environment)
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-1",
            identity_type=IdentityType.DEMO,
        )
        config = {"configurable": {"thread_id": actor.session_id}}

        first_result = graph.invoke(
            {
                "user_input": "今天下午出去玩，千万别超预算",
                "actor": actor,
            },
            config=config,
        )

        self.assertIn("__interrupt__", first_result)
        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, ("ask_question",))
        self.assertEqual(snapshot.interrupts[0].value["field"], "budget_per_person")

        question = snapshot.interrupts[0].value
        final_result = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "action": ClarificationAction.ANSWER.value,
                    "value": "人均200",
                    "request_revision": question["request_revision"],
                }
            ),
            config=config,
        )

        self.assertTrue(final_result["ready_for_planning"])
        self.assertEqual(
            final_result["active_request"].budget_per_person.value,
            200,
        )
        # The answer is projected onto the pending field and resumes at
        # Enrichment; the original request is not sent through the full Router
        # a second time.
        self.assertEqual(len(router.inputs), 1)
        self.assertNotIn("用户补充：人均200", router.inputs[0])

    def test_chitchat_finishes_without_entering_planning(self) -> None:
        class ChitchatRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.CHITCHAT,
                    intent_scores={Intent.CHITCHAT: 1.0},
                    reply="你好，今天想聊点什么？",
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=ChitchatRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-2",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "你好", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertFalse(result["ready_for_planning"])
        self.assertIsNone(result["candidate_set"])
        self.assertEqual(result["interpretation"].reply, "你好，今天想聊点什么？")

    def test_weather_intent_does_not_enter_planning(self) -> None:
        class WeatherRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.CHECK_WEATHER,
                    intent_scores={Intent.CHECK_WEATHER: 1.0},
                    raw_constraints=RawConstraints(date_text="今天"),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=WeatherRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-3",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "今天天气怎么样", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertFalse(result["ready_for_planning"])

    def test_weather_condition_with_plan_constraint_enters_existing_planning_chain(self) -> None:
        class ConditionalWeatherRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.CHECK_WEATHER,
                    intent_scores={Intent.CHECK_WEATHER: 1.0},
                    time_proposals=(_afternoon(),),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        preferences=["下雨就安排室内活动"],
                        scene_tags=["室内"],
                    ),
                    evidence_map={
                        "preferences": "下雨就安排室内活动",
                        "scene_tags": "室内活动",
                    },
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=ConditionalWeatherRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-weather-condition",
            identity_type=IdentityType.DEMO,
        )

        result = graph.invoke(
            {"user_input": "先看天气，要是下雨就安排室内活动", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertTrue(result["ready_for_planning"])
        self.assertIsNotNone(result["candidate_set"])
        self.assertTrue(result["candidate_set"].plans)

    def test_non_blocking_request_finishes_with_structured_candidate_plans(self) -> None:
        class PlanningRouter(InterpretationRouter):
            def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
                return Interpretation(
                    primary_intent=Intent.PLAN_OUTING,
                    intent_scores={Intent.PLAN_OUTING: 0.99},
                    time_proposals=(_afternoon(),),
                    raw_constraints=RawConstraints(
                        date_text="今天",
                        max_distance_text="别太远",
                    ),
                )

        environment = EnvironmentContext(
            now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        graph = build_entry_graph(
            router=PlanningRouter(),
            environment_provider=lambda _: environment,
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-entry-4",
            identity_type=IdentityType.DEMO,
        )

        config = {"configurable": {"thread_id": actor.session_id}}
        with self.assertNoLogs(level="WARNING"):
            result = graph.invoke(
                {"user_input": "今天下午出去玩，别太远", "actor": actor},
                config=config,
            )
            restored = graph.get_state(config).values["candidate_set"]

        self.assertTrue(result["ready_for_planning"])
        self.assertGreaterEqual(len(result["candidate_set"].plans), 1)
        self.assertEqual(len(result["candidate_set"].plans[0].stops), 2)
        self.assertEqual(restored, result["candidate_set"])

    def test_tonight_is_compiled_to_today_evening_without_a_date_question(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-tonight-contract",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {"user_input": "今晚只安排一家晚饭", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertTrue(result["ready_for_planning"])
        window = result["active_request"].planning_window
        self.assertEqual(window.date.value.isoformat(), "2026-08-12")
        self.assertEqual(window.start_at.value, "18:00")
        self.assertEqual(window.end_at.value, "22:00")
        self.assertTrue(result["candidate_set"].plans)

    def test_runtime_decisions_distinguish_demo_router_and_rule_planning(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-runtime-trace",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {"user_input": "今天下午出去玩", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        self.assertEqual(result["runtime_decisions"][0].adapter, "demo_rule")
        self.assertFalse(result["runtime_decisions"][0].model_invoked)
        self.assertEqual(result["runtime_decisions"][1].adapter, "rule_based")
        self.assertFalse(result["runtime_decisions"][1].model_invoked)

    def test_demo_return_deadline_question_resumes_with_a_bare_clock_answer(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-return-resume",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )
        config = {"configurable": {"thread_id": actor.session_id}}

        first_result = graph.invoke(
            {"user_input": "今天下午出去玩，晚饭前后一定要回家", "actor": actor},
            config=config,
        )
        self.assertEqual(first_result["__interrupt__"][0].value["field"], "return_by")

        question = first_result["__interrupt__"][0].value
        final_result = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "action": ClarificationAction.ANSWER.value,
                    "value": "18:00",
                    "request_revision": question["request_revision"],
                }
            ),
            config=config,
        )

        self.assertTrue(final_result["ready_for_planning"])
        self.assertIsNone(final_result["pending_issue"])
        self.assertTrue(final_result["candidate_set"].plans)
        self.assertTrue(
            all(
                plan.route_legs[-1].destination_name == "出发地"
                for plan in final_result["candidate_set"].plans
            )
        )

    def test_demo_unresolved_date_is_not_replaced_by_session_default(self) -> None:
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-unresolved-date",
            identity_type=IdentityType.DEMO,
        )
        graph = build_entry_graph(
            router=DemoRouter(),
            environment_provider=lambda _: environment,
        )

        result = graph.invoke(
            {"user_input": "等忙完那天带家里人出去", "actor": actor},
            config={"configurable": {"thread_id": actor.session_id}},
        )

        question = result["__interrupt__"][0].value
        self.assertEqual(question["field"], "date")
        self.assertEqual(
            question["rule_id"],
            "question.date.unresolved.v1",
        )
        resumed = graph.invoke(
            Command(
                resume={
                    "clarification_id": question["clarification_id"],
                    "request_revision": question["request_revision"],
                    "action": ClarificationAction.USE_DEFAULT.value,
                }
            ),
            config={"configurable": {"thread_id": actor.session_id}},
        )
        self.assertTrue(resumed["ready_for_planning"])
        self.assertEqual(
            resumed["active_request"].planning_window.date.value.isoformat(),
            "2026-08-15",
        )


if __name__ == "__main__":
    unittest.main()

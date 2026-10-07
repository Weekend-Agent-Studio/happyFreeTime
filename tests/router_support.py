"""Test-only router seam for deterministic Interpretation fixtures.

Production Routers return ``TurnInterpreterResult`` directly.  A handful of
graph tests intentionally author the older semantic projection because they
are testing downstream request/verification behavior, not model extraction.
This helper keeps that fixture concern out of the product graph.
"""

from __future__ import annotations

from app.domain.constraints import CommandOperation, Intent, Interpretation, TargetReference
from app.domain.turn import (
    CheckWeatherProposal,
    ChitchatProposal,
    CreatePlanProposal,
    PatchConstraintsProposal,
    QueryPlanProposal,
    ReplaceStopProposal,
    TurnCompiler,
    TurnProposal,
    TurnTargetProposal,
)
from app.domain.runtime import RuntimeDecision
from app.services.router_extractor import RouterContext, TurnInterpreterResult


class InterpretationRouter:
    """Adapt a test-authored Interpretation at the test composition root."""

    def interpret_with_runtime(
        self,
        user_input: str,
        context: RouterContext,
    ) -> TurnInterpreterResult:
        interpretation = self.interpret(user_input, context)
        command = interpretation.conversation_command
        if command is not None and command.operation == CommandOperation.PATCH_CONSTRAINTS:
            proposal = TurnProposal(
                act=PatchConstraintsProposal(
                    constraint_patch=command.constraint_patch,
                    evidence_map=dict(command.evidence),
                )
            )
        elif command is not None and command.operation == CommandOperation.REPLACE:
            target = command.target or TargetReference(raw_text="那个地方")

            def target_proposal(reference: TargetReference) -> TurnTargetProposal:
                return TurnTargetProposal(
                    role=reference.role,
                    resource_type=reference.resource_type,
                    stop_index=reference.stop_index,
                    raw_text=reference.raw_text,
                )

            proposal = TurnProposal(
                act=ReplaceStopProposal(
                    target=target_proposal(target),
                    locked_targets=tuple(
                        target_proposal(item) for item in command.locked_targets
                    ),
                    replacement_criteria=command.replacement_criteria,
                    evidence=dict(command.evidence),
                )
            )
        elif interpretation.primary_intent in {Intent.PLAN_OUTING, Intent.FIND_ACTIVITY}:
            proposal = TurnProposal(
                act=CreatePlanProposal(
                    raw_constraints=interpretation.raw_constraints,
                    time_proposals=interpretation.time_proposals,
                    evidence_map=dict(interpretation.evidence_map),
                )
            )
        elif interpretation.primary_intent == Intent.CHECK_WEATHER:
            proposal = TurnProposal(
                act=CheckWeatherProposal(
                    raw_constraints=interpretation.raw_constraints,
                    time_proposals=interpretation.time_proposals,
                    evidence_map=dict(interpretation.evidence_map),
                )
            )
        elif interpretation.primary_intent == Intent.QUERY_PLAN:
            proposal = TurnProposal(
                act=QueryPlanProposal(query=interpretation.reply or "当前方案")
            )
        elif interpretation.primary_intent == Intent.REFINE_PLAN:
            proposal = TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(
                        raw_text=interpretation.target_reference or "那个地方"
                    ),
                    evidence=dict(interpretation.evidence_map),
                )
            )
        else:
            proposal = TurnProposal(act=ChitchatProposal())

        compilation = TurnCompiler.compile(
            proposal,
            context=context.decision_context,
        )
        compilation = compilation.model_copy(
            update={
                "interpretation": compilation.interpretation.model_copy(
                    update={
                        "reply": interpretation.reply,
                        "requires_clarification": interpretation.requires_clarification,
                        "selected_plan_index": interpretation.selected_plan_index,
                        "extraction_confidence": dict(interpretation.extraction_confidence),
                        "inferred_fields": set(interpretation.inferred_fields),
                    }
                )
            }
        )
        return TurnInterpreterResult(
            interpretation=compilation.interpretation,
            action=compilation.action,
            runtime=RuntimeDecision(
                stage="turn_interpreter",
                adapter="test_fixture",
                model_invoked=False,
                attempts=0,
                latency_ms=0,
            ),
        )

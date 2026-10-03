"""Resolve non-constraint clarification actions and replacement targets.

Constraint answers belong to ``ClarificationPatchCompiler`` and
``ConstraintEngine``. This resolver only handles conversation control actions
and target references for a pending replacement; it never parses planning
constraints or edits the active request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.domain.catalog import ResourceType
from app.domain.constraints import (
    ClarificationAction,
    ClarificationReply,
    CommandOperation,
    ConversationCommand,
    Interpretation,
    PendingModification,
    QuestionDecision,
    StopRole,
    TargetReference,
)


class ClarificationResolution(str, Enum):
    RESOLVED = "resolved"
    USE_DEFAULT = "use_default"
    UNRESOLVED = "unresolved"
    CANCELLED = "cancelled"
    NEW_REQUEST = "new_request"
    MODIFICATION_RESOLVED = "modification_resolved"


@dataclass(frozen=True)
class ClarificationOutcome:
    status: ClarificationResolution
    interpretation: Interpretation
    question: QuestionDecision | None = None
    value: str | None = None


class ClarificationResolver:
    """Resolve cancel/new-request actions and replacement-target replies.

    Constraint fields are intentionally out of scope: an answer to a pending
    constraint issue must be compiled to a field-scoped ``RequestPatch`` and
    applied by ``ConstraintEngine`` in the Graph.
    """

    def resolve(
        self,
        *,
        pending_question: QuestionDecision,
        reply: ClarificationReply,
        base_interpretation: Interpretation,
        pending_modification: PendingModification | None = None,
    ) -> ClarificationOutcome:
        clarification_id = pending_question.clarification_id
        if clarification_id and reply.clarification_id != clarification_id:
            raise ValueError("stale clarification_id")

        action = reply.action
        value = (reply.value or "").strip() or None
        if action == ClarificationAction.ANSWER and value in {
            "按默认来吧", "按默认", "用默认", "使用默认出发地", "默认地点",
        }:
            action = ClarificationAction.USE_DEFAULT
        if action == ClarificationAction.ANSWER and value in {"取消", "算了", "先不规划了"}:
            action = ClarificationAction.CANCEL

        if action == ClarificationAction.CANCEL:
            return ClarificationOutcome(
                status=ClarificationResolution.CANCELLED,
                interpretation=base_interpretation.model_copy(
                    update={
                        "reply": "已取消本轮补充，你可以直接描述新的规划需求。",
                        "requires_clarification": False,
                    }
                ),
            )

        if action == ClarificationAction.NEW_REQUEST:
            if not value:
                return self._unresolved(pending_question, base_interpretation)
            return ClarificationOutcome(
                status=ClarificationResolution.NEW_REQUEST,
                interpretation=base_interpretation,
                value=value,
            )

        if (
            action != ClarificationAction.ANSWER
            or not value
            or not pending_question.allow_free_text
            or pending_question.issue_kind != "target"
            or pending_question.field not in {"target_reference", "conversation_command"}
            or pending_modification is None
            or pending_modification.operation != "replace"
        ):
            return self._unresolved(pending_question, base_interpretation)

        target = self._parse_target_reference(value)
        if target is None:
            return self._unresolved(pending_question, base_interpretation)

        command = ConversationCommand(
            operation=CommandOperation.REPLACE,
            target=target,
            locked_targets=pending_modification.locked_targets,
            constraint_patch=pending_modification.constraint_patch,
            replacement_criteria=pending_modification.replacement_criteria,
            evidence={**pending_modification.evidence, "target": value},
        )
        updated = base_interpretation.model_copy(
            update={
                "target_reference": value,
                "conversation_command": command,
                "reply": "",
                "requires_clarification": False,
            }
        )
        return ClarificationOutcome(
            status=ClarificationResolution.MODIFICATION_RESOLVED,
            interpretation=updated,
            value=value,
        )

    @staticmethod
    def _parse_target_reference(value: str) -> TargetReference | None:
        normalized = re.sub(r"\s+", "", value).strip("，。！？；：")
        role_aliases = {
            "活动": StopRole.ACTIVITY,
            "项目": StopRole.ACTIVITY,
            "景点": StopRole.ACTIVITY,
            "午饭": StopRole.LUNCH,
            "午餐": StopRole.LUNCH,
            "晚饭": StopRole.DINNER,
            "晚餐": StopRole.DINNER,
            "吃饭": StopRole.MEAL,
        }
        if normalized in role_aliases:
            return TargetReference(role=role_aliases[normalized], raw_text=value)
        if normalized in {"餐厅", "饭店"}:
            return TargetReference(resource_type=ResourceType.RESTAURANT, raw_text=value)
        index_aliases = {
            "第一站": 0,
            "第1站": 0,
            "第二站": 1,
            "第2站": 1,
            "第三站": 2,
            "第3站": 2,
            "第四站": 3,
            "第4站": 3,
        }
        if normalized in index_aliases:
            return TargetReference(stop_index=index_aliases[normalized], raw_text=value)
        return None

    @staticmethod
    def _unresolved(
        decision: QuestionDecision,
        interpretation: Interpretation,
    ) -> ClarificationOutcome:
        next_attempt = min(decision.max_attempts, decision.attempt + 1)
        question = decision.model_copy(
            update={
                "attempt": next_attempt,
                "allow_free_text": next_attempt < decision.max_attempts,
            }
        )
        return ClarificationOutcome(
            status=ClarificationResolution.UNRESOLVED,
            interpretation=interpretation,
            question=question,
        )

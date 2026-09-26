"""Field-scoped clarification resolution.

The resolver deliberately updates only the field that Gate asked for.  It is
not a second general Router pass: an answer to a return-time question cannot
silently replace the date, roles, preferences, or conversation intent already
held in the checkpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from app.domain.constraints import (
    ClarificationAction,
    ClarificationReply,
    ConversationCommand,
    CommandOperation,
    Interpretation,
    PendingModification,
    QuestionDecision,
    RawConstraints,
    TargetReference,
    StopRole,
)
from app.domain.catalog import ResourceType


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
    defaulted_field: str | None = None
    value: str | None = None
    command: ConversationCommand | None = None


AnswerInterpreter = Callable[[str], Interpretation]


class ClarificationResolver:
    """Resolve one answer against one pending ``QuestionDecision``.

    Parsing is intentionally finite and field-directed.  The optional
    ``answer_interpreter`` is a compatibility seam for adapters that need
    model assistance, but its result is projected onto the pending field only
    and can never change the original intent or unrelated constraints.
    """

    _DEFAULTABLE_FIELDS = frozenset(
        {
            "location",
            "date",
            "time_window",
            "max_distance_km",
            "total_distance_km",
            "party",
        }
    )

    def resolve(
        self,
        *,
        pending_question: QuestionDecision,
        reply: ClarificationReply,
        base_interpretation: Interpretation,
        pending_modification: PendingModification | None = None,
        answer_interpreter: AnswerInterpreter | None = None,
    ) -> ClarificationOutcome:
        clarification_id = pending_question.clarification_id
        if clarification_id and reply.clarification_id != clarification_id:
            raise ValueError("stale clarification_id")

        action = reply.action
        value = (reply.value or "").strip() or None
        # Old clients only know how to send text.  Keep the useful ergonomic
        # aliases while the structured UI remains the source of truth.
        if action == ClarificationAction.ANSWER and value in {
            "按默认来吧",
            "按默认",
            "用默认",
            "使用默认出发地",
            "默认地点",
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

        if action == ClarificationAction.USE_DEFAULT:
            if pending_question.field not in self._DEFAULTABLE_FIELDS:
                return self._unresolved(pending_question, base_interpretation)
            raw = base_interpretation.raw_constraints
            updates: dict[str, object] = {}
            if pending_question.field == "location":
                updates["location_text"] = None
            elif pending_question.field == "date":
                updates.update(
                    {
                        "date_text": None,
                        "date_reference": None,
                        "weekday": None,
                        "week_offset": None,
                        "absolute_date": None,
                    }
                )
            elif pending_question.field == "time_window":
                updates.update(
                    {
                        "time_text": None,
                        "time_scope": None,
                        "explicit_time_window": None,
                        "departure_period": None,
                    }
                )
            else:
                # Clearing a field lets Enrichment apply its visible product
                # default.  Do not manufacture a user-explicit value here.
                updates[pending_question.field] = None
            updated = self._update_interpretation(
                base_interpretation,
                raw_updates=updates,
                evidence_remove=(pending_question.field, f"{pending_question.field}_text"),
                inferred_remove=(pending_question.field,),
            )
            return ClarificationOutcome(
                status=ClarificationResolution.USE_DEFAULT,
                interpretation=updated,
                defaulted_field=pending_question.field,
            )

        if action != ClarificationAction.ANSWER or not value:
            return self._unresolved(pending_question, base_interpretation)
        if not pending_question.allow_free_text:
            return self._unresolved(pending_question, base_interpretation)

        updated = self._project_answer(
            pending_question.field,
            value,
            base_interpretation,
        )
        if pending_question.field in {"target_reference", "conversation_command"} and pending_modification is not None:
            target = self._parse_target_reference(value)
            if target is not None:
                command = ConversationCommand(
                    operation=CommandOperation.REPLACE,
                    target=target,
                    locked_targets=pending_modification.locked_targets,
                    constraint_patch=pending_modification.constraint_patch,
                    replacement_criteria=pending_modification.replacement_criteria,
                    evidence={
                        **pending_modification.evidence,
                        "target": value,
                    },
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
                    command=command,
                )
        if (
            pending_modification is not None
            and pending_modification.operation == "patch_constraints"
            and (
                pending_question.continuation == "patch_constraints"
                or pending_question.field == "constraint_patch"
            )
        ):
            patch_field = self._patch_field_for_question(
                pending_question.field,
                pending_question.rule_id,
            )
            if patch_field is not None:
                patch_value: object = value
                update: dict[str, object] = {patch_field: patch_value}
                if pending_question.field in {"preferences", "diet_tags", "avoid"}:
                    update[patch_field] = (value,)
                if pending_question.field == "strict_budget":
                    if value in {"不限", "预算不限", "不设预算", "取消预算"}:
                        update = {
                            "strict_budget": False,
                            "clear_fields": ("budget_per_person", "strict_budget"),
                        }
                    else:
                        return self._unresolved(pending_question, base_interpretation)
                patch = pending_modification.constraint_patch.model_copy(update=update)
                command = ConversationCommand(
                    operation=CommandOperation.PATCH_CONSTRAINTS,
                    constraint_patch=patch,
                    evidence={**pending_modification.evidence, "patch": value},
                )
                updated = base_interpretation.model_copy(
                    update={
                        "conversation_command": command,
                        "reply": "",
                        "requires_clarification": False,
                    }
                )
                return ClarificationOutcome(
                    status=ClarificationResolution.MODIFICATION_RESOLVED,
                    interpretation=updated,
                    value=value,
                    command=command,
                )
        if updated is None and answer_interpreter is not None:
            # This optional path is still field-scoped: the model's other
            # fields are discarded by ``_project_answer_interpretation``.
            try:
                proposed = answer_interpreter(value)
            except Exception:
                proposed = None
            if proposed is not None:
                updated = self._project_answer_interpretation(
                    pending_question.field,
                    proposed,
                    base_interpretation,
                    value,
                )
        if updated is None:
            return self._unresolved(pending_question, base_interpretation)
        return ClarificationOutcome(
            status=ClarificationResolution.RESOLVED,
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
    def _patch_field_for_question(field: str | None, rule_id: str | None = None) -> str | None:
        if field == "constraint_patch" and rule_id:
            field = {
                "question.patch.date.v1": "date",
                "question.patch.time_window.v1": "time_window",
                "question.patch.departure_at.v1": "departure_at",
                "question.patch.return_by.v1": "return_by",
                "question.patch.location.v1": "location",
                "question.patch.budget.v1": "budget_per_person",
                "question.patch.distance.v1": "max_distance_km",
                "question.patch.total_distance.v1": "total_distance_km",
            }.get(rule_id)
        return {
            "date": "date_text",
            "time_window": "time_window_text",
            "departure_at": "departure_at_text",
            "return_by": "return_by_text",
            "location": "location_text",
            "budget_per_person": "budget_text",
            "max_distance_km": "max_distance_text",
            "total_distance_km": "total_distance_text",
            "preferences": "preferences",
            "diet_tags": "diet_tags",
            "avoid": "avoid",
            "strict_budget": "strict_budget",
        }.get(field or "")

    def _unresolved(
        self,
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

    @classmethod
    def _project_answer(
        cls,
        field: str,
        value: str,
        base: Interpretation,
    ) -> Interpretation | None:
        raw_updates: dict[str, object] = {}
        evidence_key = field

        if field == "budget_per_person":
            amount = cls._parse_amount(value)
            if amount is None or amount <= 0:
                return None
            raw_updates.update({"budget_text": value, "budget_per_person": amount})
            evidence_key = "budget_per_person"
        elif field == "return_by":
            clock = cls._parse_clock(value)
            if clock is None:
                return None
            raw_updates.update({"return_by_text": value, "return_by": clock})
            evidence_key = "return_by_text"
        elif field == "departure_at":
            clock = cls._parse_clock(value)
            if clock is None:
                return None
            raw_updates.update({"departure_at_text": value, "departure_at": clock})
            evidence_key = "departure_at_text"
        elif field == "date":
            from app.services.enrichment import TemporalCompiler

            date_text, reference, weekday, offset, absolute = TemporalCompiler.extract_date(value)
            if date_text is None:
                return None
            raw_updates.update(
                {
                    "date_text": date_text,
                    "date_reference": reference,
                    "weekday": weekday,
                    "week_offset": offset,
                    "absolute_date": absolute,
                }
            )
            evidence_key = "date_text"
        elif field == "time_window":
            from app.services.enrichment import TemporalCompiler

            time_text, scope, window = TemporalCompiler.extract_time(value)
            if time_text is None:
                # A bare clock is not a range and must not silently become a
                # planning horizon; ask again for a window.
                return None
            raw_updates.update(
                {
                    "time_text": time_text,
                    "time_scope": scope,
                    "explicit_time_window": window,
                }
            )
            evidence_key = "time_text"
        elif field == "location":
            if len(value) < 2:
                return None
            raw_updates["location_text"] = value
            evidence_key = "location_text"
        elif field == "max_distance_km":
            distance = cls._parse_float(value)
            if distance is None or distance <= 0:
                return None
            raw_updates.update({"max_distance_text": value, "max_distance_km": distance})
            evidence_key = "max_distance_text"
        elif field == "total_distance_km":
            distance = cls._parse_float(value)
            if distance is None or distance <= 0:
                return None
            raw_updates.update({"total_distance_text": value, "total_distance_km": distance})
            evidence_key = "total_distance_km"
        elif field == "child_age":
            age = cls._parse_int(value)
            if age is None or not 0 <= age <= 17:
                return None
            raw_updates["child_age"] = age
            evidence_key = "child_age"
        elif field == "party":
            count = cls._parse_int(value)
            if count is not None and count > 0:
                raw_updates["adults"] = count
            elif len(value) >= 2:
                raw_updates["members"] = [value]
            else:
                return None
            evidence_key = "party"
        elif field == "selected_plan_index":
            index = cls._parse_plan_index(value)
            if index is None:
                return None
            # This is consumed by the execution path; it is not a planning
            # constraint.  Keeping it in the raw Interpretation is outside the
            # current MT0 scope, so let the existing Gate ask again safely.
            return None
        else:
            return None

        evidence = dict(base.evidence_map)
        evidence[evidence_key] = value
        confidence = dict(base.extraction_confidence)
        confidence[evidence_key] = 1.0
        inferred = set(base.inferred_fields)
        if field == "party":
            inferred.discard("party")
        return cls._update_interpretation(
            base,
            raw_updates=raw_updates,
            evidence=evidence,
            confidence=confidence,
            inferred=inferred,
        )

    @classmethod
    def _project_answer_interpretation(
        cls,
        field: str,
        proposed: Interpretation,
        base: Interpretation,
        evidence: str,
    ) -> Interpretation | None:
        source = proposed.raw_constraints
        updates: dict[str, object] = {}
        if field == "location" and source.location_text:
            updates["location_text"] = source.location_text
        elif field == "date" and source.date_text:
            updates.update(
                {
                    "date_text": source.date_text,
                    "date_reference": source.date_reference,
                    "weekday": source.weekday,
                    "week_offset": source.week_offset,
                    "absolute_date": source.absolute_date,
                }
            )
        elif field == "return_by" and (source.return_by or source.return_by_text):
            updates.update({"return_by": source.return_by, "return_by_text": source.return_by_text or evidence})
        elif field == "departure_at" and (source.departure_at or source.departure_at_text):
            updates.update({"departure_at": source.departure_at, "departure_at_text": source.departure_at_text or evidence})
        else:
            return None
        return cls._update_interpretation(
            base,
            raw_updates=updates,
            evidence={**base.evidence_map, field: evidence},
            confidence={**base.extraction_confidence, field: 1.0},
        )

    @staticmethod
    def _update_interpretation(
        base: Interpretation,
        *,
        raw_updates: dict[str, object],
        evidence: dict[str, str] | None = None,
        confidence: dict[str, float] | None = None,
        inferred: set[str] | None = None,
        evidence_remove: tuple[str, ...] = (),
        inferred_remove: tuple[str, ...] = (),
    ) -> Interpretation:
        raw = base.raw_constraints.model_copy(update=raw_updates)
        next_evidence = dict(base.evidence_map if evidence is None else evidence)
        for key in evidence_remove:
            next_evidence.pop(key, None)
        next_confidence = dict(base.extraction_confidence if confidence is None else confidence)
        for key in evidence_remove:
            next_confidence.pop(key, None)
        next_inferred = set(base.inferred_fields if inferred is None else inferred)
        next_inferred.difference_update(inferred_remove)
        return base.model_copy(
            update={
                "raw_constraints": RawConstraints.model_validate(raw),
                "evidence_map": next_evidence,
                "extraction_confidence": next_confidence,
                "inferred_fields": next_inferred,
                "reply": "",
                "requires_clarification": False,
            }
        )

    @staticmethod
    def _parse_float(value: str) -> float | None:
        match = re.search(r"\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None

    @classmethod
    def _parse_int(cls, value: str) -> int | None:
        match = re.search(r"\d+", value)
        if match:
            return int(match.group(0))
        chinese = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        for phrase, number in chinese.items():
            if phrase in value:
                return number
        return None

    @classmethod
    def _parse_amount(cls, value: str) -> int | None:
        return cls._parse_int(value)

    @classmethod
    def _parse_clock(cls, value: str) -> str | None:
        match = re.search(
            r"(?P<period>上午|早上|中午|下午|晚上|夜里|夜间)?\s*"
            r"(?P<hour>\d{1,2}|[一二三四五六七八九十两]+)"
            r"(?:[:：](?P<minute>\d{1,2})|点(?P<half>半)|点(?P<cnminute>[零〇一二三四五六七八九十两]+)分?|点)?",
            value,
        )
        if not match:
            return None
        hour_text = match.group("hour")
        # Do not interpret the first Chinese numeral in phrases such as
        # “一整天” or “两站” as a clock.  Chinese hour forms must carry the
        # explicit “点” marker; Arabic digits remain compatible with the
        # concise answer “9”.
        if not hour_text.isdigit() and "点" not in match.group(0):
            return None
        hour = int(hour_text) if hour_text.isdigit() else cls._parse_int(hour_text)
        if hour is None:
            return None
        minute_text = match.group("minute")
        if minute_text is not None:
            minute = int(minute_text)
        elif match.group("half"):
            minute = 30
        elif match.group("cnminute"):
            minute = cls._parse_int(match.group("cnminute"))
        else:
            minute = 0
        if minute is None or not 0 <= minute <= 59:
            return None
        period = match.group("period")
        if period in {"下午", "晚上", "夜里", "夜间"} and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        if not 0 <= hour <= 23:
            return None
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _parse_plan_index(value: str) -> int | None:
        match = re.search(r"第\s*(\d+)\s*(?:个|项|个方案)?", value)
        if match:
            return max(0, int(match.group(1)) - 1)
        return None

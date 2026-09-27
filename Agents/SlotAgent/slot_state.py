"""Small, deterministic state protocol for the original SlotAgent.

This module deliberately does not introduce the V2 compiler or question gate.
It only makes the old interrupt/resume contract explicit: preserve confirmed
slots and merge one answer into the field that is currently pending.
"""

from copy import deepcopy
import re
from typing import Any, Dict, Optional


_QUESTION_PATTERNS = (
    ("date", re.compile(r"日期|哪天|哪一天|什么时候出行|出行时间")),
    ("location", re.compile(r"从哪里|出发地|地点|位置|地标|附近|具体.*地方")),
    ("budget", re.compile(r"预算|人均|花费|多少钱|费用")),
    ("companions", re.compile(r"同行|几位|人数|谁一起|几个人")),
    ("preferences", re.compile(r"偏好|喜欢|风格|想要什么|有什么要求")),
    ("time_hint", re.compile(r"几点|出发|返回|回家|时段|时间范围|持续多久|玩多久")),
)

_EMPTY_ANSWERS = {"", "不知道", "不清楚", "说不清", "随便吧", "都可以", "你看着办"}


def question_field(question: str) -> Optional[str]:
    """Classify the old SlotAgent's question without guessing a value."""
    text = str(question or "")
    for field, pattern in _QUESTION_PATTERNS:
        if pattern.search(text):
            return field
    return None


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def merge_slot_state(previous: Dict[str, Any], update: Dict[str, Any], target_field: Optional[str] = None) -> Dict[str, Any]:
    """Merge a SlotAgent result while preserving all non-target fields.

    The original model still emits a complete slot object.  Only the pending
    field is trusted on resume; this prevents an answer such as "九点" from
    erasing a previously confirmed date, location, or companion profile.
    """
    merged = deepcopy(previous or {})
    incoming = update or {}
    if target_field:
        if target_field in incoming and _has_value(incoming[target_field]):
            merged[target_field] = deepcopy(incoming[target_field])
        return merged
    for key, value in incoming.items():
        if _has_value(value):
            merged[key] = deepcopy(value)
    return merged


def _coerce_answer(field: str, answer: str) -> Any:
    text = str(answer or "").strip()
    if text in _EMPTY_ANSWERS:
        return None
    if field == "location":
        return {"address": text}
    if field == "companions":
        return {"raw": text}
    if field == "preferences":
        return {"raw": text}
    return text


def apply_answer(state: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """Apply one resume answer to a pending field with explicit outcomes."""
    result = deepcopy(state or {})
    result.setdefault("slot", {})
    result.setdefault("answered_fields", [])
    result.setdefault("clarification_round", 0)
    result.setdefault("max_clarification_rounds", 3)
    pending = result.get("pending_field")

    if result["clarification_round"] >= result["max_clarification_rounds"]:
        result["status"] = "clarification_limit_reached"
        return result
    if not pending:
        result["status"] = "unclassified_question"
        return result
    if pending in result["answered_fields"]:
        result["status"] = "repeated_clarification"
        return result

    value = _coerce_answer(pending, answer)
    result["clarification_round"] += 1
    if value is None:
        result["status"] = "unclassified_question"
        return result

    result["slot"][pending] = value
    result["answered_fields"].append(pending)
    result["status"] = "resolved"
    return result


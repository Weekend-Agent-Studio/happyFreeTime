import pytest

from Agents.SlotAgent.slot_state import (
    apply_answer,
    merge_slot_state,
    question_field,
)


def test_question_field_classifies_supported_slot_questions():
    assert question_field("请问哪一天出行？") == "date"
    assert question_field("大概几点出发或回来？") == "time_hint"
    assert question_field("从哪里出发？") == "location"
    assert question_field("预算大概是多少？") == "budget"
    assert question_field("有几位同行？") == "companions"


def test_merge_slot_state_only_updates_target_field():
    previous = {
        "date": "这周六",
        "location": {"address": "国贸"},
        "time_hint": "14:00-18:00",
        "budget": "人均200",
        "companions": {"members": ["父母"]},
        "preferences": {"tags": ["安静"]},
    }
    model_update = {
        "date": "明天",
        "location": {"address": "望京"},
        "time_hint": "09:00-12:00",
        "budget": "无限",
        "companions": {"members": ["陌生人"]},
        "preferences": {"tags": ["嘈杂"]},
    }

    merged = merge_slot_state(previous, model_update, target_field="budget")

    assert merged["budget"] == "无限"
    assert merged["date"] == "这周六"
    assert merged["location"] == {"address": "国贸"}
    assert merged["companions"] == {"members": ["父母"]}
    assert merged["preferences"] == {"tags": ["安静"]}


def test_apply_answer_marks_valid_field_and_rejects_repeat_or_unknown():
    state = {
        "slot": {"date": "", "budget": ""},
        "pending_field": "date",
        "answered_fields": [],
        "clarification_round": 0,
        "max_clarification_rounds": 3,
    }

    updated = apply_answer(state, "这周六")
    assert updated["slot"]["date"] == "这周六"
    assert updated["answered_fields"] == ["date"]
    assert updated["status"] == "resolved"

    repeated = dict(updated)
    repeated["pending_field"] = "date"
    repeated = apply_answer(repeated, "下周六")
    assert repeated["status"] == "repeated_clarification"
    assert repeated["slot"]["date"] == "这周六"

    unknown = dict(updated)
    unknown["pending_field"] = "budget"
    unknown = apply_answer(unknown, "随便吧")
    assert unknown["status"] == "unclassified_question"
    assert unknown["slot"]["budget"] == ""


def test_apply_answer_returns_explicit_failure_at_max_rounds():
    state = {
        "slot": {"date": ""},
        "pending_field": "date",
        "answered_fields": [],
        "clarification_round": 2,
        "max_clarification_rounds": 2,
    }
    result = apply_answer(state, "说不清")
    assert result["status"] == "clarification_limit_reached"
    assert result["slot"]["date"] == ""


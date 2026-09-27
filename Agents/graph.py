from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Dict, Any

from Agents.IntentAgent.intent_agent import classify_intent
from Agents.SlotAgent.slot_agent import run_slot
from Agents.SlotAgent.slot_state import apply_answer, question_field
from Agents.PlannerAgent.planner_agent import run_planner
from Agents.ExecutorAgent.executor_agent import run_executor
from langgraph.types import interrupt

class State(TypedDict):
    user_input: str
    intent: str
    intents: dict
    reply: str
    slot: dict
    plans: dict
    execution: dict
    selected_index: int
    pending_field: str
    missing_required_fields: list
    clarification_round: int
    max_clarification_rounds: int
    answered_fields: list
    last_answered_field: str


def intent_node(state: State):
    has_plans = bool(state.get("plans", {}).get("plans"))
    result = classify_intent(state["user_input"], state.get("intents", {}), state.get("reply", ""), has_plans=has_plans)
    return {
        "intent": result["primary"],
        "intents": result["intents"],
        "reply": result.get("reply", ""),
        "selected_index": result.get("selected_index", -2),
    }


def slot_node(state: State):
    answered_fields = state.get("answered_fields", [])
    last_answered_field = state.get("last_answered_field") or None
    slot = run_slot(
        state['user_input'], state['intent'], state['intents'],
        previous_slot=state.get("slot", {}),
        answered_fields=answered_fields,
        merge_target_field=last_answered_field,
    )

    # 解析失败时内部重试，避免反问用户
    if not slot['is_complete'] and slot.get('ask_question', '').startswith('解析失败'):
        slot = run_slot(
            state['user_input'], state['intent'], state['intents'],
            previous_slot=state.get("slot", {}),
            answered_fields=answered_fields,
            merge_target_field=last_answered_field,
        )

    if not slot['is_complete']:
        if slot.get("error_code"):
            return {
                "slot": slot,
                "reply": "无法稳定识别需要补充的信息，本轮已停止，请重新描述需求。",
                "pending_field": "",
                "last_answered_field": "",
            }
        pending = question_field(slot.get("ask_question", ""))
        if not pending:
            return {
                "slot": slot,
                "reply": "无法确定需要补充的字段，本轮已停止，请换一种说法。",
                "pending_field": "",
                "last_answered_field": "",
            }
        clarification_state = {
            "slot": slot,
            "pending_field": pending,
            "answered_fields": answered_fields,
            "clarification_round": state.get("clarification_round", 0),
            "max_clarification_rounds": state.get("max_clarification_rounds", 3),
        }
        user_answer = interrupt({"question": slot["ask_question"], "field": pending})
        answer_state = apply_answer(clarification_state, user_answer)
        if answer_state["status"] != "resolved":
            messages = {
                "repeated_clarification": "同一信息被重复询问，本轮已停止。",
                "unclassified_question": "这次回答无法归类到当前问题，本轮已停止。",
                "clarification_limit_reached": "补充次数已达到上限，本轮已停止。",
            }
            return {
                "slot": slot,
                "reply": messages.get(answer_state["status"], "本轮无法继续。"),
                "pending_field": pending,
                "clarification_round": answer_state["clarification_round"],
                "answered_fields": answer_state["answered_fields"],
                "last_answered_field": "",
            }
        return {
            "user_input": state["user_input"] + "\n用户补充：" + str(user_answer),
            "slot": answer_state["slot"],
            "pending_field": "",
            "clarification_round": answer_state["clarification_round"],
            "answered_fields": answer_state["answered_fields"],
            "last_answered_field": pending,
        }

    return {"slot": slot, "pending_field": "", "last_answered_field": "", "missing_required_fields": []}

def planner_node(state: State):
    plans = run_planner(
        state['user_input'], state['intent'], state['intents'], state['slot'],
        prev_plans=state.get('plans'),
        prev_execution=state.get('execution'),
    )
    return {"plans": plans, "execution": {}}


def executor_node(state: State):
    execution = run_executor(
        state['user_input'], state['intent'], state['intents'],
        state.get('plans', {}), state.get('slot', {}),
        state.get('selected_index', -2),
    )
    return {"execution": execution}
   
graph = StateGraph(State)
graph.add_node("intent", intent_node)
graph.add_node("slot", slot_node)
graph.add_node("planner", planner_node)
graph.add_node("executor", executor_node)
graph.set_entry_point("intent")


def route_after_intent(state: State):
    if state["intent"] == "chitchat":
        return END
    elif state["intent"] == "refine_plan" and state.get("slot", {}).get("is_complete"):
        return "planner"
    elif state["intent"] == "confirm_execution" and state.get("plans", {}).get("plans"):
        return "executor"
    return "slot"


def route_after_slot(state: State):
    if state.get("reply"):
        return END
    slot = state.get("slot", {})
    if slot.get("is_complete"):
        if state["intent"] == "check_weather":
            return END
        return "planner"
    return "slot"


graph.add_conditional_edges("intent", route_after_intent, {END: END, "slot": "slot", "planner": "planner", "executor": "executor"})
graph.add_conditional_edges("slot", route_after_slot, {END: END, "planner": "planner", "slot": "slot"})
graph.add_edge("planner", END)
graph.add_edge("executor", END)


from langgraph.checkpoint.memory import MemorySaver


app = graph.compile(checkpointer=MemorySaver())


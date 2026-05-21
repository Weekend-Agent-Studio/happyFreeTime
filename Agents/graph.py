from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Dict, Any

from Agents.IntentAgent.intent_agent import classify_intent
from Agents.SlotAgent.slot_agent import run_slot
from Agents.PlannerAgent.planner_agent import run_planner
from langgraph.types import interrupt

class State(TypedDict):
    user_input: str
    intent: str
    intents: dict
    slot: dict
    plans: dict


def intent_node(state: State):
    result = classify_intent(state["user_input"])
    return {"intent": result["primary"], "intents": result["intents"]}


def slot_node(state: State):
    slot = run_slot(state['user_input'], state['intent'], state['intents'])
    # 把 slot 结果写回 state
    if not slot['is_complete']:
        user_answer = interrupt({"question": slot["ask_question"]})
        return {
            "user_input": state["user_input"] + "\n用户补充：" + user_answer,
            "slot": slot
        }

    return {"slot": slot}

def planner_node(state: State):
    plans = run_planner(state['user_input'], state['intent'], state['intents'], state['slot'])
    return {"plans": plans}
   
graph = StateGraph(State)
graph.add_node("intent", intent_node)
graph.add_node("slot", slot_node)
graph.add_node("planner", planner_node)
graph.set_entry_point("intent")


def route_after_intent(state: State):
    if state["intent"] == "chitchat":
        return END
    elif state["intent"] == "refine_plan" and state["slot"].get("is_complete"):
        return "planner"
    return "slot"


def route_after_slot(state: State):
    slot = state.get("slot", {})
    if slot.get("is_complete"):
        return "planner"
    return "slot"


graph.add_conditional_edges("intent", route_after_intent, {END: END, "slot": "slot", "planner": "planner"})
graph.add_conditional_edges("slot", route_after_slot, {"planner": "planner", "slot": "slot"})
graph.add_edge("planner", END)


from langgraph.checkpoint.memory import MemorySaver


app = graph.compile(checkpointer=MemorySaver())


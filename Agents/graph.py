from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Dict, Any

from Agents.IntentAgent.intent_agent import classify_intent
from Agents.slot_agent import run_slot
from langgraph.types import interrupt

class State(TypedDict):
    user_input: str
    intent: str
    intents: dict
    slot: dict


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

   
graph = StateGraph(State)
graph.add_node("intent", intent_node)
graph.add_node("slot", slot_node)
graph.set_entry_point("intent")


def route_after_intent(state: State):
    if state["intent"] == "chitchat":
        return END
    return "slot"


def route_after_slot(state: State):
    slot = state.get("slot", {})
    if slot.get("is_complete"):
        return END
    return "slot"


graph.add_conditional_edges("intent", route_after_intent, {END: END, "slot": "slot"})
graph.add_conditional_edges("slot", route_after_slot, {END: END, "slot": "slot"})


from langgraph.checkpoint.memory import MemorySaver


app = graph.compile(checkpointer=MemorySaver())


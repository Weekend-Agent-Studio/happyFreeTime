from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Dict, Any

from Agents.intent_agent import build_intent_agent()


class State(TypedDict):
    # 用户输入
    user_input: str

    # 意图识别结果
    intent: str
    slots: dict



def intent_node(state: State):
    agent = build_intent_agent()
    result = agent.invoke({"user_input": state["user_input"]})
    return {
        "intent": result["intent"], 
        "slots": result["slots"]
        }



   



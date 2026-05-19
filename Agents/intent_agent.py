"""
意图识别 Agent —— 短时活动规划系统的入口节点
分析用户输入，识别意图类型并提取关键槽位。
"""

import json
import os
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

load_dotenv()


class Intent(str, Enum):
    PLAN_OUTING = "plan_outing"
    FIND_ACTIVITY = "find_activity"
    CHECK_WEATHER = "check_weather"
    REFINE_PLAN = "refine_plan"
    CHITCHAT = "chitchat"


class IntentState(TypedDict):
    user_input: str
    intent: Optional[str]
    confidence: Optional[float]
    reasoning: Optional[str]
    response: Optional[str]


SYSTEM_PROMPT = """你是短时活动规划助手的意图识别模块。只做一件事：判断用户意图。

意图类型：
- plan_outing: 用户想规划短时外出活动安排
- find_activity: 用户想查找特定类型的活动
- check_weather: 用户想查询天气
- refine_plan: 用户想调整已有计划
- chitchat: 闲聊或其他无关话题

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
{"intent":"意图类型","confidence":0.0-1.0,"reasoning":"简短理由"}
"""


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-chat"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
    )


def _parse_result(raw: str, fallback_intent: str = "chitchat") -> dict:
    """从 LLM 原始输出中提取 JSON，增加容错。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if lines[0].startswith("```") else text
        if text.endswith("```"):
            text = text[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "intent": fallback_intent,
            "confidence": 0.0,
            "reasoning": "JSON 解析失败",
        }


def _intent_node(state: IntentState) -> IntentState:
    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=state["user_input"]),
    ]
    response = llm.invoke(messages)
    parsed = _parse_result(response.content)

    state["intent"] = parsed.get("intent", "chitchat")
    state["confidence"] = parsed.get("confidence", 0.0)
    state["reasoning"] = parsed.get("reasoning", "")
    state["response"] = response.content
    return state


def build_intent_agent():
    """构建意图识别 Graph（当前只有单节点，后续会加条件边）。"""
    graph = StateGraph(IntentState)
    graph.add_node("intent", _intent_node)
    graph.set_entry_point("intent")
    graph.add_edge("intent", END)
    return graph.compile()


def classify_intent(user_input: str) -> IntentState:
    """对外快捷接口：输入用户文本，返回分类结果。"""
    agent = build_intent_agent()
    initial: IntentState = {
        "user_input": user_input,
        "intent": None,
        "confidence": None,
        "reasoning": None,
        "response": None,
    }
    return agent.invoke(initial)

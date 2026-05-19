"""
意图识别 —— 短时活动规划系统的入口
纯 LangChain 实现，一次 LLM 调用完成分类。
"""

import json
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

load_dotenv()


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
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
    )


def _parse_result(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if lines[0].startswith("```") else text
        if text.endswith("```"):
            text = text[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"intent": "chitchat", "confidence": 0.0, "reasoning": "JSON 解析失败"}


def classify_intent(user_input: str) -> dict:
    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_input),
    ]
    response = llm.invoke(messages)
    return _parse_result(response.content)

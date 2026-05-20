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


SYSTEM_PROMPT = """你是短时活动规划助手的意图识别模块。判断用户可能包含的所有意图，一句用户输入可能同时涉及多个意图。

意图类型：
- plan_outing: 用户想规划短时外出活动安排
- find_activity: 用户想查找特定类型的活动/餐厅/场所
- check_weather: 用户想查询天气
- refine_plan: 用户想调整已有计划
- chitchat: 闲聊或与上述无关的话题

规则：
- intents 中为每个意图给出 0.0-1.0 的置信度，仅列出置信度 > 0.3 的意图
- primary 是最主要的意图（置信度最高的那个）
- 纯闲聊时 intents 只包含 chitchat

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
{"intents":{"plan_outing":0.9,"check_weather":0.4},"primary":"plan_outing","reasoning":"简短理由"}
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
        return {"intents": {"chitchat": 1.0}, "primary": "chitchat", "reasoning": "JSON 解析失败"}


def classify_intent(user_input: str) -> dict:
    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_input),
    ]
    response = llm.invoke(messages)
    return _parse_result(response.content)

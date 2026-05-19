"""
事项补全 Agent
接收意图识别结果，通过 LLM + tool calling 补充缺失信息，
无法自动补全的生成反问让用户回答。
"""

import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from Tools import get_cur_loc, get_cur_time, get_cur_weather
from langgraph.prebuilt import create_react_agent

load_dotenv()


class SlotState(TypedDict):
    user_input: str
    intent: str

    date: str
    location: Dict[str, Any]
    weather: Dict[str, Any]
    companions: Dict[str, Any]
    budget: str
    preferences: Dict[str, Any]
    time_hint: str
    ask_question: str
    is_complete: bool


SYSTEM_PROMPT = """你是短时活动规划助手的事项补全模块。接收意图识别结果和用户的问题，判断需要补充哪些信息。
1. 首先根据输入的信息判断需要补充哪些信息，这些信息应包括下列内容：
- 用户活动开始日期。
- 用户活动开始的位置，若未指定位置，则默认为用户当前的位置。
- 用户活动开始时的天气情况。
- 用户活动的共同参与者和参与者们对应的描述，如性别，年龄，是否怀孕，是否减肥中等。
- 用户活动的预算。
- 用户的偏好。
- 用户活动的持续时间。

2.根据需要补充的信息内容，首先尝试调用工具补充需要的信息：
   - 先并行调用 get_cur_loc 和 get_cur_time。
   - 调用 get_cur_weather 时，loc 参数必须传入 get_cur_loc 返回的完整对象，不要只传地址字符串。
   - 若工具无法补充某些信息，则输出一个能够一次性获得所有信息的问题。

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出 JSON，禁止任何解释、分析或额外文字。只输出纯 JSON：
{"date":"活动日期","location":{位置工具返回的对象},"weather":{天气工具返回的对象},"companions":{"count":1,"members":[{"role":"本人"}]},"budget":"中等","preferences":{},"time_hint":"14:00-18:00","ask_question":"一次性反问（无需反问时为空字符串）","is_complete":true或false（true=所有信息齐全可出方案，false=还需反问用户）}
"""


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-pro"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
    )

def _parse_result(raw: str) -> dict:
    text = raw.strip()
    # 去掉 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if lines[0].startswith("```") else text
        if text.endswith("```"):
            text = text[:-3]
    # 从混杂文本中提取 JSON
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "date": "",
            "location": {},
            "weather": {},
            "companions": {},
            "budget": "",
            "preferences": {},
            "time_hint": "",
            "ask_question": "解析失败，请重新描述需求",
            "is_complete": False,
        }

slot_agent = create_react_agent(
    model=_get_llm(),
    tools=[get_cur_loc, get_cur_time, get_cur_weather],
    prompt=SYSTEM_PROMPT,
)

# 4. 给 graph.py 调用的入口
def run_slot(user_input: str, intent: str):
    result = slot_agent.invoke({
        "messages": [
            HumanMessage(content=f"用户原话：{user_input}\n意图类型：{intent}")
        ],
    }, config={"recursion_limit": 30})
    last_msg = result["messages"][-1]
    return _parse_result(last_msg.content)



"""
还得改
"""

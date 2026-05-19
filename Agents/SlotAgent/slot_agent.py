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


SYSTEM_PROMPT = """你是短时活动规划助手的事项补全模块。接收主要意图和完整意图列表，按需收集信息。

根据主要意图决定需要收集哪些维度：
- check_weather: 只需 location + date + weather，companions/budget/preferences/time_hint 不需要
- find_activity: 需全部字段
- plan_outing: 需全部字段
- refine_plan: 需全部字段（修改已有计划需要完整信息）

工作步骤：
1. 先并行调用 get_cur_loc 和 get_cur_time
2. 调用 get_cur_weather 时，loc 参数必须传入 get_cur_loc 返回的完整对象
3. 从用户原话提取意图类型对应维度需要的信息
4. 工具无法补充时生成一次性的反问
5. 不需要的维度直接填入默认值（companions 为空对象、budget 为空字符串等），不要反问

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
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

def run_slot(user_input: str, intent: str, intents: dict):
    intents_str = ", ".join(f"{k}({v:.0%})" for k, v in intents.items())
    result = slot_agent.invoke({
        "messages": [
            HumanMessage(content=f"用户原话：{user_input}\n主要意图：{intent}\n所有意图：{intents_str}")
        ],
    }, config={"recursion_limit": 30})
    last_msg = result["messages"][-1]
    return _parse_result(last_msg.content)


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

load_dotenv()


SYSTEM_PROMPT = """你是短时活动规划助手的信息补全模块。接收用户原话和意图类型，补全生成出行方案所需的全部信息。

可用工具：get_cur_loc（获取位置）、get_cur_time（获取时间）、get_cur_weather（查天气）。

工作步骤：
1. 调用 get_cur_loc 和 get_cur_time
2. 用位置和今天日期调用 get_cur_weather
3. 从用户原话提取同行人信息（人数/性别/年龄/忌口）、预算、活动偏好
4. 可推断的按默认值：没说位置→用当前位置，"离家近"→5km，"下午"→14:00、4小时，没提预算→"中等"
5. 无法推断的填入 missing_fields 并生成一句自然的中文反问

严格按以下 JSON 格式输出（勿输出 markdown 代码块）：
{"location":{工具返回},"time":{工具返回},"weather":{工具返回},"enriched_slots":{"time_start":"14:00","duration_hours":4,"radius_km":5,"party":{"count":1,"members":[{"role":"本人"}]},"budget":"中等","activity_preference":""},"missing_fields":["字段名"],"ask_question":"一次性反问","is_complete":true/false,"reasoning":"简短理由"}
"""


class SlotState(TypedDict):
    user_input: str
    intent: str
    enriched_slots: Dict[str, Any]
    missing_fields: List[str]
    ask_question: str
    is_complete: bool
    reasoning: str


TOOLS = [get_cur_loc, get_cur_time, get_cur_weather]
TOOL_BY_NAME = {t.name: t for t in TOOLS}


def _get_llm():
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-chat"),
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "enriched_slots": {},
            "missing_fields": ["全部信息"],
            "ask_question": "信息不完整，请重新描述一下出行需求？",
            "is_complete": False,
            "reasoning": "JSON 解析失败",
        }


def _slot_node(state: SlotState) -> SlotState:
    llm = _get_llm()
    llm_with_tools = llm.bind_tools(TOOLS)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"用户原话：{state['user_input']}\n意图类型：{state['intent']}"),
    ]

    for _ in range(5):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if response.tool_calls:
            for tc in response.tool_calls:
                tool = TOOL_BY_NAME.get(tc["name"])
                if tool:
                    result = tool.invoke(tc["args"])
                    messages.append(ToolMessage(
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=tc["id"],
                    ))
        else:
            parsed = _parse_result(response.content)
            state["enriched_slots"] = parsed.get("enriched_slots", {})
            state["missing_fields"] = parsed.get("missing_fields", [])
            state["ask_question"] = parsed.get("ask_question", "")
            state["is_complete"] = parsed.get("is_complete", False)
            state["reasoning"] = parsed.get("reasoning", "")
            return state

    state["is_complete"] = False
    state["ask_question"] = "处理超时，请重新描述需求"
    return state


def build_slot_agent():
    graph = StateGraph(SlotState)
    graph.add_node("slot", _slot_node)
    graph.set_entry_point("slot")
    graph.add_edge("slot", END)
    return graph.compile()


def run_slot(user_input: str, intent: str) -> SlotState:
    agent = build_slot_agent()
    initial: SlotState = {
        "user_input": user_input,
        "intent": intent,
        "enriched_slots": {},
        "missing_fields": [],
        "ask_question": "",
        "is_complete": False,
        "reasoning": "",
    }
    return agent.invoke(initial)

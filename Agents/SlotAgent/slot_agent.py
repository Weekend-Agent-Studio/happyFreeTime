"""
事项补全 Agent
接收意图识别结果，通过 LLM + tool calling 补充缺失信息，
无法自动补全的生成反问让用户回答。
"""

import json
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from Tools import get_cur_loc, get_cur_time, get_weather
from langgraph.prebuilt import create_react_agent
from Agents.SlotAgent.slot_state import merge_slot_state, question_field

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
- find_activity: 需日期、地点、时间范围和预算；companions/preferences 可以为空
- plan_outing: 需日期、地点、时间范围和预算；companions/preferences 可以为空
- refine_plan: 需日期、地点、时间范围和预算；companions/preferences 可以为空

工作步骤：
1. 先并行调用 get_cur_loc 和 get_cur_time
2. 调用 get_weather 时，从 get_cur_loc 返回的对象中取 lat 作为 latitude、lng 作为 longitude
3. 从用户原话提取意图类型对应维度需要的信息
4. 工具无法补充时只针对一个缺失的必需维度生成一次反问
5. companions/preferences 不是必需维度，缺失时填默认值，不要反问
6. 如果输入中提供了“上一轮已确认槽位”，这些值必须原样保留；用户补充只用于当前待补维度

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
{"date":"活动日期","location":{位置工具返回的对象},"weather":{天气工具返回的对象},"companions":{"count":1,"members":[{"role":"本人"}]},"budget":"中等","preferences":{},"time_hint":"14:00-18:00","ask_question":"一次性反问（无需反问时为空字符串）","is_complete":true或false（true=所有信息齐全可出方案，false=还需反问用户）}

重要：调用完工具后，你的最终回复必须且仅包含上述 JSON 对象，不要加任何解释、分析或对话文字。一个字都别多说，直接输出 JSON。"""


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )

def _repair_json(raw: str) -> dict:
    """LLM 修复格式不正确的 JSON 输出。"""
    llm = _get_llm()
    response = llm.invoke([
        SystemMessage(content="把下面文本转为合法JSON对象，只输出JSON，不要其他内容。"),
        HumanMessage(content=raw[:2000]),
    ])
    # 再走一次提取+解析
    text = response.content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


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
        try:
            return _repair_json(raw)
        except (json.JSONDecodeError, Exception):
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
    tools=[get_cur_loc, get_cur_time, get_weather],
    prompt=SYSTEM_PROMPT,
)


_QUESTION_TEMPLATES = {
    "date": "请问准备哪一天出行？",
    "location": "请问从哪里出发，或活动地点在哪里？",
    "time_hint": "请问大概几点出发、几点结束？",
    "budget": "请问人均预算大概是多少？",
}


def _required_fields(intent: str):
    if intent == "check_weather":
        return ("date", "location")
    if intent in {"find_activity", "plan_outing", "refine_plan"}:
        return ("date", "location", "time_hint", "budget")
    return ()


def _is_present(value):
    return value not in (None, "", [], {})


def _normalise_slot(slot: dict, previous_slot: Optional[dict] = None, target_field: Optional[str] = None):
    merged = merge_slot_state(previous_slot or {}, slot or {}, target_field=target_field)
    for key in ("companions", "preferences", "weather"):
        merged.setdefault(key, {})
    merged.setdefault("budget", "")
    merged.setdefault("time_hint", "")
    merged.setdefault("date", "")
    merged.setdefault("location", {})
    return merged


def run_slot(
    user_input: str,
    intent: str,
    intents: dict,
    previous_slot: Optional[dict] = None,
    pending_field: Optional[str] = None,
    answered_fields: Optional[list] = None,
    merge_target_field: Optional[str] = None,
):
    import time
    t0 = time.perf_counter()
    intents_str = ", ".join(f"{k}({v:.0%})" for k, v in intents.items())
    prior = previous_slot or {}
    pending_hint = pending_field or "无"
    prior_hint = json.dumps(prior, ensure_ascii=False)
    result = slot_agent.invoke({
        "messages": [
            HumanMessage(content=(
                f"用户原话：{user_input}\n主要意图：{intent}\n所有意图：{intents_str}\n"
                f"上一轮已确认槽位（必须保留）：{prior_hint}\n"
                f"当前待补字段：{pending_hint}\n"
                f"已回答字段：{json.dumps(answered_fields or [], ensure_ascii=False)}"
            ))
        ],
    }, config={"recursion_limit": 30})
    llm_calls = sum(1 for m in result["messages"] if m.__class__.__name__ == "AIMessage")
    print(f"[slot_agent] {time.perf_counter() - t0:.2f}s ({llm_calls} LLM calls)")
    last_msg = result["messages"][-1]
    parsed = _parse_result(last_msg.content)
    slot = _normalise_slot(
        parsed,
        prior,
        target_field=merge_target_field if merge_target_field is not None else pending_field,
    )

    required = _required_fields(intent)
    missing = [field for field in required if not _is_present(slot.get(field))]
    parsed_question = parsed.get("ask_question", "")
    next_field = question_field(parsed_question) if parsed_question else None
    if not missing:
        slot["ask_question"] = ""
        slot["is_complete"] = True
        slot["missing_required_fields"] = []
        return slot

    # Never allow the model to ask a confirmed field again.  The graph will
    # surface this as a safe state-machine diagnostic instead of looping.
    if next_field in (answered_fields or []):
        slot["ask_question"] = ""
        slot["is_complete"] = False
        slot["error_code"] = "repeated_clarification"
        slot["missing_required_fields"] = missing
        return slot

    field = next_field if next_field in missing else missing[0]
    slot["ask_question"] = parsed_question or _QUESTION_TEMPLATES[field]
    slot["is_complete"] = False
    slot["missing_required_fields"] = missing
    return slot


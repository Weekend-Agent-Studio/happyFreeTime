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
- confirm_execution: 用户想确认/选定某个方案并执行预定（如"选方案A""就第一个吧""都行帮我定了""不想订了"等）
- chitchat: 闲聊或与上述无关的话题

规则：
- intents 中为每个意图给出 0.0-1.0 的置信度，仅列出置信度 > 0.3 的意图
- primary 是最主要的意图（置信度最高的那个）
- 纯闲聊时 intents 只包含 chitchat
- 用户选择、确认、拒绝方案时 primary 应为 confirm_execution，同时输出 selected_index 字段
- selected_index: 方案A/第一个→0，方案B/第二个→1，方案C/第三个→2，"最后一个""倒数第一个""选最后的"→-3，"不想订了""算了"→-1
- 非 confirm_execution 意图时 selected_index 填 -2 即可
- 当上下文提示"已有方案"时，用户表达价格/时间/偏好上的不满或调整诉求（如"太贵了""预算降到""换成室内的""改到下午"），primary 应判为 refine_plan

闲聊时 reply 字段给一句简短友好的回复（1-2句），非闲聊时 reply 留空字符串即可。

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
{"intents":{"plan_outing":0.9,"check_weather":0.4},"primary":"plan_outing","selected_index":-2,"reasoning":"简短理由","reply":""}
"""


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
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
        return {"intents": {"chitchat": 1.0}, "primary": "chitchat", "selected_index": -2, "reasoning": "JSON 解析失败", "reply": "嗯嗯~"}


def classify_intent(user_input: str, prev_intents: dict = None, prev_reply: str = "",
                    has_plans: bool = False) -> dict:
    import time
    t0 = time.perf_counter()
    llm = _get_llm()
    context = user_input
    if prev_reply:
        context = f"上一轮助手回复：{prev_reply}\n用户本轮：{user_input}"
    if prev_intents:
        prev_intents_str = ", ".join(f"{k}({v:.0%})" for k, v in prev_intents.items())
        context += f"\n上一轮意图：{prev_intents_str}"
    if has_plans:
        context += "\n（上下文：已有规划方案，用户如有不满或调整诉求，应判为 refine_plan）"
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=context),
    ]
    response = llm.invoke(messages)
    result = _parse_result(response.content)
    print(f"[intent_agent] {time.perf_counter() - t0:.2f}s")
    return result
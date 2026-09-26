"""
RouterExtractor —— 一次 LLM 调用完成意图识别 + 约束抽取。
环境信息（时间/位置/天气）由代码提前注入 prompt，LLM 不调工具。
"""
import json
import os
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from Tools import get_cur_time, get_cur_loc

load_dotenv()

# ---- System Prompt ----

SYSTEM_PROMPT = """你是短时活动规划助手的路由与约束抽取模块。从用户输入中同时完成两件事：识别意图 + 抽取约束。

意图类型：
- plan_outing: 用户想规划短时外出活动安排
- find_activity: 用户想查找特定类型的活动/餐厅/场所
- check_weather: 用户想查询天气
- refine_plan: 用户想调整已有计划
- confirm_execution: 用户想确认/选定某个方案并执行预定
- cancel_execution: 用户明确不执行或取消
- chitchat: 闲聊或与上述无关的话题

规则：
- intents 中为每个意图给出 0.0-1.0 置信度，仅列出 > 0.3 的意图
- primary 是置信度最高的意图
- 纯闲聊时 intents 只包含 chitchat，raw_constraints 为空对象
- 用户选择、确认、拒绝方案时 primary 应为 confirm_execution 或 cancel_execution

selected_index 规则：
- 方案A/第一个 → 0，方案B/第二个 → 1，方案C/第三个 → 2
- "最后一个""倒数第一个""选最后的" → -3，"不想订了""算了" → -1
- 非选择/确认意图 → -2

raw_constraints 抽取规则：
- 只抽取用户原话中明确提到的信息，不要编造
- 没说的字段保持 null 或空值
- date_text: 用户说的日期表达（"今天""明天""这周六"等）
- time_text: 用户说的时间表达（"下午""晚上""2点"等）
- duration_minutes: 用户明确说的时间长度（如"4小时"→240），没说则 null
- location_text: 用户说的位置表达（"海淀""附近"等），没说则空
- companions: 从原话抽取人群信息。adults、children、child_age 有明确值才填，不要猜测。members 用原文词语
- budget_text: 预算相关原文（"人均150""500以内"等）
- budget_per_person: 能明确算出人均预算才填数字
- max_distance_text: 距离约束原文（"别太远""步行可达"等）
- max_distance_km: 有明确数字才填
- preferences: 用户表达的活动偏好列表
- diet_tags: 饮食相关的偏好标签
- scene_tags: 场景相关的标签（"家庭""约会""朋友聚餐"等）
- avoid: 用户明确排斥的内容

extraction_confidence 规则：
- 只对从原文中抽取到的字段给出置信度
- 0.9+: 原文明确提及（如"孩子5岁"）
- 0.5-0.9: 可从上下文可靠推断
- 0.3-0.5: 模糊暗示，不确定
- 不要为 null/空值的字段给出置信度

evidence_map 规则：
- 对每个有置信度的字段，记录用户原文中的证据片段
- 简短引用即可，不要改写

输出格式：严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
{"intent":"plan_outing","intents":{"plan_outing":0.95},"selected_index":-2,"raw_constraints":{...},"extraction_confidence":{...},"evidence_map":{...},"reply":""}

重要：
- 纯闲聊时不解析 raw_constraints，直接返回空对象
- 只做识别和抽取，不做规划，不编造信息
- reply 字段：闲聊时给一句友好回复（1-2句），非闲聊留空
"""


# ---- LLM ----

def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


# ---- JSON 解析 ----

def _parse_result(raw: str) -> dict:
    """从 LLM 原始输出中提取 JSON，带容错。"""
    text = raw.strip()

    # 去 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if lines[0].startswith("```") else text
        if text.endswith("```"):
            text = text[:-3]

    # 提取 JSON 对象
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _repair_json(raw)


def _repair_json(raw: str) -> dict:
    """LLM 修复格式不正确的 JSON。"""
    try:
        llm = _get_llm()
        response = llm.invoke([
            SystemMessage(content="把下面文本转为合法JSON对象，只输出JSON，不要其他内容。"),
            HumanMessage(content=raw[:2000]),
        ])
        text = response.content.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        return json.loads(text)
    except Exception:
        return _fallback_result()


def _fallback_result() -> dict:
    """解析完全失败时的兜底。"""
    return {
        "intent": "chitchat",
        "intents": {"chitchat": 1.0},
        "selected_index": -2,
        "raw_constraints": {},
        "extraction_confidence": {},
        "evidence_map": {},
        "reply": "不好意思我没太理解，能再描述一下吗？",
    }


# ---- 环境信息收集 ----

def _collect_env_info() -> dict:
    """代码驱动收集环境信息，用于注入 prompt。天气获取失败不影响流程。"""
    env = {}

    # 时间（同步，不会失败）
    try:
        now = get_cur_time.invoke({})
        env["current_date"] = now.get("date_iso", "")
        env["current_weekday"] = now.get("weekday", "")
        env["current_time"] = f"{now.get('hour', 0):02d}:{now.get('minute', 0):02d}"
    except Exception:
        pass

    # 位置（同步，不会失败）
    try:
        loc = get_cur_loc.invoke({})
        env["default_location"] = f"{loc.get('address', '')}（{loc.get('district', '')}）"
    except Exception:
        env["default_location"] = "北京市朝阳区"

    # 天气（MCP，可能慢或失败）
    try:
        from Tools import get_weather
        loc = get_cur_loc.invoke({})
        w_str = get_weather.invoke({
            "latitude": str(loc.get("lat", "39.9087")),
            "longitude": str(loc.get("lng", "116.4713")),
        })
        if isinstance(w_str, str):
            w = json.loads(w_str)
            env["weather"] = f"{w.get('weather', '未知')}，{w.get('temperature', 'N/A')}"
    except Exception:
        env["weather"] = "未获取"

    return env


# ---- 主入口 ----

def run_router_extractor(
    user_input: str,
    has_plans: bool = False,
    prev_intent: str = "",
    prev_slot: Optional[dict] = None,
) -> dict:
    """
    一次 LLM 调用完成意图识别 + 约束抽取。

    参数：
        user_input: 用户当前输入
        has_plans: 当前是否已有规划方案
        prev_intent: 上一轮意图
        prev_slot: 上一轮已补全的 slot（用于多轮上下文）
    返回：
        {
            "intent": str,
            "intents": dict,
            "selected_index": int,
            "raw_constraints": dict,
            "extraction_confidence": dict,
            "evidence_map": dict,
            "reply": str
        }
    """
    import time
    t0 = time.perf_counter()

    # 1. 收集环境信息
    env = _collect_env_info()

    # 2. 构建上下文
    context_lines = [f"用户输入：{user_input}"]

    if prev_intent:
        context_lines.append(f"上一轮意图：{prev_intent}")
    if prev_slot:
        slot_brief = {}
        for k in ("companions", "budget", "time_hint", "preferences"):
            if k in prev_slot and prev_slot[k]:
                slot_brief[k] = prev_slot[k]
        if slot_brief:
            context_lines.append(f"上一轮已补全：{json.dumps(slot_brief, ensure_ascii=False)}")
    if has_plans:
        context_lines.append("（上下文：已有规划方案，用户如有不满或调整诉求，应判为 refine_plan）")

    context = "\n".join(context_lines)

    # 3. 注入环境信息到 System Prompt
    env_block = f"""当前环境（由系统提供，不是用户说的）：
  - 日期：{env.get('current_date', '未知')}（{env.get('current_weekday', '')}）
  - 时间：{env.get('current_time', '未知')}
  - 位置：{env.get('default_location', '未知')}
  - 天气：{env.get('weather', '未知')}

{context}"""

    # 4. LLM 调用
    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=env_block),
    ]
    response = llm.invoke(messages)
    result = _parse_result(response.content)

    # 5. 确保必要字段存在
    result.setdefault("intent", "chitchat")
    result.setdefault("intents", {"chitchat": 1.0})
    result.setdefault("selected_index", -2)
    result.setdefault("raw_constraints", {})
    result.setdefault("extraction_confidence", {})
    result.setdefault("evidence_map", {})
    result.setdefault("reply", "")

    print(f"[router_extractor] {time.perf_counter() - t0:.2f}s (1 LLM)")
    return result

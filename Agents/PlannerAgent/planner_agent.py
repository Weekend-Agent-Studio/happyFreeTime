"""
规划agent —— 短时活动规划系统的规划器
利用意图识别结果和补全的信息给给用户规划活动等。
"""

import json
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from Tools import search_activities, search_restaurants, search_products, estimate_route

load_dotenv()


SYSTEM_PROMPT = """你是短时活动规划助手的规划编排模块。接收用户需求和已补全的信息，生成完整的出行方案。

规划规则：
1. 第一轮同时并行调用 search_activities、search_restaurants、search_products（三者互不依赖，必须一次同时调用）
2. 收到全部搜索结果后如需确认距离，可调 estimate_route，但不必须
3. 将活动-餐厅-增量拼成一条时间连续的路线，确保地点不跳远
4. 输出 2-3 条不同风格的方案供用户选择
5. timeline 中每个项的 name、address、price 必须来自工具返回的真实数据，禁止自行编造活动名或餐厅名
6. 同一活动/餐厅/商品在 timeline 中只能出现一次，将时间合并为一段，禁止拆分成多条
7. 搜索工具返回的结果已包含营业时间、评分、价格、库存等全部信息，直接使用，无需额外调用查询工具
8. 总计调用工具控制在 4-5 次以内：第一轮 3 个 search 同时调用 + estimate_route 0-1 次

严格按以下 JSON 格式输出，不要输出 markdown 代码块，只输出纯 JSON：
{
  "plans": [
    {
      "title": "方案标题",
      "style": "风格",
      "timeline": [
        {"time": "14:00-16:30", "step": "活动", "name": "活动名", "address": "地址", "price": 120, "note": ""},
        {"time": "17:00-18:30", "step": "餐厅", "name": "餐厅名", "address": "地址", "price": 90, "note": ""},
        {"time": "18:30-19:00", "step": "增量", "name": "商品名", "address": "地址", "price": 40, "note": ""}
      ],
      "total_price": 250,
      "highlights": ["亮点1", "亮点2"]
    }
  ],
  "reasoning": "简短理由"
}

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
                "plans": [
                {
                    "title": "方案A标题",
                    "style": "风格",
                    "timeline": [
                        {"time": "14:00-16:30", "step": "活动", "name": "xx", "address": "xx", "price": 120, "note": ""},
                        {"time": "17:00-18:30", "step": "餐厅", "name": "xx", "address": "xx", "price": 90, "note": ""},
                        {"time": "18:30-19:00", "step": "增量", "name": "xx", "address": "xx", "price": 40, "note": ""}
                    ],
                    "total_price": 250,
                    "highlights": ["亮点1", "亮点2"]
                }
                ],
                "reasoning": "简短理由"
            }

planner_agent = create_react_agent(
    model=_get_llm(),
    tools=[search_activities, search_restaurants, search_products, estimate_route],
    prompt=SYSTEM_PROMPT,
)


def run_planner(user_input, intent: str, intents: dict, slots: dict):
    import time
    t0 = time.perf_counter()
    intents_str = ", ".join(f"{k}({v:.0%})" for k, v in intents.items())
    result = planner_agent.invoke({
        "messages": [
            HumanMessage(content=f"用户原话：{user_input}\n主要意图：{intent}\n所有意图：{intents_str}\n补全的信息：{slots}")
        ],
    }, config={"recursion_limit": 50})
    llm_calls = sum(1 for m in result["messages"] if m.__class__.__name__ == "AIMessage")
    print(f"[planner_agent] {time.perf_counter() - t0:.2f}s ({llm_calls} LLM calls)")
    last_msg = result["messages"][-1]
    return _parse_result(last_msg.content)
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
from Tools import search_activities, search_restaurants, search_products, get_resource_detail, check_open_hours,check_availability, estimate_route, estimate_queue_time, check_weather_suitability

load_dotenv()


SYSTEM_PROMPT = """你是短时活动规划助手的规划编排模块。接收用户需求和已补全的信息，生成完整的出行方案。

规划规则：
1. 先调用 search_activities 获取候选活动，根据时间窗口、天气、偏好筛选
2. 再调用 search_restaurants 获取候选餐厅，在活动地点附近、符合预算和忌口
3. 最后调用 search_products 获取沿途增量消费（甜品、鲜花、蛋糕、小吃街）
4. 将活动-餐厅-增量拼成一条时间连续的路线，确保地点不跳远
5. 输出 2-3 条不同风格的方案供用户选择

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
            "plans": [
            {
                "title": "方案A标题",
                "style": "风格（如亲子优先/性价比/浪漫）",
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
    tools=[search_activities, search_restaurants, search_products, get_resource_detail, check_open_hours,check_availability, estimate_route, estimate_queue_time, check_weather_suitability],
    prompt=SYSTEM_PROMPT,
)


def run_planner(user_input, intent: str, intents: dict, slots: dict):
    intents_str = ", ".join(f"{k}({v:.0%})" for k, v in intents.items())
    result = planner_agent.invoke({
        "messages": [
            HumanMessage(content=f"用户原话：{user_input}\n主要意图：{intent}\n所有意图：{intents_str}\n补全的信息：{slots}")
        ],
    }, config={"recursion_limit": 50})
    last_msg = result["messages"][-1]
    return _parse_result(last_msg.content)
"""
规划agent —— 短时活动规划系统的规划器
两步式：LLM 决定调哪些工具 → Python 并行执行 → LLM 出方案。
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from Tools import search_activities, search_restaurants, search_products, estimate_route

load_dotenv()

# 可用工具注册表
TOOL_MAP = {
    "search_activities": search_activities,
    "search_restaurants": search_restaurants,
    "search_products": search_products,
    "estimate_route": estimate_route,
}

PLANNER_SYSTEM_PROMPT = """你是短时活动规划助手的规划编排模块。根据用户需求和已补全信息，先决定需要调用哪些搜索工具，一次性并行调用完。收到搜索结果后生成2-3条出行方案。

调用工具规则：
1. 必须一次同时调用所需的所有工具（search_activities、search_restaurants、search_products），不要分多轮
2. 保持地点一致——所有搜索的 loc 都使用已补全信息中的 location
3. estimate_route 按需调用，用于确认关键点位间距离

方案生成规则：
1. 方案必须严格使用搜索结果中的真实数据（name、address、price），禁止编造
2. 活动-餐厅-增量拼成时间连续的路线，地点不跳远
3. 2-3条不同风格的方案，时间+活动+餐厅+增量形成连续timeline
4. refine_plan时参考上一轮方案和执行结果，保留成功项、替换失败项

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
重要：最终回复必须且仅包含上述 JSON 对象，不要加任何解释。"""


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _repair_json(raw: str) -> dict:
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
        try:
            return _repair_json(raw)
        except (json.JSONDecodeError, Exception):
            return {
                "plans": [
                    {
                        "title": "方案A标题", "style": "风格",
                        "timeline": [
                            {"time": "14:00-16:30", "step": "活动", "name": "xx", "address": "xx", "price": 120, "note": ""},
                            {"time": "17:00-18:30", "step": "餐厅", "name": "xx", "address": "xx", "price": 90, "note": ""},
                            {"time": "18:30-19:00", "step": "增量", "name": "xx", "address": "xx", "price": 40, "note": ""}
                        ],
                        "total_price": 250, "highlights": ["亮点1", "亮点2"]
                    }
                ],
                "reasoning": "简短理由"
            }


def _execute_tool_calls(tool_calls: list) -> list:
    """并行执行 LLM 决定的工具调用，直接调用原始函数（不用 .invoke）。"""
    if not tool_calls:
        return []

    def _run(tc):
        name = tc["name"]
        args = tc["args"]
        func = TOOL_MAP.get(name)
        if func is None:
            print(f"  [tool] {name} -> 未找到工具")
            return {"id": tc["id"], "name": name, "error": f"Unknown tool: {name}"}
        try:
            # @tool 调用：把整个 dict 作为输入传入
            result = func.invoke(args)
            print(f"  [tool] {name}({args}) -> {len(result) if isinstance(result, list) else 'ok'}")
            return {"id": tc["id"], "name": name, "result": result}
        except Exception as e:
            print(f"  [tool] {name}({args}) -> ERROR: {e}")
            return {"id": tc["id"], "name": name, "error": str(e)}

    with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
        return list(pool.map(_run, tool_calls))


def run_planner(user_input, intent: str, intents: dict, slots: dict,
                prev_plans: dict = None, prev_execution: dict = None):
    import time
    t0 = time.perf_counter()

    intents_str = ", ".join(f"{k}({v:.0%})" for k, v in intents.items())
    context = f"用户原话：{user_input}\n主要意图：{intent}\n所有意图：{intents_str}\n补全的信息：{slots}"
    if prev_plans and prev_plans.get("plans"):
        context += f"\n上一轮方案：{json.dumps(prev_plans, ensure_ascii=False)}"
    if prev_execution and prev_execution.get("results"):
        context += f"\n上一轮执行结果：{json.dumps(prev_execution['results'], ensure_ascii=False)}"

    llm = _get_llm()
    llm_with_tools = llm.bind_tools(list(TOOL_MAP.values()))

    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=context),
    ]

    total_tool_calls = 0
    max_rounds = 3

    for round_num in range(max_rounds):
        response = llm_with_tools.invoke(messages)
        tool_calls = response.tool_calls

        if not tool_calls:
            # 没有工具调用 → LLM 认为可以出方案了
            elapsed = time.perf_counter() - t0
            print(f"[planner_agent] {elapsed:.2f}s ({round_num + 1} LLM rounds, {total_tool_calls} tool calls)")
            return _parse_result(response.content)

        # 有工具调用 → 并行执行
        total_tool_calls += len(tool_calls)
        print(f"  [round{round_num}] {[(tc['name'], tc['args']) for tc in tool_calls]}")
        tool_results = _execute_tool_calls(tool_calls)
        messages.append(response)
        for tr in tool_results:
            content = json.dumps(tr.get("result") or tr.get("error"), ensure_ascii=False)
            messages.append(ToolMessage(content=content, tool_call_id=tr["id"]))

    # 兜底：达到最大轮次，强制最后一次输出
    elapsed = time.perf_counter() - t0
    print(f"[planner_agent] {elapsed:.2f}s (max rounds reached, {total_tool_calls} tool calls)")
    return _parse_result(messages[-1].content)

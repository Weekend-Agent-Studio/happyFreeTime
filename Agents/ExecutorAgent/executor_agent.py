"""
执行模块 —— 用户确认方案后自动执行预定
遍历 planner 生成的 timeline，调用执行工具逐个预定，最后 LLM 汇总结果。
"""

import re
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from services.catalog_service import search_activities, search_restaurants, search_products
from services.order_service import book_tickets, reserve_restaurant, place_order

SUMMARY_PROMPT = """你是活动规划助手，根据预定执行结果生成简短总结。逐条汇报成功或失败，确认号和金额。
如实汇报，不要编造未发生的结果。"""


def _get_llm():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _match_resource(name: str, step_type: str, location: dict) -> Optional[str]:
    """根据名称匹配资源 ID。"""
    if step_type == "活动":
        results = search_activities(location=location) or []
    elif step_type == "餐厅":
        results = search_restaurants(location=location) or []
    else:
        results = search_products(location=location) or []

    if not results:
        return None

    core = re.split(r'[·（(]', name)[0].strip()

    for r in results:
        if r["name"] == core or r["name"] == name:
            return r["id"]
    for r in results:
        if core in r["name"] or r["name"] in core:
            return r["id"]
    return None


def _execute_item(item: dict, slot: dict) -> dict:
    """执行单个 timeline 项的预定。"""
    step = item.get("step", "")
    name = item.get("name", "")
    time_range = item.get("time", "14:00-16:00")
    start_time = time_range.split("-")[0].strip() if "-" in time_range else time_range

    date = slot.get("date", "2026-05-23")
    location = slot.get("location", {})
    companions = slot.get("companions", {})

    try:
        if step == "活动":
            resource_id = _match_resource(name, "活动", location)
            if not resource_id:
                return {"success": False, "name": name, "error": f"未找到匹配的活动：{name}"}
            return book_tickets(resource_id, date, start_time, companions.get("count", 1))

        elif step == "餐厅":
            resource_id = _match_resource(name, "餐厅", location)
            if not resource_id:
                return {"success": False, "name": name, "error": f"未找到匹配的餐厅：{name}"}
            return reserve_restaurant(resource_id, date, start_time, companions.get("count", 2))

        elif step == "增量":
            resource_id = _match_resource(name, "增量", location)
            if not resource_id:
                return {"success": False, "name": name, "error": f"未找到匹配的商品：{name}"}
            return place_order(resource_id, 1, location.get("address", ""), start_time)

        elif step in ("交通", "回家"):
            return {"success": True, "name": name, "step": step, "message": "无需预定"}

        else:
            return {"success": False, "name": name, "error": f"未知步骤类型：{step}"}

    except Exception as e:
        return {"success": False, "name": name, "step": step, "error": str(e)}


def run_executor(user_input: str, intent: str, intents: dict, plans: dict, slot: dict,
                 selected_index: int = -2) -> dict:
    """用户选择方案后自动执行所有预定，LLM 汇总结果。"""
    import time
    t0 = time.perf_counter()
    if selected_index == -1:
        print(f"[executor_agent] {time.perf_counter() - t0:.2f}s")
        return {
            "executed": False,
            "results": [],
            "summary": "好的，没有执行任何预定。如需其他帮助请随时说。",
        }

    plan_list = plans.get("plans", [])
    if selected_index == -3:
        selected_index = len(plan_list) - 1
    if selected_index < 0 or selected_index >= len(plan_list):
        print(f"[executor_agent] {time.perf_counter() - t0:.2f}s")
        return {
            "executed": False,
            "results": [],
            "summary": f"无法确定您选择了哪个方案，当前共有{len(plan_list)}个方案，请说\"选方案A\"或\"选第一个\"。",
        }

    choice = selected_index

    selected = plan_list[choice]
    timeline = selected.get("timeline", [])

    results = [_execute_item(item, slot) for item in timeline]

    # LLM 汇总
    llm = _get_llm()
    context = f"方案：{selected.get('title', 'N/A')}\n执行结果：\n"
    for r in results:
        if r.get("success"):
            context += f"✅ {r.get('step','')} {r.get('target_name', r.get('name',''))}：成功，确认号 {r.get('confirmation_id','')}，{r.get('total_price',0)}元\n"
        else:
            context += f"❌ {r.get('step','')} {r.get('name','')}：失败，{r.get('error', r.get('message',''))}\n"

    response = llm.invoke([
        SystemMessage(content=SUMMARY_PROMPT),
        HumanMessage(content=context),
    ])

    ret = {
        "executed": True,
        "plan_title": selected.get("title", ""),
        "results": results,
        "summary": response.content.strip(),
    }
    print(f"[executor_agent] {time.perf_counter() - t0:.2f}s")
    return ret

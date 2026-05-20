"""
Mock 层验证脚本：不依赖 LLM，直接测试 services/ 和 tools/ 的完整链路。
"""
import json
import os
import sys

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.catalog_service import (
    get_resource_detail,
    search_activities,
    search_restaurants,
    search_products,
)
from services.feasibility_service import (
    check_open_hours,
    check_availability,
    estimate_route,
    estimate_queue_time,
    check_weather_suitability,
)
from services.scoring_service import score_activity, score_restaurant, score_plan, compare_plans
from services.planning_service import generate_candidate_plans, validate_plan_timeline
from services.order_service import (
    book_tickets,
    reserve_restaurant,
    place_order,
    cancel_booking,
    reset_orders,
)

SEP = "=" * 60


def test_data_loading():
    """1. 数据加载与详情查询"""
    print(f"\n{SEP}")
    print("1. 数据加载测试")
    print(SEP)

    for rid in ["act_001", "rest_001", "prod_001", "nonexistent"]:
        item = get_resource_detail(rid)
        if item:
            print(f"  {rid}: {item['name']} (评分 {item.get('rating')})")
        else:
            print(f"  {rid}: 不存在（正确）")


def test_search():
    """2. 搜索功能"""
    print(f"\n{SEP}")
    print("2. 搜索测试")
    print(SEP)

    loc = {"lat": 39.9087, "lng": 116.4713}

    # 搜索亲子活动
    acts = search_activities(
        tags=["亲子", "室内"],
        location=loc,
        radius_km=5,
        party_profile={"adults": 2, "children": 1, "child_age": 5},
    )
    print(f"\n  搜索亲子活动（半径5km）: 找到 {len(acts)} 个")
    for a in acts:
        print(f"    - {a['name']} | 匹配: {a.get('_match_reasons')} | 距离: {a.get('_distance_km', 'N/A')}km")

    # 搜索低卡餐厅
    rests = search_restaurants(
        diet_tags=["低卡"],
        scene_tags=["家庭"],
        location=loc,
        radius_km=5,
        party_size=3,
    )
    print(f"\n  搜索低卡+家庭餐厅: 找到 {len(rests)} 个")
    for r in rests:
        print(f"    - {r['name']} | 匹配: {r.get('_match_reasons')} | 人均: {r.get('avg_price')}元")

    # 搜索商品
    prods = search_products(category="蛋糕", location=loc)
    print(f"\n  搜索蛋糕: 找到 {len(prods)} 个")
    for p in prods:
        print(f"    - {p['name']} | {p.get('avg_price')}元 | 库存: {p.get('stock')}")


def test_feasibility():
    """3. 可行性校验"""
    print(f"\n{SEP}")
    print("3. 可行性校验测试")
    print(SEP)

    # 营业时间
    result = check_open_hours("act_001", "14:30")
    print(f"\n  营业时间 act_001 14:30: {result['reason']}")

    result = check_open_hours("act_003", "20:00")
    print(f"  营业时间 act_003 20:00: {result['reason']}")

    # 库存
    result = check_availability("act_001", "2026-05-23", "14:30")
    print(f"\n  库存 act_001 5/23 14:30: {result['reason']}")

    # 桌位（4人桌16:00，应为无桌或需检查）
    result = check_availability("rest_004", "2026-05-23", "18:00", party_size=4)
    print(f"  桌位 rest_004 5/23 18:00 4人: {result['reason']}")

    # 路线
    origin = {"lat": 39.9087, "lng": 116.4713, "address": "国贸"}
    dest = {"lat": 39.9215, "lng": 116.3958, "address": "奥森"}
    route = estimate_route(origin, dest, mode="taxi")
    print(f"\n  路线 国贸→奥森 taxi: {route['distance_km']}km, {route['duration_minutes']}min")

    # 排队
    queue = estimate_queue_time("rest_003")
    print(f"  排队 rest_003: {queue['reason']}")

    # 天气
    weather = {"weather": "中雨", "temperature": "18摄氏度"}
    result = check_weather_suitability("act_002", weather)
    print(f"  天气 act_002（户外）+ 中雨: {result['reason']}")

    result = check_weather_suitability("act_001", weather)
    print(f"  天气 act_001（室内）+ 中雨: {result['reason']}")


def test_scoring():
    """4. 评分"""
    print(f"\n{SEP}")
    print("4. 评分测试")
    print(SEP)

    constraints = {
        "party_profile": {"adults": 2, "children": 1, "child_age": 5},
        "max_distance_km": 8,
        "budget_per_person": 120,
        "time_window": {"date": "2026-05-23", "start": "14:00", "end": "18:00"},
        "weather": {"weather": "晴朗", "temperature": "24摄氏度"},
        "preferences": ["亲子", "室内"],
        "diet_tags": ["低卡", "儿童餐"],
        "scene_tags": ["家庭"],
        "location": {"lat": 39.9087, "lng": 116.4713},
    }

    # 评分活动
    for aid in ["act_001", "act_002", "act_003", "act_004"]:
        result = score_activity(aid, constraints)
        print(f"  {aid} 活动评分: {result['score']}/100 | +{result['reasons']} | -{result['penalties']}")

    print()
    # 评分餐厅
    for rid in ["rest_001", "rest_002", "rest_003", "rest_004"]:
        result = score_restaurant(rid, constraints)
        print(f"  {rid} 餐厅评分: {result['score']}/100 | +{result['reasons']} | -{result['penalties']}")


def test_planning():
    """5. 方案生成"""
    print(f"\n{SEP}")
    print("5. 方案生成测试")
    print(SEP)

    constraints = {
        "location": {"lat": 39.9087, "lng": 116.4713, "address": "北京市朝阳区建国路88号"},
        "time_window": {"date": "2026-05-23", "start": "14:00", "end": "18:00"},
        "party_profile": {"adults": 2, "children": 1, "child_age": 5},
        "max_distance_km": 8,
        "budget_per_person": 120,
        "preferences": ["亲子", "室内"],
        "diet_tags": ["低卡", "儿童餐"],
        "scene_tags": ["家庭"],
        "weather": {"weather": "晴朗", "temperature": "24摄氏度"},
    }

    plans = generate_candidate_plans(constraints)
    scored = compare_plans(plans, constraints)

    for i, plan in enumerate(scored, 1):
        print(f"\n  方案 {i}: [{plan['plan_type']}] {plan['title']}")
        print(f"    总分: {plan.get('total_score', 'N/A')}")
        print(f"    总价: {plan.get('total_price', 0)}元")
        print(f"    总时长: {plan.get('total_duration_minutes', 0)}分钟")
        print(f"    亮点: {plan.get('highlights', [])}")
        print(f"    取舍: {plan.get('tradeoffs', [])}")
        print(f"    时间线:")
        for item in plan.get("items", []):
            print(f"      {item['start']} - {item['end']} [{item['type']}] {item['title']}")
        print(f"    待确认: {len(plan.get('required_confirmations', []))} 项")

        # 时间线校验
        valid = validate_plan_timeline(plan)
        print(f"    时间线校验: {'OK' if valid['is_valid'] else 'FAIL'} | 问题: {valid['issues']} | 警告: {valid['warnings']}")


def test_execution():
    """6. 订单执行"""
    print(f"\n{SEP}")
    print("6. 订单执行测试")
    print(SEP)

    reset_orders()

    # 订票
    result = book_tickets("act_001", "2026-05-23", "14:30", count=3)
    print(f"  订票: {result['message']}")
    print(f"    确认号: {result.get('confirmation_id', 'N/A')}")

    # 再次订同场次（库存应减少）
    result = book_tickets("act_001", "2026-05-23", "14:30", count=10)
    print(f"  超额订票: {result['message']}")

    # 预约餐厅
    result = reserve_restaurant("rest_001", "2026-05-23", "17:00", party_size=3)
    print(f"  预约餐厅: {result['message']}")

    # 预约不支持的4人桌餐厅
    result = reserve_restaurant("rest_004", "2026-05-23", "18:00", party_size=4)
    print(f"  超额预约: {result['message']}")

    # 下单商品
    result = place_order("prod_001", 1, "北京市朝阳区建国路88号", "2026-05-23 18:00")
    print(f"  下单: {result['message']}")

    # 取消
    if result.get("success"):
        cancel = cancel_booking(result["confirmation_id"])
        print(f"  取消: {cancel['message']}")


def test_test_cases():
    """7. 跑 test_cases.json 中的场景"""
    print(f"\n{SEP}")
    print("7. 测试用例验证")
    print(SEP)

    with open("data/test_cases.json", "r", encoding="utf-8") as f:
        cases = json.load(f)

    for case in cases:
        print(f"\n  [{case['case_id']}] {case['scene']}")
        print(f"    输入: {case['user_input'][:50]}...")

        c = case["constraints"]
        plans = generate_candidate_plans(c)
        scored = compare_plans(plans, c)

        # 验证 success_criteria
        passed = 0
        for crit in case["success_criteria"]:
            # 简单规则检查
            if "亲子" in crit or "儿童" in crit or "5岁" in crit:
                # 检查活动是否包含亲子相关
                has_family = any(
                    "family" in str(p.get("items", [{}])[1].get("resource_id", "")) if len(p.get("items", [])) > 1 else False
                    or any("亲子" in str(item.get("title", "")) for item in p.get("items", []))
                    for p in scored
                )
                if has_family:
                    passed += 1
                    print(f"      [PASS] {crit}")
                else:
                    print(f"      [WARN] {crit}（需人工确认）")

            elif "低卡" in crit or "健康" in crit:
                has_healthy = any(
                    "rest_001" in str(p.get("items", []))
                    for p in scored
                )
                if has_healthy:
                    passed += 1
                    print(f"      [PASS] {crit}")
                else:
                    print(f"      [WARN] {crit}（需人工确认）")

            elif "4小时" in crit or "时长" in crit:
                within_time = any(
                    p.get("total_duration_minutes", 0) <= 240
                    for p in scored
                )
                if within_time:
                    passed += 1
                    print(f"      [PASS] {crit}")
                else:
                    print(f"      [WARN] {crit}（需人工确认）")

            elif "确认" in crit:
                has_confirm = any(
                    len(p.get("required_confirmations", [])) > 0
                    for p in scored
                )
                if has_confirm:
                    passed += 1
                    print(f"      [PASS] {crit}")
                else:
                    print(f"      [WARN] {crit}（需人工确认）")

            elif "方案" in crit or "至少" in crit:
                if len(scored) >= 2:
                    passed += 1
                    print(f"      [PASS] {crit}（生成 {len(scored)} 个方案）")
                else:
                    print(f"      [WARN] {crit}（仅 {len(scored)} 个方案）")

            elif "排除" in crit or "解释" in crit:
                has_tradeoffs = any(
                    len(p.get("tradeoffs", [])) > 0
                    for p in scored
                )
                if has_tradeoffs:
                    passed += 1
                    print(f"      [PASS] {crit}")
                else:
                    print(f"      [WARN] {crit}（无取舍说明）")

            else:
                print(f"      [????] {crit}（需人工确认）")

        print(f"    自动检查: {passed}/{len(case['success_criteria'])} 项通过")


if __name__ == "__main__":
    print("Mock 层完整链路验证")
    print(f"项目根目录: {os.path.dirname(os.path.abspath(__file__))}")

    try:
        test_data_loading()
        test_search()
        test_feasibility()
        test_scoring()
        test_planning()
        test_execution()
        test_test_cases()

        print(f"\n{SEP}")
        print("所有测试完成！")
        print(SEP)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

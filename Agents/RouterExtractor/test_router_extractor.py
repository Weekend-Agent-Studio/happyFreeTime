"""
RouterExtractor 验证脚本：测试意图识别 + 约束抽取 + 置信度 + 证据的输出。
"""
import json
import os
import sys
import time

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "Agents", "RouterExtractor"))

from router_extractor import run_router_extractor


TEST_CASES = [
    # ========== 场景 1：家庭亲子完整输入 ==========
    {
        "label": "家庭亲子完整输入",
        "user_input": "今天下午想带老婆孩子出去玩4小时，别太远，老婆最近减肥，孩子5岁",
        "checks": [
            ("primary intent 为 plan_outing", lambda r: r["intent"] == "plan_outing"),
            ("intents 含 plan_outing", lambda r: "plan_outing" in r["intents"]),
            ("companions.adults = 2", lambda r: r["raw_constraints"].get("companions", {}).get("adults") == 2),
            ("companions.children = 1", lambda r: r["raw_constraints"].get("companions", {}).get("children") == 1),
            ("companions.child_age 含 5", lambda r: "5" in str(r["raw_constraints"].get("companions", {}).get("child_age", ""))),
            ("extraction_confidence 含 companions 相关 key", lambda r: any(k.startswith("companions") for k in r.get("extraction_confidence", {}))),
            ("evidence_map 含 companions 相关 key", lambda r: any(k.startswith("companions") for k in r.get("evidence_map", {}))),
            ("duration_minutes = 240", lambda r: r["raw_constraints"].get("duration_minutes") in (240, "240") or int(r["raw_constraints"].get("duration_minutes", 0)) == 240),
            ("diet_tags 含 低卡/减肥", lambda r: any(t in str(r["raw_constraints"].get("diet_tags", [])) for t in ["低卡", "减肥", "健康"])),
            ("scene_tags 含 家庭/亲子", lambda r: any(t in str(r["raw_constraints"].get("scene_tags", [])) for t in ["家庭", "亲子"])),
            ("reply 为空（非闲聊）", lambda r: r.get("reply", "") == ""),
            ("selected_index = -2", lambda r: r["selected_index"] == -2),
        ],
    },

    # ========== 场景 2：朋友聚会 ==========
    {
        "label": "朋友聚会",
        "user_input": "周六晚上4个朋友想聚一下，预算人均150左右，最好能吃饭再玩点轻松的",
        "checks": [
            ("primary intent 为 plan_outing", lambda r: r["intent"] == "plan_outing"),
            ("companions.adults = 4", lambda r: r["raw_constraints"].get("companions", {}).get("adults") == 4),
            ("budget_per_person = 150", lambda r: r["raw_constraints"].get("budget_per_person") == 150),
            ("scene_tags 含 朋友聚餐", lambda r: "朋友聚餐" in r["raw_constraints"].get("scene_tags", [])),
            ("date_text = 周六", lambda r: "周六" in str(r["raw_constraints"].get("date_text", ""))),
            ("extraction_confidence 含 companions 相关 key", lambda r: any(k.startswith("companions") for k in r.get("extraction_confidence", {}))),
        ],
    },

    # ========== 场景 3：情侣约会 ==========
    {
        "label": "情侣约会",
        "user_input": "这周日想安排一个轻松点的约会，不想太累，吃饭环境好一点，最好有甜品",
        "checks": [
            ("primary intent 为 plan_outing", lambda r: r["intent"] == "plan_outing"),
            ("preferences 含 轻松", lambda r: "轻松" in str(r["raw_constraints"].get("preferences", []))),
            ("scene_tags 含 约会", lambda r: "约会" in r["raw_constraints"].get("scene_tags", [])),
            ("diet_tags 或 preferences 含 甜品", lambda r: "甜品" in str(r["raw_constraints"].get("diet_tags", []))
             or "甜品" in str(r["raw_constraints"].get("preferences", []))),
        ],
    },

    # ========== 场景 4：纯闲聊 ==========
    {
        "label": "纯闲聊",
        "user_input": "你好呀，今天过得怎么样？",
        "checks": [
            ("primary intent 为 chitchat", lambda r: r["intent"] == "chitchat"),
            ("raw_constraints 为空", lambda r: r["raw_constraints"] == {} or len(r["raw_constraints"]) == 0),
            ("reply 不为空", lambda r: len(r.get("reply", "")) > 0),
        ],
    },

    # ========== 场景 5：查天气 ==========
    {
        "label": "查天气",
        "user_input": "周末天气怎么样，适合出去玩吗？",
        "checks": [
            ("primary intent 为 check_weather", lambda r: r["intent"] == "check_weather"),
            ("intents 含 check_weather", lambda r: "check_weather" in r["intents"]),
        ],
    },

    # ========== 场景 6：确认执行 ==========
    {
        "label": "确认执行方案A",
        "user_input": "就第一个吧，帮我订了",
        "checks": [
            ("primary intent 为 confirm_execution", lambda r: r["intent"] == "confirm_execution"),
            ("selected_index = 0", lambda r: r["selected_index"] == 0),
        ],
    },

    # ========== 场景 7：取消 ==========
    {
        "label": "取消执行",
        "user_input": "算了不订了",
        "checks": [
            ("primary 为 cancel_execution 或 confirm_execution", lambda r: r["intent"] in ("cancel_execution", "confirm_execution")),
            ("selected_index = -1", lambda r: r["selected_index"] == -1),
        ],
    },

    # ========== 场景 8：调整计划 ==========
    {
        "label": "调整计划（需 has_plans 上下文）",
        "user_input": "这个方案太贵了，换成实惠一点的",
        "has_plans": True,
        "checks": [
            ("primary intent 为 refine_plan", lambda r: r["intent"] == "refine_plan"),
        ],
    },

    # ========== 场景 9：模糊输入——低置信度 ==========
    {
        "label": "模糊输入（几个兄弟）",
        "user_input": "几个兄弟聚一下吃顿饭",
        "checks": [
            ("primary intent 为 plan_outing 或 find_activity", lambda r: r["intent"] in ("plan_outing", "find_activity")),
            ("companions 中 adults 为 null 或不存在（不应硬猜数字）",
             lambda r: r["raw_constraints"].get("companions", {}).get("adults") is None),
            ("evidence_map 中 companions 的 evidence 包含 兄弟",
             lambda r: "兄弟" in r.get("evidence_map", {}).get("companions", "")),
        ],
    },

    # ========== 场景 10：修改计划 ==========
    {
        "label": "修改已有方案中的活动",
        "user_input": "把刚才那个游乐园换成室内的吧",
        "has_plans": True,
        "checks": [
            ("primary intent 为 refine_plan", lambda r: r["intent"] == "refine_plan"),
            ("preferences 或 avoid 含 室内", lambda r: "室内" in str(r["raw_constraints"].get("preferences", [])) or "室内" in str(r["raw_constraints"].get("avoid", []))),
        ],
    },
]


def main():
    passed = 0
    failed = 0
    total_checks = 0
    start_time = time.time()

    for case in TEST_CASES:
        label = case["label"]
        user_input = case["user_input"]
        has_plans = case.get("has_plans", False)

        print(f"\n{'=' * 60}")
        print(f"[{label}]")
        print(f"  输入: {user_input}")
        if has_plans:
            print(f"  上下文: has_plans=True")

        try:
            result = run_router_extractor(user_input, has_plans=has_plans)
        except Exception as e:
            print(f"  [ERROR] 调用失败: {e}")
            failed += len(case["checks"])
            total_checks += len(case["checks"])
            continue

        print(f"  intent: {result['intent']}")
        print(f"  intents: {result['intents']}")
        print(f"  selected_index: {result['selected_index']}")
        print(f"  reply: {result.get('reply', '')[:50]}")

        rc = result.get("raw_constraints", {})
        if rc:
            companions = rc.get("companions", {})
            if companions:
                print(f"  companions: adults={companions.get('adults')}, "
                      f"children={companions.get('children')}, "
                      f"child_age={companions.get('child_age')}")
            print(f"  preferences: {rc.get('preferences', [])}")
            print(f"  diet_tags: {rc.get('diet_tags', [])}")
            print(f"  scene_tags: {rc.get('scene_tags', [])}")
            print(f"  budget_per_person: {rc.get('budget_per_person')}")
            print(f"  duration_minutes: {rc.get('duration_minutes')}")
            print(f"  date_text: {rc.get('date_text', '')}")
            print(f"  time_text: {rc.get('time_text', '')}")

        conf = result.get("extraction_confidence", {})
        evid = result.get("evidence_map", {})
        if conf:
            print(f"  confidence: {conf}")
        if evid:
            print(f"  evidence: {evid}")

        # 逐项检查
        case_ok = True
        for check_desc, check_fn in case["checks"]:
            total_checks += 1
            try:
                if check_fn(result):
                    passed += 1
                    print(f"    [PASS] {check_desc}")
                else:
                    failed += 1
                    case_ok = False
                    print(f"    [FAIL] {check_desc}")
            except Exception as e:
                failed += 1
                case_ok = False
                print(f"    [ERROR] {check_desc}: {e}")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"总计: {passed}/{total_checks} 通过 ({passed/max(total_checks,1):.0%}), "
          f"耗时 {elapsed:.1f}s, 平均 {elapsed/len(TEST_CASES):.1f}s/用例")


if __name__ == "__main__":
    main()

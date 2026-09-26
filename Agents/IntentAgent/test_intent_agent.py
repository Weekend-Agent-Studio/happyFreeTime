import time
from intent_agent import classify_intent

prompts = {
    # === 场景 1：经典单意图 (高纯度) ===
    "今天下午是空的，想和老婆孩子出去玩几个小时，别离家太远，帮我安排一下。": {
        "intents": {"plan_outing": 0.95},
        "primary": "plan_outing",
        "reasoning": "用户提出了明确的短时综合行程规划需求，没有附带其他特定查询指令。"
    },
    
    # === 场景 2：规划 + 天气 (最常见的双意图) ===
    "明天下午带孩子去公园玩，帮我规划一下路线，顺便看看会不会下雨。": {
        "intents": {"plan_outing": 0.85, "check_weather": 0.90},
        "primary": "check_weather", # 或者 plan_outing，取决于模型理解，这里天气是具体的前置条件
        "reasoning": "用户需要规划去公园的路线（plan_outing），同时也明确要求查询降雨情况（check_weather）。"
    },

    # === 场景 3：规划 + 找店 (包含强约束条件的规划) ===
    "周末兄弟四个聚一下搞个一条龙，晚饭必须要安排一家能喝酒看球的精酿酒吧。": {
        "intents": {"plan_outing": 0.85, "find_activity": 0.80},
        "primary": "plan_outing",
        "reasoning": "主要需求是兄弟聚会的一条龙综合规划（plan_outing），但包含明确查找特定条件餐厅的子需求（find_activity）。"
    },

    # === 场景 4：修改计划 + 找店 (多轮对话中的调整) ===
    "刚刚安排的那个游乐园太远了改掉吧，帮我在附近找个有包厢的烤肉店替代。": {
        "intents": {"refine_plan": 0.90, "find_activity": 0.85},
        "primary": "refine_plan",
        "reasoning": "用户要求否定并修改已有计划（refine_plan），并给出了新的具体查找目标烤肉店（find_activity）。"
    },

    # === 场景 5：纯单点查询 ===
    "这附近有什么适合5岁小孩玩的室内游乐园吗？": {
        "intents": {"find_activity": 0.95},
        "primary": "find_activity",
        "reasoning": "用户仅询问特定类型的游玩场所，没有提出综合行程规划要求。"
    },

    # === 场景 6：闲聊 + 天气 ===
    "你好呀，这周末天气怎么样，出门要带伞吗？": {
        "intents": {"check_weather": 0.95, "chitchat": 0.40},
        "primary": "check_weather",
        "reasoning": "包含礼貌性问候（chitchat），但核心诉求是查询周末天气和降雨情况（check_weather）。"
    },

    # === 场景 7：极端复合意图 (极限测试) ===
    "原来的计划取消吧，老婆在减肥不能吃火锅了。你重新帮我规划一个下午的行程，先看看天气，如果下雨就找个室内的素食馆，如果不下雨就去爬山。": {
        "intents": {"refine_plan": 0.80, "plan_outing": 0.90, "check_weather": 0.95, "find_activity": 0.75},
        "primary": "plan_outing",
        "reasoning": "推翻旧计划（refine_plan），要求重新整体规划（plan_outing），高度依赖天气查询结果（check_weather），且涉及寻找特定场所（find_activity）。"
    },

    # === 场景 8：纯闲聊/越界兜底 ===
    "你这个推荐引擎是怎么写的？用了什么算法？": {
        "intents": {"chitchat": 0.95},
        "primary": "chitchat",
        "reasoning": "用户在询问系统本身的技术实现，与活动规划、查询、天气等功能无关，属于闲聊兜底范畴。"
    }
}



def main():
    primary_correct = 0
    total_intent_overlap = 0.0  # Jaccard 累计
    total = len(prompts)
    start_time = time.time()

    for i, (prompt, expected) in enumerate(prompts.items(), 1):
        print(f"\n[{i}] {prompt}")
        res = classify_intent(prompt)

        expected_primary = expected["primary"]
        expected_set = set(expected["intents"].keys())
        predicted_primary = res["primary"]
        predicted_set = set(res["intents"].keys())

        # primary 准确率
        if predicted_primary == expected_primary:
            primary_correct += 1
            status = "OK"
        else:
            status = f"FAIL  (期望 primary={expected_primary}, 实际={predicted_primary})"

        # intent 集合重叠度 (Jaccard)
        intersection = expected_set & predicted_set
        union = expected_set | predicted_set
        jaccard = len(intersection) / len(union) if union else 0.0
        total_intent_overlap += jaccard

        print(f"  {status}")
        print(f"  期望: primary={expected_primary}  intents={expected_set}")
        print(f"  实际: primary={predicted_primary}  intents={predicted_set}")
        print(f"  Intents Jaccard: {jaccard:.2f}")

    end_time = time.time()
    avg_time = (end_time - start_time) / total

    print(f"\n{'=' * 50}")
    print(f"Primary 准确率: {primary_correct}/{total} = {primary_correct / total:.1%}")
    print(f"Intents 平均重叠度: {total_intent_overlap / total:.2f}")
    print(f"平均耗时: {avg_time:.2f}s")

if __name__ == "__main__":
    main()



"""短时活动规划助手 —— 入口"""
import json

from langgraph.types import Command

from Agents.graph import app

# TEST_INPUT = "今天下午是空的，想和朋友出去玩几个小时，别离家太远，帮我安排一下。"
# TEST_ANSWER = "孩子5岁男孩，玩到6点，预算500以内，喜欢户外活动"


def main():
    while True:
        config = {"configurable": {"thread_id": "1"}}
        TEST_INPUT = input("有什么可以帮到你？\n")
        print(f"用户: {TEST_INPUT}\n")

        result = app.invoke({"user_input": TEST_INPUT}, config=config)

        while True:
            snapshot = app.get_state(config)
            if not snapshot.next:
                break

            interrupt_data = snapshot.interrupts[0].value
            print(f"Agent 反问: {interrupt_data['question']}")

            try:
                user_answer = input("你的回答: ")
            except (EOFError, OSError):
                break
                print(f"你的回答: {user_answer}")

            result = app.invoke(Command(resume=user_answer), config=config)

        print(f"\n意图: {result.get('intent')}")
        slot = result.get("slot", {})
        print(f"位置: {slot.get('location', {}).get('address', 'N/A')}")
        print(f"天气: {slot.get('weather', {}).get('weather', 'N/A')}")
        print(f"参与者: {slot.get('companions', {})}")
        print(f"预算: {slot.get('budget', 'N/A')}")
        print(f"时间窗口: {slot.get('time_hint', 'N/A')}")

        plans = result.get("plans", {})
        if plans:
            print(f"\n{'=' * 50}")
            print("规划方案:")
            for plan in plans.get("plans", []):
                print(f"\n  [{plan.get('title', 'N/A')}] 风格: {plan.get('style', 'N/A')}")
                print(f"  总价: {plan.get('total_price', 'N/A')}元")
                for item in plan.get("timeline", []):
                    print(f"    {item.get('time', 'N/A')} | {item.get('step', 'N/A')} | {item.get('name', 'N/A')} | {item.get('price', 0)}元")
                if plan.get("highlights"):
                    print(f"  亮点: {', '.join(plan['highlights'])}")
            print(f"\n规划理由: {plans.get('reasoning', 'N/A')}")


if __name__ == "__main__":
    main()

"""短时活动规划助手 —— 入口"""
from langgraph.types import Command

from Agents.graph import app

TEST_INPUT = "今天下午是空的，想和老婆孩子出去玩几个小时，别离家太远，帮我安排一下。"

# 模拟用户回复（非交互模式下用）
TEST_ANSWER = "孩子5岁男孩，玩到6点，预算500以内，喜欢户外活动"


def main():
    config = {"configurable": {"thread_id": "1"}}
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
            user_answer = TEST_ANSWER
            print(f"你的回答: {user_answer}")

        result = app.invoke(Command(resume=user_answer), config=config)

    print(f"\n意图: {result.get('intent')}")
    slot = result.get("slot", {})
    print(f"位置: {slot.get('location', {}).get('address', 'N/A')}")
    print(f"天气: {slot.get('weather', {}).get('weather', 'N/A')}")
    print(f"参与者: {slot.get('companions', {})}")
    print(f"预算: {slot.get('budget', 'N/A')}")
    print(f"偏好: {slot.get('preferences', {})}")
    print(f"时间窗口: {slot.get('time_hint', 'N/A')}")
    print(f"is_complete: {slot.get('is_complete')}")
    print(f"反问问题: {slot.get('ask_question', 'N/A')}")


if __name__ == "__main__":
    main()

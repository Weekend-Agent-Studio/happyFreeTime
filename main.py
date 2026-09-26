"""短时活动规划助手 —— 入口"""
import json

from langgraph.types import Command

from Agents.graph import app
from time import time

# TEST_INPUT = "今天下午是空的，想和朋友出去玩几个小时，别离家太远，帮我安排一下。"
# TEST_ANSWER = "孩子5岁男孩，玩到6点，预算500以内，喜欢户外活动"


def main():
    print("有什么可以帮到你？\n")
    while True:
        # LangGraph 的会话 ID，每次对话共享同一个 ID，状态就会在多轮之间保持（靠 MemorySaver
        config = {"configurable": {"thread_id": "1"}}
        TEST_INPUT = input("输入：")

        # 把用户输入推进 Graph，图从头跑到尾（或被中断）
        result = app.invoke({"user_input": TEST_INPUT}, config=config)

        while True:
            # 查看这个 thread 的图当前停在哪
            snapshot = app.get_state(config)
            # 如果为 None/空，说明图已经跑到 END 了，结束
            if not snapshot.next:
                break
            
            # 如果图被 interrupt() 暂停了，这里就是挂起的原因（即 Slot 的反问）
            interrupt_data = snapshot.interrupts[0].value
            print(f"Agent 反问: {interrupt_data['question']}")

            try:
                user_answer = input("你的回答: ")
            except (EOFError, OSError):
                break

            result = app.invoke(Command(resume=user_answer), config=config)

        reply = result.get("reply", "")
        if reply:
            print(f"\nAgent: {reply}\n")
            continue

        slot = result.get("slot", {})
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

        execution = result.get("execution", {})
        if execution:
            print(f"\n{'=' * 50}")
            print("执行结果:")
            print(f"  {execution.get('summary', 'N/A')}")


if __name__ == "__main__":
    start_time = time()
    main()
    end_time = time()

    print("使用的时间：", end_time - start_time)

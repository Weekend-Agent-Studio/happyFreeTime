"""HappyFreeTime V2 的交互式命令行入口。

CLI 主要用于绕过 HTTP 和前端观察 Graph 行为；默认使用真实 Router，因此需要
配置 LLM_API。无 Key 的完整演示建议使用 FastAPI 的 HFT_DEMO_MODE=1。
"""

from dotenv import load_dotenv
from langgraph.types import Command

from app.domain.constraints import ActorContext, IdentityType
from app.orchestration.entry_graph import build_entry_graph, default_environment_provider
from app.services.router_extractor import build_default_router_extractor


def _print_result(result: dict) -> None:
    """把 Graph 的结构化状态转换成人类可读的终端输出。"""
    interpretation = result.get("interpretation")
    if interpretation and interpretation.reply:
        print(f"\n助手：{interpretation.reply}\n")
        return

    enrichment = result.get("enrichment")
    if enrichment and enrichment.assumptions:
        print("\n本次规划采用的可修改假设：")
        for assumption in enrichment.assumptions:
            print(f"  - {assumption.field}: {assumption.value} ({assumption.reason})")

    candidate_set = result.get("candidate_set")
    if candidate_set is None:
        return
    if candidate_set.conflict is not None:
        print(f"\n暂时无法生成方案：{candidate_set.conflict.message}")
        for option in candidate_set.conflict.relaxation_options:
            print(f"  - {option}")
        print()
        return

    print("\n候选方案：")
    for index, plan in enumerate(candidate_set.plans, start=1):
        print(
            f"\n[{index}] {plan.title} | {plan.total_price} 元 | "
            f"{plan.total_duration_minutes} 分钟 | {plan.total_score:.1f} 分"
        )
        for stop in plan.stops:
            print(f"  {stop.start}-{stop.end} {stop.name} ({stop.price} 元)")
        for route in plan.route_legs:
            print(
                f"  路线估算：{route.origin_name} -> {route.destination_name}，"
                f"{route.distance_km}km / {route.duration_minutes} 分钟"
            )
        if plan.highlights:
            print(f"  亮点：{'；'.join(plan.highlights[:3])}")
        if plan.tradeoffs:
            print(f"  取舍：{'；'.join(plan.tradeoffs[:2])}")
    print()


def main() -> None:
    """创建单用户内存 Graph，并循环处理输入与 interrupt 反问。"""
    load_dotenv()
    actor = ActorContext(
        user_id="demo-user",
        session_id="demo-session-v2",
        identity_type=IdentityType.DEMO,
    )
    graph = build_entry_graph(
        router=build_default_router_extractor(),
        environment_provider=default_environment_provider,
    )
    config = {"configurable": {"thread_id": actor.session_id}}

    print("HappyFreeTime V2，输入 exit 退出。")
    while True:
        user_input = input("\n你：").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        result = graph.invoke(
            {"user_input": user_input, "actor": actor},
            config=config,
        )
        # Graph 在 interrupt 处暂停时，CLI 原地收集答案并恢复同一 thread。
        while True:
            snapshot = graph.get_state(config)
            if not snapshot.next:
                break
            question = snapshot.interrupts[0].value["question"]
            answer = input(f"助手反问：{question}\n你：").strip()
            result = graph.invoke(Command(resume=answer), config=config)

        _print_result(result)


if __name__ == "__main__":
    main()

# 在确定性规划内核外采用受约束混合 Agent Graph

> Status: accepted · 2026-09-04

HappyFreeTime 保留 M2 的确定性可信规划内核，在其前方增加组合式对话控制，在规划内部增加有界语义决策：模型解释 `ConversationCommand`、提出 `InformationNeed` 和 `PlanningIntent`、调用白名单只读 Capability，并在 verified finalist 间评价软取舍；Harness 负责上下文投影、必需 Provider、结构编译、硬约束、预算、实际终止、权限和副作用。这样选择是为了让 LLM 真正影响方案，同时不牺牲路线、时间、库存、营业与执行的可复现性。

## Considered Options

- 继续纯确定性 Workflow：延迟低、易测试，但开放语言、模糊体验检索、定向修改和工具选择会持续退化为关键词与分支堆叠。
- 恢复全流程 ReAct：模型自主性强，但天气、地理编码和路线等必需调用会产生无收益循环、延迟、费用和不可复现行为。
- 拆成多个角色 Agent：没有独立权限、状态或领域所有权支撑这些边界，只会增加 Prompt 交接与调试成本。
- 受约束混合 Agent Graph：选择此方案；模型只在存在语义判断价值的 Seam 上决策，确定性 Module 保持事实和不变量。

## Consequences

- 当前 M2 版本仍应描述为“Graph Workflow + 可信规划内核”；完成 M3 的模型观察循环、语义规划影响与评测后，才使用“受约束规划 Agent”。
- `ContextAssembler`、`ToolBroker`、`CandidateRetriever` 和 `PlanningService` 必须保持小 Interface、深 Implementation；Provider 调用不为了 Graph 化而全部节点化。
- 所有模型决策必须有 schema、来源、预算、停止原因、确定性 fallback 和离线消融证据。
- RAG 与 MCP 通过 Retriever/Capability Adapter 扩展，不改变领域契约，也不能绕过 Actor scope、Verifier、确认和执行状态机。

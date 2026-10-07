# S-CORE3G：架构收口与当前实现说明

更新日期：2026-10-06  
范围：S-CORE3A–G，基于 S-CORE3F 提交 `8e297a3` 的开发分支。

> 历史快照：H1–H4 在本文之后继续收敛了请求生命周期、时间 Wire Contract、动态 PlanSpec 和旧动作路径。当前执行契约与最终复验以 [S-CORE3H4 收口记录](s_core3h4_closeout_20261007.md) 和 [S-CORE3 面试说明](../interview/09_S-CORE3从多入口到统一动作链路.md) 为准；本文保留 G 阶段的过程记录。

## 收口结论

S-CORE3 将自然语言入口、约束更新、定向修改和 Graph 状态边界收敛为以下主链：

```text
自然语言
  → TurnProposal + DecisionContext
  → TurnCompiler
  → CompiledNextAction
  → Request/Modification/Query workflow

顶部栏 / 反问回答
  → typed RequestPatch

RequestPatch
  → ConstraintEngine
  → PlanRequest
  → Readiness / QuestionPolicy
  → PlanningIntent / PlanSpecCompiler
  → Retrieval / Beam / Scheduler / Provider / Verifier
  → PlanVersion / PlanDiff / Advisor / Presenter
```

Graph 负责节点、路由、interrupt/resume 和 checkpoint；紧密的检索、排程、Provider 调用和验证仍由深层 Service 完成。FastAPI 只负责 HTTP DTO、依赖组合和错误映射，完整 planning turn 由 `PlanningTurnApplication` 负责。

## 各切片交付

- **3A：** `DecisionContext` 由确定性 Builder 从 Repository、checkpoint 和当前 Session 投影，限制模型只看到完成引用解析所需的摘要。
- **3B：** `TurnProposal` 使用 UserAct 联合；`TurnCompiler` 生成唯一 `CompiledNextAction`，Graph 不再根据模型的 `primary_intent` 或旧 `operation` 路由。
- **3C：** 地点、距离、时间和偏好保留作用域；Router 判断语义作用域，Normalizer 做格式转换，Enrichment 只补环境默认值和 Provider 事实。
- **3D：** 顶部栏、创建提案、更新提案和反问回答各自保留入口适配器，共享字段级 Normalizer，最终都生成 `RequestPatch`。
- **3E：** Graph 状态按 Durable Session、Pending Interaction、Turn Input、Turn Result 分层；过期 revision/clarification 和中断恢复在 workflow 内处理。
- **3F：** `PlanningTurnApplication` 承担加载状态、校验版本、调用/恢复 Graph、持久化 run、创建 PlanVersion/PlanDiff 及响应组装。
- **3G：** 删除无运行时职责的旧入口别名，更新当前架构、状态所有权和 checkpoint 说明；不删除仍被执行链消费的内部投影。

## 有意保留的内部类型

`Interpretation`、`Intent.REFINE_PLAN` 和 `ConversationCommand` 仍被现有 Enrichment、请求编译、Planner、Modification 和冻结评测实际消费：

- `TurnProposal.kind` / `CompiledNextAction.kind` 是模型动作和 Graph 路由的唯一判别层；
- `Interpretation` 是执行链所需的内部语义投影，不是模型 Wire Schema，也不是旧 checkpoint Adapter；
- `ConversationCommand` 是定向替换的结构化执行契约，不允许模型凭资源 ID 越权；
- `Intent.REFINE_PLAN` 当前是历史命名的内部投影，不能作为对外动作或新的模型协议。

因此 3G 不强行删除这些仍有消费者的类型，以免把架构收口变成另一轮语义迁移。后续若要彻底删除，应单独做执行服务的领域接口迁移并重新冻结评测。

## 状态所有权与恢复

- Repository 是 `PlanRequest`、`PlanVersion`、selected plan 和业务会话数据的事实来源。
- Graph checkpoint 只保存暂停位置、pending interaction、本轮临时状态和恢复所需的中间值。
- 当前 checkpoint schema 为 `planner-core3e-v1`；旧开发 checkpoint 不做兼容读取，新开发会话需使用新 schema。
- 本次清理不删除本地消息/会话历史；若需要清空开发数据，应显式删除对应本地数据库，而不是由应用启动隐式迁移。

## 3G 清理项

已删除：

- `entry_graph.Router` 兼容别名；
- `router_extractor.RouterExtractor` 兼容别名；
- `build_default_router_extractor` 旧构造别名。

仍保留：

- 真实执行链需要的内部 `Interpretation`/`ConversationCommand`；
- 旧提交、冻结 Fixture 和历史报告，用于版本对比和可追溯性；
- `Plan.skeleton_id` 等稳定输出字段，它们现在只承载 PlanSpec ID，不代表旧 Skeleton 模型仍存在。

## 最终验证（5ea7341）

本次在干净提交上完成后端、前端和完整 C/B 评测。报告目录均位于 `artifacts/evals/`，不保存原始模型响应或密钥：

| 变体 | 任务成功 | 必需断言 | 硬约束 | 关键说明 |
|---|---:|---:|---:|---|
| C0 Rule + Rule | 34/36 | 206/208 | 7/7 | 两条已知 Rule 语义证据缺口 |
| C1 LLM Intent + Rule | 34/36 | 198/200 | 7/7 | PlanningIntent 17/17，无 fallback |
| C2 Rule + Hybrid | 36/36 | 208/208 | 7/7 | Hybrid 补齐两条语义证据 |
| C3 LLM Intent + Hybrid | 36/36 | 208/208 | 7/7 | PlanningIntent 17/17，无 fallback |
| C4 + Advisor | 36/36 | 208/208 | 7/7 | Advisor 23/27 接受，4 次安全回退 |
| B0 Live Router | 28/36 | 174/196 | 6/6 | Router 36 次，1 次结构化回退 |
| B3 Live Router + Advisor | 29/36 | 174/196 | 6/6 | Advisor 17/22 接受，5 次安全回退 |

所有 Frozen C0–C4 的冲突诊断为 4/4，修改链路均为 9–10/10，目标替换、锁定站点保持和 PlanDiff 均通过。C4 Advisor 接受样本的 Plan ID、Fact/Evidence grounding 为 100%；`understood_need_grounding` 仍显示为 0/23，因为当前 V2 Advisor 合同已不再让模型输出 `understood_needs`，该旧指标不应作为质量结论。

延迟分开记录：C3 BGE 冷启动约 34.8 秒、稳态检索约 P50/P95 197/483ms；C4 Advisor P50/P95 2588/3286ms，端到端 P50/P95 3090/9971ms；B3 端到端 P50/P95 2555/12903ms。Live 成功率受真实 Router 输出波动影响，不能与 Frozen 结果合并成生产成功率。

工程验证：后端 444 passed；前端 Vitest 36 passed，TypeScript 检查和 Vite build 通过；Python 编译与 `git diff --check` 通过。前端验证使用主工作树已安装依赖的临时 Junction，验证后已移除。

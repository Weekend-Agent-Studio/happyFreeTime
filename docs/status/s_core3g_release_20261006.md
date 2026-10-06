# S-CORE3G：架构收口与当前实现说明

更新日期：2026-10-06  
范围：S-CORE3A–G，基于 S-CORE3F 提交 `8e297a3` 的开发分支。

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

## 验证口径

本记录只收口架构与文档，不把尚未在本提交上执行的 Live B0/B3 或真实 LLM C1/C3/C4 结果写成发布指标。最终发布前仍应在干净提交上执行后端、前端、编译检查、Frozen C0–C4、B0/B3 和重点手工 E2E，并在报告中分开记录冷启动与稳态延迟。

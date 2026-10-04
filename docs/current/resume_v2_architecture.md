# Resume V2 当前架构

_Resume V2 当前实现快照；核对日期：2026-10-04。S-CORE2A–E 在 `codex/s-core2-request-engine` 上完成，尚未合并 main；本文件描述该分支的实现。发布 tag 仍是冻结评测基线。_

---

## 📋 文档定位

这是一份“当前系统是什么”的架构说明，依据：

- 发布 tag：planner-v2-eval-baseline，指向 bef5fa3；
- 评测代码提交：db8603b，是发布 tag 的祖先；
- 36 条人工复核 Frozen Fixture；
- S-CORE1A/B/C 的代码、自动测试和阶段报告；其评测代码提交 `52fd353` 的正式结果见 [S-CORE1C 报告](../status/s_core1c_release_20261002.md)。
- S-CORE2A–E 的代码、测试和本次验收报告；该分支基于已合并 S-CORE1 的 `origin/main@26df327`，仍需单独 PR。冻结 tag 的历史指标仍只对应旧提交。

如果本文与根 README、代码或自动测试冲突，以代码和测试为准。目标架构、长期记忆、真实执行、Saga 和 MCP 演进见 canonical/architecture_v2.md，不能从目标设计推断为当前能力。

## 🎯 当前系统解决什么问题

系统面向北京本地生活和周末出行规划，输入可以是：

- 日期、出发地、出发/返程时间；
- 同行人、预算、距离和饮食要求；
- 活动、餐食、节奏、场景和开放偏好；
- 选中方案后的定向修改。

系统输出最多三个已经通过事实和硬约束复核的候选方案，或者输出结构化反问/冲突。当前使用版本化 POI Catalog、可复现 Demo World，以及 mock/replay/live Provider Adapter；Demo World 的商业信息是模拟数据，不是实时商户承诺。

## 🔗 运行时总链路

```mermaid
flowchart TB
    accTitle: Resume V2 Runtime Architecture
    accDescr: The current implementation separates natural language interpretation, deterministic planning, external fact verification, grounded presentation, and durable session state.

    input([👤 User request]) --> api[🌐 FastAPI application]

    subgraph graph_control["⚙️ Stateful graph"]
        api --> turn[🧠 TurnInterpreter or DemoRouter]
        turn --> compile[⚙️ Enrichment + Proposal Compilers]
        compile --> engine{🧭 ConstraintEngine}
        engine -->|NeedsClarification| policy[❓ QuestionPolicy]
        policy --> pause[🔒 Interrupt and SQLite checkpoint]
        pause --> input
        engine -->|Resolved| policy
        policy -->|ready| intent[🧠 PlanningIntent adapter]
        engine -->|Conflict| conflict[⚠️ Structured conflict]
    end

    subgraph planning_core["⚙️ Planning core"]
        intent[Rule semantics + optional LLM proposal] --> spec[🛡️ PlanSpecCompiler]
        spec --> retrieve[🔍 Catalog and Hybrid Retrieval]
        retrieve --> search[⚙️ Beam Search]
        search --> schedule[⚙️ Timeline Scheduler]
        schedule --> facts[🔌 Route, weather, availability]
        facts --> verify[🛡️ Verifier and bounded repair]
    end

    verify --> advice[🧠 Grounded Advisor or rule fallback]
    advice --> state[💾 Plan Version and Session Snapshot]
    state --> output([📤 CandidateSet, conflict, or question])
    output --> api
```

Graph 只编排有状态分支、反问恢复和有限决策；普通过滤、评分、Provider 调用和搜索保持在 Service/Adapter 内部，不为每个函数增加 Graph 节点。

## 🧠 语义入口与状态边界

### TurnInterpreter

真实 Router 输出受限的 Wire Proposal，主要包括：

- 用户操作：创建、修改、查询或聊天；
- 原始约束和用户证据；
- 受限的修改目标引用；
- PlanningIntent 所需的语义入口。

模型不能输出真实资源 ID、应用已经知道的方案 ID、路线价格营业库存等外部事实，也不能生成可以绕过权限和状态校验的执行命令。

Wire Proposal 由 Harness 编译为内部领域对象。旧 Interpretation 字段仍保留部分兼容能力，但不应理解为模型直接拥有整个领域状态。

### PlanRequest、RequestPatch 与 QuestionPolicy

`PlanRequest` 是 Planner 唯一读取的规划条件快照。自然语言入口由 `EnrichmentService` 与 `RequestPatchProposalCompiler` 把 Router Wire Proposal 编译为一个 `RequestPatch`；顶部栏由类型化 API DTO 编译为同一 Patch；反问恢复由字段级 Compiler 生成 Patch。三类入口都交给纯确定性的 `ConstraintEngine` 原子应用、校验 revision、跨字段硬冲突并更新请求 revision。模型不直接写 PlanRequest，也不裁定硬约束。

`ConstraintEngine` 的结果分为 `ResolvedRequest`、`NeedsClarification` 和 `ConflictedRequest`。`QuestionPolicy` 只处理结构化 Issue 与当前交互条件，以确定性规则决定是否阻断并渲染模板问题；它不从 `RawConstraints` 或用户原话里二次抽取约束，也不调用 LLM。`RawConstraints` 仍是 Router Proposal 的有限抽取 DTO，不是 Planner 输入或执行状态。

反问由 `interrupt/resume` 恢复。每个待问 Issue 绑定 `clarification_id + request_revision`；用户回答由字段 Compiler 限定在当前字段，顶部栏 Patch 可解决对应 Issue。旧 ID/revision 被拒绝为明确的 stale 状态；有效回答不会把整段对话重新送回 Router。

### CREATE、约束修改与局部替换

- CREATE：Router Proposal → Enrichment/时间 Proposal 编译 → `RequestPatch` → `ConstraintEngine` → `PlanRequest` → Readiness/QuestionPolicy → Planner。
- 方案后约束修改：`RequestPatchUpdateCompiler` 编译日期、时间、地点、预算、距离、偏好和清除操作；再由同一 Engine 应用，成功后保存新请求 revision。
- 反问回答：`ClarificationPatchCompiler` 只编译当前待补字段，并复用更新 Patch Compiler 与同一 Engine；取消/新需求和方案目标引用仍由 `ClarificationResolver` 处理，它不再修改规划约束。
- 顶部栏：`planning_context` 是 PlanRequest 的只读投影；输入经类型化 API DTO 编译，不经过 Router。保存约束与按新条件重新规划分离；`plan_stale` 表示当前显示方案仍对应旧 request revision。
- 定向替换仍走 `ConversationCommand` / Command Compiler 边界；它不伪装成通用 RequestPatch。

## 🧩 结构提案与规划搜索

### PlanningIntent、Proposal 与 PlanSpecCompiler

模型每次规划至多输出一个 `PlanStructureProposal v3` Wire DTO，其中可以同时包含 1–4 个有序角色 slot、core/optional、pace、受证据约束的 objectives 和角色级检索 query。模型没有输出第二份 `PlanningIntent` 或可执行 `PlanSpec`：Harness 从 proposal 投影出内部 `PlanningIntent`（仅 `pace + semantic_request`），再由 `PlanSpecCompiler` 把结构部分编译为一个或多个 concrete `PlanSpec`。optional slot 只存在于 proposal，Compiler 将其有界展开；`PlanSpec` 本身只有确定的有序角色序列。

用户显式站数/角色由同一个 `PlanSpecCompiler` 预检并保持优先；没有 LLM 提案时，Rule baseline 也由该 Compiler 内的私有工厂直接生成 `PlanSpec`。旧 `PlanSkeleton` 领域模型、注册表和转换 Adapter 已删除。PlanSpecCompiler 负责结构合法性、用户显式结构优先级、证据引用和稳定 ID；它不证明 POI、路线、营业或时间轴可行，这些由搜索、Scheduler、Provider 和 Verifier 处理。

若已接受的 LLM 结构经本地排程或 Route/Availability/Verifier 仍无可行方案，Harness 最多尝试一次完整 Rule 恢复：重用确定性 Rule `PlanningIntent`，重新按角色召回并排序，使用 Rule PlanSpec，再走同一搜索和验证 Seam；不二次调用 LLM，且优先路径会为恢复预留有限 Provider 预算。Trace 记录 proposal、编译结果、失败阶段、失败字段和最终 PlanSpec。当前外部 `Plan.skeleton_id` 是历史字段名，仅承载稳定 PlanSpec ID，不表示仍存在 Skeleton 模型。

当前不是“模型从注册表中挑一个骨架”，也不是“模型直接生成最终路线”，而是“模型提出受约束结构与软语义，Harness 编译并搜索，Verifier 对具体行程证明可行”。

### Beam Search

Beam 是当前默认搜索路径，主要价值是资源控制：

- 为不同结构保留公平搜索机会；
- 限制组合扩展量；
- 用语义、时间、营业和路线估算做软排序；
- 保留 finalist 进入真实 Route、Availability 和 Verifier；
- 不把本地估算误当作最终硬事实。

Beam 是默认主路径。Legacy Search 仅通过显式实验模式或 Beam 在没有观察到 Provider/硬约束失败时的有界搜索恢复使用；两种算法消费相同 `PlanSpec`，没有旧 Skeleton 转换链。Rule 结构恢复与 Legacy 搜索恢复是不同层次，不能把后者算成第二次 LLM 调用。Beam 的价值主要是资源控制；评测是否证明质量收益以当前 S-CORE1C 报告为准。

### 时间与路线

- 精确出发、返程截止和显式时间窗是用户约束；
- 午餐/晚餐使用角色级餐时锚点；
- 本地路线估算用于排序和预算控制；
- finalist 才调用 Route Provider 重建真实或 replay 时间线；
- 最终由 Verifier 检查路线、时间窗、营业、预算、距离、返程和 Availability。

当前没有完整 Temporal AST。Planner 只读一个 `PlanningWindow(date, start_at, end_at)`；开始/结束各自携带 `ConstraintValue` 来源，`start_kind` 为 `trip_start | departure`，`end_kind` 为 `trip_end | return_deadline`。Router 仅在时间作用域有歧义时提出轻量 `TimeProposal(target, precision, clock/period, evidence)`。默认窗口可见、可编辑；未提时间采用默认窗口，不因此反问。“早上出去玩”影响整体窗口，“早上出发”只约束出发并可能进入字段级澄清，“晚上八点前回来”以 return deadline 进入硬校验。时间含义来自显式 kind，不由前端或 rule_id 推断。

## 🔌 Provider、数据和降级

| Provider | 作用 | 可用模式 |
| --- | --- | --- |
| Catalog | POI 召回和静态硬过滤 | snapshot、fixture、demo |
| Weather | 日期/地点天气事实 | mock、replay、record、live |
| Route | 站间距离、时长和 geometry | mock、replay、record、live、本地估算 |
| Geocoding | 用户地点归一化 | mock、replay、live、降级 |
| Availability | finalist 的动态可用性检查 | mock、replay、降级 |

统一事实包含来源、核验时间和降级原因。未知或陈旧事实不会被伪装成已确认事实。

## 💾 持久化与恢复

- Session：连续对话和用户归属；
- Planning Run：一次逻辑请求及其幂等边界；
- Plan Version：不可变候选方案版本；
- PlanDiff：修改前后的结构化差异；
- SQLite checkpoint：Graph 控制流暂停和恢复；
- Provider replay/cache：评测和离线复现所需的规范化事实。

Checkpoint 不是订单事实，也不是长期记忆。当前没有真实订单、预订、叫车或长期用户记忆模块。

当前 Graph 状态以 `active_request`、一个 `pending_issue`、待应用 Patch、方案/候选结果和运行诊断为主；Enrichment 结果只作为瞬时输出，假设与地理编码事实按需单独存储。`CHECKPOINT_SCHEMA_VERSION=planner-core2e-v1`。旧开发 checkpoint 不做读取 Adapter；旧测试会话需新建，不删除用户本地数据库或历史记录。

## 📊 当前评测证据

详见冻结发布基线与 [S-CORE2E 验收报告](../status/s_core2e_release_20261004.md)。在本分支一次复验中：

- Frozen C0–C4 使用同一份 36 条 reviewed Interpretation；C0/C1 为 34/36，C2/C3/C4 为 36/36；各冻结变体硬约束 7/7、冲突归因 4/4、修改链路 10/10；
- C4 Advisor 结构有效 27/27，接受 23/27，其余 4 次按规则安全回退；
- Live B0/B3 分别为 26/36 和 28/36；硬约束均 6/6、冲突归因均 4/4。这是一次实时模型诊断，不代表通用或生产成功率，B3 也有模型波动；
- S-CORE2 Clarification Eval 24/24 子案例通过；端到端前端浏览器用例覆盖条件保存/重规划和从顶部栏解决反问，具体 runner 收尾限制见报告；
- Hybrid Retrieval 的历史正式 Recall@5 为 0.537，Rule baseline 为 0.240；Advisor 接受结果经过 Plan/Evidence/Fact ID grounding，失败时安全回退；
- BGE 冷启动延迟与稳态延迟分开记录。

## 🚫 当前明确未实现

以下内容在目标架构或路线图中出现，但不属于 Resume V2 当前能力：

- 真实下单、预订、叫车和 Saga 补偿；
- 可确认、可删除的长期记忆；
- MCP Client/Server Adapter 接入主链；
- 任意城市和任意交通方式；
- 任意自然语言自动生成无限骨架；
- 生产级实时 POI、库存和价格；
- 完整 Temporal AST 和通用 Constraint AST。

这些内容应进入路线图或 backlog，不应写进当前系统介绍。

## 📍 代码入口

| 层 | 入口 |
| --- | --- |
| Graph/API | app/orchestration/entry_graph.py、app/api/application.py |
| 领域契约 | app/domain/ |
| 语义入口 | app/services/router_extractor.py、app/services/demo_router.py |
| 请求编译与执行 | app/services/enrichment.py、app/services/request_patch_compiler.py、app/services/request_patch_update.py、app/services/constraint_engine.py |
| 反问策略与恢复 | app/services/question_policy.py、app/services/clarification_patch.py、app/services/clarification.py |
| 规划与结构 | app/services/planning.py、app/services/planning_intent.py、app/services/plan_spec_compiler.py |
| Provider | app/providers/ |
| 持久化 | app/persistence/ |
| 评测 | evals/ |

## 🎓 学习建议

先读根 README 和发布报告建立当前系统概念；再读本文；随后按一次请求流向阅读 TurnInterpreter → Proposal/Enrichment Compiler → ConstraintEngine → QuestionPolicy/Readiness → PlanningIntent → PlanSpecCompiler → PlanningService → Provider/Verifier → Persistence。自然语言、顶栏、反问三种入口的 Patch 最终汇入同一个 PlanRequest。

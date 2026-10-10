# S-CORE3：从多入口、多重动作表示到统一动作链路

_HappyFreeTime 面试设计说明 · 更新日期：2026-10-07 · 对应收口提交：`2e9f0f0`_

本文记录 S-CORE3 的重构故事：为什么自然语言、顶部栏、反问恢复和方案修改不能各自维护一条业务路径，以及如何把它们收敛成一个可解释、可验证的状态感知动作链路。它是面试讲解材料，不替代当前代码契约；当前实现以[当前架构](../current/resume_v2_architecture.md)、测试和[H4 收口记录](../status/s_core3h4_closeout_20261007.md)为准。

## 一句话结论

S-CORE3 没有把更多规划权交给模型，而是把模型的开放语义限制在 `TurnProposal`，再由确定性的 `TurnCompiler` 结合有限 `DecisionContext` 编译成唯一 `CompiledNextAction`。顶部栏和字段反问直接产生类型化 `RequestPatch`；所有请求变化都经过同一个 `ConstraintEngine`，方案修改则进入 `ModificationService`。这样 Graph 负责恢复和路由，Planner/Provider/Verifier 负责可执行性。

## 为什么要重构

早期链路大致是：

```text
自然语言
  → Interpretation(primary_intent + conversation_command)
  → Graph 根据多个字段和布尔值分支
  → 各 workflow 自己解释请求、拼 Patch 或修改方案

顶部栏 / 反问回答
  → 独立的 Patch / Resolver / API 分支
```

这会产生几个实际问题：

- 一个动作同时由 `primary_intent`、`conversation_command.operation` 和 Graph 布尔状态表达，字段之间可能不一致；
- 上下文过少时，模型无法判断“第二站”“还是便宜点”对应当前哪个请求或方案；
- 顶部栏、自然语言和反问回答可能用不同的合并规则，导致约束漂移；
- Graph 逐渐承担业务判断，API 也容易变成第二个 orchestrator；
- `Interpretation` 既像模型输出，又像领域状态，导致兼容适配层不断累积。

## 重构后的主链

```text
自然语言
  → DecisionContext + TurnProposal
  → TurnCompiler
  → CompiledNextAction
       ├─ ApplyRequestPatch
       │    → RequestPatch → ConstraintEngine → PlanRequest
       │    → RequestReadinessPolicy / QuestionPolicy
       │    → Planner
       ├─ ModifySelectedPlan
       │    → ModificationService → PlanVersion / PlanDiff
       ├─ AnswerQuery
       └─ NeedsClarification / NoAction

顶部栏 typed DTO ───────────────┐
字段反问回答 ──────────────────┼→ RequestPatch → ConstraintEngine
                              ┘
```

规划执行部分保持确定性：

```text
PlanRequest
  → PlanningIntent + PlanStructureProposal
  → PlanSpecCompiler
  → Catalog / Hybrid Retrieval / Beam Search
  → Timeline Scheduler
  → Route / Weather / Availability / Verifier
  → Grounded Advisor 或 Rule fallback
  → PlanVersion / PlanDiff / SQLite
```

关键点是：`TurnProposal` 只表达用户动作和有限语义；模型不能创建方案 ID、资源 ID、路线或价格事实。`DecisionContext` 只提供当前判断需要的摘要，不把完整聊天历史或数据库对象塞进 Prompt。

## 主要阶段

| 阶段 | 解决的问题 | 结果 |
| --- | --- | --- |
| 3A | Router 不知道当前请求、选中方案和待补字段 | 有限 `DecisionContext`，支持引用解析和动作权限判断 |
| 3B | `primary_intent` 与 `operation` 双重表示 | `TurnProposal` UserAct 联合 + 唯一 `CompiledNextAction` |
| 3C | `location_text`、时间和偏好作用域丢失 | 区分 origin/planning area、trip/departure/return 时间作用域；Enrichment 不再猜用户意图 |
| 3D | 顶部栏业务堆在 FastAPI，入口各自规范化 | `PlanningContextApplication` 和共享字段 Normalizer；所有入口最终只产生 Patch |
| 3E | Graph state 有多组可推导 flag，恢复路径重复 | 生命周期明确、工作流边界清晰、stale revision/clarification 可拒绝 |
| 3F | `application.py` 同时负责 HTTP 和完整用例 | `PlanningTurnApplication` 负责一次 planning turn，FastAPI 只做接口映射 |
| 3G | 旧路径、旧文档和当前契约并存 | 清理旧动作来源，更新架构和冻结说明 |
| H1 | 空 `PlanRequest` 被当成已有请求 | `empty/draft/planned` 生命周期和 allowed actions |
| H2 | 多空字段 TimeProposal 容易产生错误组合 | `TripRange`、`EventClock`、`Period` 判别联合和安全降级 |
| H3 | 部分显式结构被固定骨架误拒绝 | 动态 `PlanSpec` 编译，Rule 结构只作为安全 fallback |
| H4 | 生产 Graph 仍保留旧 Interpretation/tuple 动作回退 | Graph 只接受 `interpret_with_runtime() → TurnInterpreterResult`；历史夹具只能在边界显式投影 |

## 三个具体例子

### 1. 已有请求后的补充约束

用户说“还是便宜点吧”。Router 得到 `PatchConstraintsProposal`，但不直接修改状态；`TurnCompiler` 将它编译为预算 `RequestPatch`，`ConstraintEngine` 在当前 revision 上合并。若预算不足，返回结构化 conflict 或 clarification；不会把整段历史重新拼接后再猜一遍。

### 2. 基于当前方案的定向修改

用户说“把第二个换安静一点”。模型只输出目标位置和软语义，不输出数据库资源 ID。`DecisionContext.selected_plan_summary` 提供站点顺序和角色，Compiler 把“第二个”解析为站点索引，随后 `ModificationService` 保留非目标站并生成新的 `PlanVersion + PlanDiff`。

### 3. 顶部栏和反问恢复

用户在 When 面板选择日期，或回答“早上九点”。这两种输入都由对应 Adapter 产生类型化 `RequestPatch`，绕过 Router，但仍经过同一个 `ConstraintEngine`。缺少 Planner 必需字段时，`RequestReadinessPolicy` 交给 `QuestionPolicy`，Graph 用 interrupt/checkpoint 暂停；恢复时只更新待补字段，并校验 clarification ID 和 request revision。

规划失败恢复与字段反问是两种策略，不是一个开放 Agent Loop：字段缺失或模糊由 `QuestionPolicy` 提问；硬冲突、无可行方案和修改失败由 `RecoveryPolicy` 根据结构化失败事实给出有限 `RecoveryAction`。只有系统默认的距离限制允许自动尝试放宽一次；显式约束必须由用户确认。按钮携带 action ID 和类型化 payload，不再把按钮文案送回 Router；恢复 Patch 仍由 `ConstraintEngine` 校验，并复用原规划/修改链路。该恢复实现目前仍在独立分支验收，发布结论以完整 API、前端和 C/B 回归通过后的报告为准。

## 这次重构没有改变什么

S-CORE3 主要收敛控制面，不是重写 Planner。Catalog 过滤、Hybrid Retrieval、Beam 搜索、时间轴、Provider 和 Verifier 仍是独立的确定性执行层；Advisor 也在 Graph 外部，只能引用已验证的 Plan/Evidence/Fact。没有在本轮引入长期记忆、MCP、真实预订、完整 Temporal AST 或新的搜索算法。

## 证据与边界

在干净的 H4 提交上：

| 层级 | 结果 |
| --- | --- |
| Frozen C3 | 36/36，硬约束 7/7，冲突归因 4/4 |
| Frozen C4 | 36/36，硬约束 7/7，冲突归因 4/4；Advisor 接受 22/27，其余安全回退 |
| Live B0 | 30/36，硬约束 7/7，冲突归因 4/4，P50/P95 1598/2473ms |
| Live B3 | 33/36，硬约束 7/7，冲突归因 4/4，Advisor 接受 23/25 |

Frozen 结果用于组件回归；Live 结果受真实 Router 波动影响，只作为稳定性诊断，不能写成通用生产成功率。完整失败分类和运行元数据见 [H4 收口记录](../status/s_core3h4_closeout_20261007.md)。

## 面试中的 60 秒表达

> 我先做过一个自由度较高的 Multi-Agent/ReAct 原型，但它把动作判断、状态更新和外部事实混在一起，失败时很难知道是模型、状态还是规划器的问题。Resume V2 保留 Graph 做状态和 interrupt/resume，把 Planner、Provider、Verifier 做成确定性内核。S-CORE3 又把自然语言、顶部栏和反问统一到同一状态感知动作链：模型输出有限的 `TurnProposal`，Compiler 结合上下文生成唯一动作，所有约束更新进入 `RequestPatch → ConstraintEngine → PlanRequest`，修改进入 `PlanVersion/PlanDiff`。这样模型仍能理解开放表达，但不能越权创造事实，系统也能对每个失败点做回归和安全降级。

不要声称系统支持任意自然语言、长期记忆、MCP 工具调用或真实预订；这些仍是后续方向。真正值得展开的是：为什么动作协议要单一、为什么顶部栏不经过 Router、为什么 checkpoint 只保存流程状态、以及如何用 Frozen/Live 分层评测验证重构没有牺牲硬约束安全。

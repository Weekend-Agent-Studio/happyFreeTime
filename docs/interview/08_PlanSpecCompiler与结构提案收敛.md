# S-CORE1A/B/C：从结构混杂到单一 PlanSpec Compiler

_面试设计说明；现行结构契约以代码、测试和 S-CORE1C 正式评测为准。_

## 一句话

模型只提出一个有边界的 `PlanStructureProposal v3`；Harness 把软语义投影为 `PlanningIntent`，把结构编译为 concrete `PlanSpec`，再通过召回、搜索、调度、Provider 和 Verifier 判断具体行程是否可执行。模型不直接生成最终行程。

## 为什么要做三阶段收敛

| 切片 | 解决的问题 | 结果 |
| --- | --- | --- |
| S-CORE1A | Planner 内部同时存在 `PlanSkeleton`、`CompiledPlanSpec` 与转换函数 | 搜索、Beam、Scheduler、修改链路统一消费 `PlanSpec`；删除旧结构模型、注册表和强转适配器 |
| S-CORE1B | `PlanningIntent` 同时携带软语义和结构字段，旧/新 Proposal 与 checkpoint 兼容分支并存 | 实时 LLM 只输出 `PlanStructureProposal v3`；`PlanningIntent` 只保留 `pace + semantic_request`；开发 checkpoint 明确版本隔离 |
| S-CORE1C | Rule fallback 曾只换结构却复用 LLM 语义/检索结果；文档仍描述旧合同 | 完整 Rule 恢复、单一 Compiler 入口、C0–C4/B0/B3 最终回归和三阶段文档收口，结果见最终报告 |

## 模型究竟输出什么

模型每轮最多输出一种结构化 Wire DTO：

```text
PlanStructureProposal v3
├── slots: 1–4 个有序角色，core / optional
├── pace
├── grounded objectives
├── role-specific queries
└── evidence refs
```

它**没有另外输出 `PlanningIntent` 和 `PlanSpec` 两份结构**。Harness 用同一份 Proposal 派生两类内部结果：

```text
Proposal ──语义投影──> PlanningIntent(pace, semantic_request)
Proposal ──结构编译──> PlanSpec[]
```

`PlanningIntent` 不是模型 Wire DTO，而是规划服务使用的软语义投影；`PlanSpec` 也不是模型输出，它只包含已经确定的有序 StopRole 序列。optional slot 在编译时做有限展开，optional 不会泄漏到可执行结构。

## 三种结构来源如何汇合

```text
用户显式站数/角色 ─────────────┐
LLM PlanStructureProposal v3 ──┼──> PlanSpecCompiler ──> PlanSpec[]
Rule baseline ─────────────────┘
```

- 显式站数/角色在模型调用前由同一个 `PlanSpecCompiler` 预检，始终优先；非法结构在消耗模型和路线预算前返回冲突。
- LLM Proposal 经 Compiler 校验角色顺序、餐时顺序、重复餐类、显式结构保持、Optional 展开及 evidence 引用，生成有界 `PlanSpec[]`。
- Rule baseline 使用 Compiler 内部私有工厂直接生成 `PlanSpec`，没有独立 Skeleton Registry 或转换 Adapter。

Compiler 负责“结构是否合法、能否编译”，不负责“目录里有没有合适 POI、真实路线是否来得及”。这是避免把结构合法性和运行时可行性混成一个大校验器的关键。

## 编译之后的执行和恢复

```text
PlanSpec[]
   ↓
按角色召回候选（可用 Hybrid Retrieval）
   ↓
Beam 有界扩展 + 本地时间轴排序
   ↓
TimelineScheduler
   ↓
Route / Availability / Weather Provider
   ↓
PlanVerifier + 有界局部修复
   ↓
Verified Plans 或结构化冲突
```

如果已接受的 LLM 结构在本地排程或后续 Provider/Verifier 检查后仍无可行方案，Harness 最多做一次完整 Rule 恢复：

```text
首选 LLM 路径失败
   ↓
Rule PlanningIntent + 新的角色级召回/排序 + Rule PlanSpec
   ↓
同一个 Search / Schedule / Provider / Verifier Seam
```

这不是“只换一个骨架”：恢复路径也切回 Rule 语义和检索排序；也不是第二轮模型思考，恢复时不再调用 LLM，且首选路径会保留有界 Route/Availability 预算。Trace 会记录 Proposal、编译结果、fallback 阶段和失败原因。Structured Proposal 的格式错误仍可能有一次有界格式修复；它与“可执行性失败后的 Rule 恢复”是两种不同机制。

## Compiler 和 Verifier 的边界

| 层 | 回答的问题 | 典型职责 |
| --- | --- | --- |
| PlanSpecCompiler | “结构是否可编译？” | 角色、顺序、显式站数/角色、optional 展开、grounding、稳定 ID |
| Search / Scheduler | “当前候选能否形成时间上合理的组合？” | 角色候选池、Beam、餐时锚点、本地时间线与排序 |
| Provider / Verifier | “这个具体方案是否真实可行？” | 路线、营业、Availability、硬预算/距离/时间/返程等复核 |

结构可编译不等于 POI 存在；POI 候选存在不等于路线可行；分数高也不能覆盖 Verifier 的硬约束失败。

## 为什么不让 LLM 直接生成最终行程

最终行程需要引用 Catalog 中存在的资源，并依赖路线时长、逐站时间、营业、天气/可用性等事实。让模型直接输出站点和时间会把猜测与事实混在一起，也很难保证修改时只改变指定站点。当前分工让模型发挥在开放语义和软取舍上的优势，同时由可测试、可回放的确定性代码拥有事实、预算、状态和最终裁决权。

这不是把 LLM 缩成关键词分类器：Proposal 可以改变角色序列、重复活动、可选站点、软目标和角色级检索方向；区别在于每个提议都要被编译、搜索并验证。

## 代码入口与证据

- Proposal / 语义投影：[planning_intent.py](../../app/services/planning_intent.py)
- Wire 和领域模型：[planning.py](../../app/domain/planning.py)
- 唯一结构 Compiler：[plan_spec_compiler.py](../../app/services/plan_spec_compiler.py)
- PlanSpec：[plan_spec.py](../../app/services/plan_spec.py)
- 规划、fallback、Provider/Verifier Seam：[planning.py](../../app/services/planning.py)
- S-CORE1A/B/C 的阶段结果及正式指标：见 [S-CORE1C 最终评测记录](../status/s_core1c_release_20261002.md)。面试时使用该分支的 C/B 结果，不把旧冻结 tag 指标说成新分支指标。

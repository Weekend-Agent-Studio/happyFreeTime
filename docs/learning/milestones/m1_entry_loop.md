# M1 核心入口闭环学习复盘

_持续更新中的学习文档；主要链路已完成，学习与缺口复盘尚未完成 · 最后更新：2026-08-20_

---

> **当前状态：** 已学习约束来源、interrupt/resume、Planning、API/持久化、React 与测试评测六个切片，并完成 GET Session 恢复数据测试实验。下一步进行闭卷链路图和完整答辩。浏览器刷新恢复已确认是 M1 缺口，不能再把后端可恢复等同于端到端可恢复。

## 🎯 M1 解决的问题

用户用一句自然语言描述周末需求后，系统需要形成可检查的约束；缺少非关键条件时透明默认，关键条件无法确定时只问必要问题；条件足够时生成结构化候选或明确冲突，并通过 HTTP、SQLite 和 React 形成可恢复的纵向闭环。

M1 明确不包含真实天气、真实 POI、真实路线、可编辑约束和真实订单执行。

## 🔄 端到端链路

```mermaid
flowchart LR
    user([用户输入]) --> api[FastAPI 会话接口]

    subgraph graph ["LangGraph 入口闭环"]
        router[Router 语义抽取] --> enrichment[Enrichment 规范化]
        enrichment --> gate{Gate 需要反问?}
        gate -->|是| interrupt[保存状态并暂停]
        interrupt -->|用户回答| router
        gate -->|否| planning[Planning 生成候选]
    end

    api --> router
    interrupt --> response[统一响应]
    planning --> response
    response --> sqlite[(SQLite 记录)]
    response --> ui([React 工作台])

    classDef process fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a5f
    classDef decision fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    classDef data fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d

    class api,router,enrichment,interrupt,planning,response process
    class gate decision
    class sqlite data
```

### 代码入口

| 环节 | 主要文件 | 当前职责 |
| --- | --- | --- |
| 领域契约 | `app/domain/constraints.py`、`app/domain/planning.py` | 定义节点间强类型输入输出和不变量 |
| 语义入口 | `app/services/demo_router.py`、`router_extractor.py` | 离线规则或真实 LLM 生成同一种 `Interpretation` |
| 约束补全 | `app/services/enrichment.py` | 解析原始表达、补默认并保留来源 |
| 必要反问 | `app/services/question_gate.py` | 按确定性优先级决定 blocking question |
| Graph | `app/orchestration/entry_graph.py` | 路由、interrupt、checkpoint 和恢复 |
| 候选规划 | `app/services/planning.py` | 适配 V1 Planner，返回候选或结构化冲突 |
| HTTP 与持久化 | `app/api/application.py`、`app/persistence/` | 会话接口、消息、方案和 checkpoint |
| 前端 | `frontend/src/App.tsx` | 消费 question、plans、conflict 和约束摘要 |
| 评测 | `evals/`、`tests/` | 单元、集成与离线 smoke 验证 |

## 🧱 已学习的关键设计

### 约束来源不是同一层概念

| 来源 | 含义 | 示例 | 是否属于 Assumption |
| --- | --- | --- | --- |
| `user_explicit` | 用户直接明确表达 | “两个人”“人均 150” | 否 |
| `user_inferred` | 用户提供间接证据，系统作推断 | “约会”推断两人 | 否 |
| `default_rule` | 用户没有表达或暗示，系统使用产品默认 | 默认最近周末、默认距离 | 是 |
| `system_context` | 来自系统上下文 | 当前定位 | 否 |

`ConstraintValue` 保存规范化后的值和来源；`Assumption` 解释系统为什么补了默认值。用户推断虽然可能需要编辑，但不能伪装成系统默认。

### 用户没说与系统没懂必须区分

- 用户没说：非阻断字段可以使用透明默认并继续规划
- 用户说了但无法解析：保留缺失，交给 Gate 判断是否必须反问

如果把两者都直接默认，系统可能悄悄违背用户明确提出的要求。

### Gate 不由 LLM 自由决定

Router 只产出事实性的结构化解释；Gate 根据产品规则检查严格预算、显式但未解析的日期、地点、距离等字段。这样同一种输入的反问行为可重复、可单测，也能解释优先级。

### 回答恢复后重新经过 Router

用户的回答可能只是一个数字，也可能纠正时间、取消需求或开始新计划。Graph checkpoint 保存暂停状态；恢复时把原请求与补充回答组合，再经过 Router 和后续确定性节点，而不是假设回答一定等于某个字段值。

### Enrichment 与 Gate 共同决定是否可规划，但不是同一步

Enrichment 产出规范化约束和可见假设；Gate 再检查其中是否仍有阻断当前动作的缺失或歧义。因此，更准确的说法是“得到规范化且当前可用于规划的约束”，而不是假定 `NormalizedConstraints` 在任何情况下都包含所有字段。

### 当前 Planning 数据流

```text
NormalizedConstraints
  -> 转为旧 Planner 接受的字典
  -> 从 Mock Catalog 召回活动与餐厅
  -> 组合双站点候选并计算时间、费用与评分
  -> 对完整候选执行有限的可行性复检
  -> 把旧字典方案转回 V2 Plan
  -> CandidateSet(plans 或 conflict)
```

V2/V1 adapter 的本质是**数据契约兼容层**：输入侧把强类型 `NormalizedConstraints` 转成旧 Planner 的字典，输出侧把旧方案字典转成 V2 `Plan`。它不表示“V1 由 LLM 直接规划、V2 改为代码规划”，也不等同于新旧多 Agent 架构的分界；当前被复用的旧 Planner 本身就是确定性代码。

当前最终复检只覆盖双站点、结束时间、严格预算和总评分大于零。它能拦住部分组合级问题，但还不能证明候选在真实世界可执行。

### 来源与强度是两条正交轴

| 维度 | 回答的问题 | 示例 |
| --- | --- | --- |
| `source` | 这个值从哪里来 | 用户明确说“最晚 18:00 到家”；系统默认 `14:00–20:00` |
| `strength` | 规划是否允许违反 | “必须 18:00 到家”是硬约束；默认结束时间通常是软偏好 |

当前约束已经记录 `source`，但还没有统一记录独立的 `strength`。因此当前实现会把某些时间上限一律当作硬过滤，无法完整表达“用户明确的截止时间”和“系统默认的期望时段”之间的差别。

### 可行性术语不能混用

| 术语 | 作用范围 | 含义 |
| --- | --- | --- |
| `violation` | 单个候选 | 某个硬约束不满足，因此该候选被淘汰 |
| `conflict` | 整个候选集 | 所有候选都因 violation 被淘汰，系统需要解释无解和放宽方向 |
| `tradeoff` | 可行候选 | 仍然可行，但牺牲了某个软偏好，例如预算略高于偏好值 |
| `warning` | 可行候选或系统能力 | 数据不确定、能力降级或执行风险；它本身不使方案不可行 |

餐厅到达时已经打烊属于 `violation`，无需再参与偏好评分；只有所有候选都因此或因其他硬约束淘汰时，才汇总为 `conflict`。

### M2 目标链路与 LLM 边界

Catalog 召回不只是匹配自然语言关键词。合理的输入包括活动/用餐等结构需求、地点与半径、人数和儿童年龄、日期与可用性等硬条件，以及轻松、甜品、少赶路等偏好标签。以后可以引入语义检索或让 LLM 做查询扩展，但预算、营业、库存和时间线等硬校验仍应由结构化数据与确定性规则负责。

M2 的目标可以概括为：Provider 提供天气、POI、路线与库存事实；Catalog 先召回并做单资源剪枝；Planner 组合候选；Verifier 使用实际到达时间、完整预算、路线与库存做整计划复检；最后才对可行候选评分排序。LLM 可以辅助模糊偏好理解和把结构化结果翻译成自然语言，但不能覆盖或修改硬约束判断。

当前值得保留的缺口如下：

- 没有按实际到达时间后验验证营业状态
- 没有真实库存、天气和路线事实
- 时间约束有来源，但没有统一的硬/软强度
- “下午、晚上吃饭、必须几点结束”等组合时段语义尚不完整
- `CandidateSet` 在语义上应为 plans 与 conflict 互斥，但当前类型还未强制该不变量

### 业务持久化与 checkpoint 保存不同事实

| 数据 | 当前保存位置 | 回答的问题 |
| --- | --- | --- |
| 用户、会话、消息、最新方案 | 业务表 | 用户拥有什么、前端可以查询什么 |
| Graph 状态、暂停位置、interrupt | checkpoint 表 | 工作流运行到哪里、如何继续 |
| 规范化约束与假设 | 主要在 checkpoint | 当前运行使用了什么；尚未形成稳定业务快照 |

两类表当前可以位于同一个 SQLite 文件，但不能互相替代。`GET session` 只组装业务消息和最新方案，不会自动读取 checkpoint 并恢复约束条、待回答问题或 Graph 内部状态。

### Session、Planning Run 与消息不是同一粒度

`Session` 表示一段长期规划对话；一条逻辑提交应创建一个 `PlanningRun`；一个 Run 可以关联输入消息、约束快照、结果和错误。当前业务模型没有持久化 `run_id`，因此无法可靠表达消息、方案版本和失败属于哪一轮。

客户端生成的 `request_id` 用于标识一次逻辑提交。浏览器超时后的重试应复用原 ID，服务端才能返回原 Run 或结果，而不是重复插入消息、调用模型并生成方案。服务端仍可生成独立的内部 `run_id`。

### Session Status 与 Graph 位置不能混用

| 状态 | 所属层 | 示例用途 |
| --- | --- | --- |
| `session_status=needs_input` | 业务层 | 恢复页面、显示待补充会话、构建历史列表 |
| `graph_pending_node=ask_question` | 执行层 | `Command(resume)` 从正确节点继续 |

checkpoint 中的 `interrupts` 已经携带待回答字段、问题和严重级别，因此 M1 不必再发明重复的“中断类型”字段。业务层仍需要稳定状态和 `SessionView`，避免前端理解节点名、checkpoint 版本或序列化结构。

### 已验证的刷新恢复缺口

实际浏览器复现表明，方案生成后 URL 仍为 `/`，刷新会清空消息、约束和方案。与此同时，SQLite 中对应业务记录仍存在，后端 `GET session` 能读回消息和方案。根因是前端只用 React 内存保存 `sessionId`，没有 URL 定位、历史列表、启动恢复或 `getSession()` 调用。

这意味着：后端“知道 session ID 时可从 checkpoint 恢复”已经具备，但路线图要求的“浏览器刷新后继续被 interrupt 的会话”尚未端到端完成。完整历史会话中心可以后置，恢复当前活跃会话不能再标记为已完成。

### 数据库失败会造成跨存储不一致

当前用户消息、Graph checkpoint、方案替换和助手回复不是同一个事务。若 Graph 完成后 `replace_plans()` 失败，checkpoint 可能已经包含新候选，而业务表仍保留旧方案或没有方案，助手回复也不会写入。再次提交还可能产生重复消息，因为当前没有 `request_id` 幂等保护。

### React 内存状态不等于会话状态

| 前端状态 | 当前含义 | 刷新策略 |
| --- | --- | --- |
| `sessionId` | 当前会话定位 | 放入 URL 或通过最近会话恢复 |
| `messages` | 页面消息数组 | 从业务消息表重新读取 |
| `response` | 最近一次聚合响应 | 不原样持久化，由 SessionView 重建页面所需数据 |
| `input` | 未发送草稿 | 可选保存到浏览器本地 |
| `loading`、`error`、`rightTab` | 短暂交互状态 | 通常刷新后重置 |
| `selectedPlanId` | 当前查看的候选 | 可重置；不等于正式选择 |

正式选择方案是业务动作，应该形成稳定的 `Selected Plan` 并写入后端；当前点击方案卡只改变详情面板。`sessionId` 本身也不能恢复一切，它只是查询钥匙，后端必须实际保存并返回消息、约束、问题、方案和选择状态。

### AgentResponse 应是互斥状态机

当前 `status="completed"` 同时承载普通回复、方案和冲突，而多个字段可以独立存在。若后端错误地同时返回 `plans` 与 `conflict`，现有 React 条件渲染可能把两者同时展示。

目标契约应使用可判别的互斥结果：`needs_input` 必须有 question，`planned` 必须有非空 plans，`conflict` 必须有冲突对象，普通 `replied` 必须有回复。后端在 Pydantic 边界拒绝非法组合，前端 TypeScript 再用同一 `status` 穷尽分支，而不是决定优先信任哪个字段。

### 更新状态属于 Planning Run，不属于每个旧 Plan

用户修改条件时，可以保留上一版方案并显示“正在根据新条件更新”。这个 `updating` 状态描述当前 Planning Run；无需给每个旧候选分别写一份更新状态。

新 Run 成功后，新 Plan Version 成为 current，旧版本可标记为 superseded；新 Run 失败后，旧版本仍可展示，但必须标明“更新失败，当前仍是上一版本”，并允许使用同一 `request_id` 重试。仅用颜色或标签而没有底层状态数据，刷新后无法还原，也不利于无障碍展示。

### 测试证据必须说明边界

`python -m unittest discover -s tests -v` 从 `tests` 目录发现默认匹配 `test*.py` 的模块，并运行可发现的 `TestCase.test*` 方法。2026-08-19 实测后端 **30/30** 通过，前端 `pnpm run build` 也通过。

这些证据的边界如下：

| 结论 | 当前证据 |
| --- | --- |
| 严格预算缺金额会反问 | Gate、Graph 和 API 测试均覆盖 |
| Mock 条件下生成结构化方案或冲突 | Planning、API 和 smoke 覆盖 |
| `user_id` 会话隔离 | Repository 与 API 测试覆盖 |
| 真实 LLM 抽取准确率 | 未覆盖；Fake 输出只验证结构化契约、重试和降级 |
| 真实商家营业与推荐质量 | 未覆盖；当前使用 Mock Catalog，且无质量指标 |
| 页面刷新恢复 | 已实现并完成一次手工浏览器回归；仍无仓库内可重复浏览器 E2E |
| 前端类型检查和生产打包 | `pnpm run build` 覆盖，但不验证交互 |

9 条 smoke case 位于 `evals/smoke_cases.json`。运行器直接构造 `Interpretation`，只经过 Enrichment、Gate 和 Planning，绕过 Router、Graph、HTTP、SQLite 与 React。因此准确名称是“离线行为 smoke”，不是端到端测试。它检查结果类型、反问字段、冲突代码与指定硬约束字段；其中 6 条标记为硬约束路径并汇总通过率，不评估真实抽取和主观推荐质量。

真实 Router 应通过人工标注数据集评测 schema 合法率、意图准确率、字段 Precision/Recall/F1、关键字段错误率、稳定性、延迟与成本。推荐质量还需单独评价硬约束满足率、无解识别、排序相关性和用户反馈。

刷新恢复跨越 URL、React 启动、API、业务表和浏览器刷新。React 组件测试适合作为快速回归，API 测试验证 `SessionView`，但最终必须有一条 Playwright 浏览器主路径才能证明完整用户旅程。详细基础与面试问答见 [`../../interview/04_测试与评测基础.md`](../../interview/04_测试与评测基础.md)。

## 🧪 已完成的参与证据

| 证据 | 用户参与的判断 | 自动验证 |
| --- | --- | --- |
| `d12d5d7` | “约会”人数属于 `user_inferred`，不能进入 `Assumption`；“朋友”不能直接等于两人 | Router、Enrichment、API 与前端构建 |
| `b27817a` | 推断字段必须同时有值、证据和 confidence | Pydantic 跨字段失败测试与全量回归 |
| Gate 场景推演 | 严格预算缺金额必须反问 | `tests/test_question_gate.py` |
| interrupt/resume 复述 | 回答需要结合原请求重新抽取 | `tests/test_entry_graph.py`、`tests/test_api.py` |
| API/持久化推演 | 区分业务表、checkpoint、SessionView、请求幂等和跨事务失败 | 浏览器刷新复现、SQLite 只读检查与 `GET session` 验证 |
| React 状态推演 | 区分查看与正式选择、临时与可恢复状态，并要求后端拒绝 plans/conflict 非法组合 | `App.tsx` 状态流与刷新行为对照 |
| 测试证据推演 | 区分测试层次，识别 Fake Router、离线 smoke、前端 build 和刷新 E2E 的证明边界 | 2026-08-19 后端 30/30、前端 build 通过 |
| GET Session 测试实验 | 先预测消息顺序、方案标识和重复读取边界，再通过公开 HTTP seam 固化恢复行为 | `tests/test_api.py::test_get_session_restores_stable_conversation_and_plan_snapshot`；2026-08-20 全量 30/30 |

## 🎤 当前面试表达草稿

### 30 秒版本

M1 把自然语言入口拆成 LLM 语义抽取和确定性业务决策两层。Router 只生成带证据的结构化解释，Enrichment 负责默认与来源，Gate 决定必要反问，LangGraph 管理 interrupt 和 checkpoint，Planner 返回候选或结构化冲突。这样模型负责模糊语言理解，规则负责可测试的业务约束。

### 深挖时要能回答

- 为什么不让 Router 顺便补齐默认值
- 为什么 confidence 不是可靠概率，只是推断元数据
- 为什么“和朋友出去”不能推断准确人数
- 为什么 Gate 是普通 Service，而不是另一个 Agent
- checkpoint 保存什么，SQLite 业务数据又保存什么
- 为什么恢复后重新抽取，而不是直接把回答写入待补字段
- 为什么 `request_id` 由客户端生成，而 `run_id` 可以由服务端生成
- 为什么 Session Status 不能直接使用 Graph 节点名
- Graph 完成而方案落库失败时，系统会产生什么不一致
- 为什么 response 互斥首先是后端契约责任，前端仍需要判别联合
- 为什么 `updating` 属于 Planning Run，而 `superseded` 属于 Plan Version
- 为什么当前查看的方案不等于用户正式选择的方案

## 📖 相关基础知识

| 主题 | 与项目的连接 | 当前掌握 |
| --- | --- | --- |
| Pydantic 结构化输出 | 定义 Router 输出并拒绝不自洽元数据 | `L2` |
| `model_validator` | 检查推断值、证据和置信度的跨字段不变量 | `L2` |
| LangGraph State | 在节点间携带解释、约束、问题和候选 | `L2` |
| interrupt 与 checkpoint | 暂停并恢复有状态工作流 | `L2` |
| 确定性节点与 Agent | 根据决策自主性分配 LLM 和普通代码 | `L2` |
| Adapter | 在 V2 强类型契约与旧 Planner 字典之间双向转换 | `L2` |
| 硬约束与评分 | 区分可行性、偏好排序、tradeoff 与 conflict | `L2` |
| API 状态机与持久化 | 区分业务状态、执行状态、SessionView、事务与幂等 | `L2` |
| React 状态机 | 区分瞬时 UI 状态、可恢复会话状态和互斥响应 | `L2` |
| 测试与评测 | 区分单元、集成、API、组件、E2E、smoke 与 LLM eval，并完成 API 恢复数据实验 | `L3` |

## ✅ M1 学习完成标准

- [ ] 不看代码画出完整请求和反问恢复链路
- [ ] 独立解释 Raw、Normalized、Assumption 和 provenance
- [ ] 修改一条 Enrichment 或 Gate 测试并预测结果
- [ ] 解释 Planner 的生成、评分、复检和冲突
- [ ] 从一个 HTTP 请求追踪到 checkpoint、消息和方案记录
- [ ] 解释前端如何处理 question、plans 和 conflict
- [ ] 说明离线 smoke 能证明什么、不能证明什么
- [ ] 完成一次 2 分钟项目讲解和至少三轮追问
- [ ] 按最终理解重写本页的面试讲解

## 📍 下一学习切片

闭卷画出 M1 全链路，并进行 2 分钟项目讲解和变体追问；根据答辩暴露的薄弱点再选择下一个 L3 小实验。

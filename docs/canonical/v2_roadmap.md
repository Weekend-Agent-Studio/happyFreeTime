# HappyFreeTime V2 开发路线图

> 目标：以 5 个核心里程碑和 2 个桥接切片完成可信规划、产品体验、受约束智能、可控记忆、可修改、可执行、可观测、可评测的闭环 | 状态：当前路线图基线；实际完成情况以根目录 `README.md` 为准 | 更新：2026-09-04

---

## 1. 路线图原则

每个里程碑都必须形成可运行的纵向切片，而不是只完成某一层的大量基础设施。自动测试与功能同步开发；完整评测方案可以后置，但评测数据结构、运行轨迹和 smoke cases 从 M1 开始。

产品主线固定为“可信、可修改、会记住一家人的周末管家”。M2.5 与 M3.5 是有明确验收标准的桥接切片，不改变 M1-M5 的核心编号：前者避免可信规划长期停留在工程控制台形态，后者让记忆与语义检索在执行闭环前进入真实规划，而不是到 M5 才临时补画像或 RAG。

2026-09-04 起，M3 不再只等同于“替换一个站点”，而是按三段推进：先建立组合式对话命令和有界上下文，再完成定向修改，最后接入真正影响检索与结构的受约束语义决策。M5 从“首次实现智能规划”改为“产品化、消融评测与可选协议适配”，避免直到项目末期才验证 LLM 是否有业务价值。

优先级定义：

- P0：没有它就无法形成可信主链路。
- P1：让主链路具备产品闭环和面试竞争力。
- P2：规模扩大或公开部署后再增加。

```mermaid
flowchart LR
    accTitle: HappyFreeTime V2 里程碑顺序
    accDescr: 项目从核心入口和可信规划开始，依次完成产品呈现、对话控制与修改、可控记忆与语义检索、执行闭环，最后产品化和评测。

    m1[M1 核心入口] --> m2[M2 可信规划]
    m2 --> m25[M2.5 产品呈现]
    m25 --> m3[M3 可修改体验]
    m3 --> m35[M3.5 记忆与 Hybrid RAG]
    m35 --> m4[M4 执行闭环]
    m4 --> m5[M5 产品化与评测]
```

## 2. 开发前置动作

预计 0.5-1 天，不单独算里程碑：

- 冻结 V1 行为并记录 5-8 个当前基线用例。
- 新建 `app/`、`frontend/`、`evals/` 目录骨架。
- 确定 Python、Node、SQLite 和高德配置方式。
- 增加 `graph_version`、`provider_mode`、`llm_enabled` 功能开关。
- 建立统一的格式化、静态检查和测试命令。

完成后，旧链路仍能运行，新功能只进入 V2。

## 3. M1 核心入口

**目标：** 用户输入一句自然语言后，系统通过 Router -> Enrichment -> Gate 生成一组结构化约束，并能给出一个本地估算的双站计划或一个必要反问。

建议周期：3-5 个有效开发日。

**实现检查点（2026-08-28）：** 核心入口、SQLite checkpoint、业务 Session View、最近会话侧栏和 URL 刷新恢复已经闭环。消息接口已引入客户端 Request ID 与持久化 Planning Run；Plan 实例 ID 和组合指纹已分离，消除了跨会话相同方案组合的主键冲突。公开部署前仍需把固定 Demo 用户升级为匿名 Cookie 身份，并补可重复执行的浏览器 E2E 文件。

### 3.1 范围

**Domain**

- 定义 `ActorContext`、`Interpretation`、`RawConstraints`、`NormalizedConstraints`。
- 定义 `ConstraintValue`、`Assumption`、`QuestionDecision`。
- 定义最小 `Stop`、`RouteLeg`、`Plan`、`AgentEvent`。
- 定义领域错误和 ResponseEnvelope。

**Orchestration**

- 实现 V2 MainGraph 的 Router、Enrichment、Gate、interrupt/resume。
- Router 使用结构化输出，只保留一次必要 LLM。
- Gate 使用代码规则，每轮只问一个问题。

**Planning**

- 支持 2-4 小时的活动 + 餐饮双站骨架。
- 复用现有 JSON fixture 和本地 Haversine 路线估算。
- 输出 1-3 个结构化方案，不要求高德和完整多样化。

**API / UI**

- 建立 FastAPI 会话和消息端点。
- 建立最小 React 工作区：聊天区、约束摘要、方案列表。
- 通过 SSE 或最小事件流展示“理解需求、补全约束、生成方案”。

**Persistence / Eval**

- SQLite 保存 users、sessions、messages、planning runs、plans 和 trace events。
- 使用 SQLite checkpointer，验证 interrupt 后可恢复。
- 建立 5-8 个 `EvalCase` smoke cases 与 JSON/Markdown 报告骨架。

### 3.2 验收标准

- 一次普通规划请求最多 1 次 LLM 调用即可进入规划。
- 缺普通预算或距离时不反问，并在方案中展示默认假设。
- 严格预算无金额、执行无选择等阻塞情况会正确反问。
- 相对日期和“下午”“别太远”由 Enrichment 规则解析。
- 刷新或重启后，可以恢复完整 Session View，并继续一个被 interrupt 的会话。
- 最近非空会话可以在左栏切换；刷新和浏览器前进/后退保持 URL 与活跃会话一致。
- 相同 Request ID 的重试不重复写消息或规划，相同方案组合可以合法出现在不同会话。
- 前端能够完成“输入 -> 约束 -> 方案/反问”的完整交互。
- smoke eval 可以在无高德、无网络模式稳定运行。

### 3.3 必须测试

- Router schema validation、失败重试和安全澄清。
- Enrichment 的日期、时间、距离、默认值和来源优先级。
- Gate 的 blocking/non-blocking 参数化测试。
- Graph interrupt/resume 集成测试。
- API 用户与会话隔离测试。
- Request ID 重试、内容冲突、跨会话相同方案组合和 Session View 恢复测试。
- 一个 Playwright 主路径截图测试。

### 3.4 明确不做

- 一日多站、高德路线、地图、订单执行、长期记忆。
- 为所有旧 Service 做彻底重构。

## 4. M2 可信规划

**目标：** 从“能给方案”提升到“方案在时间、距离、天气、营业和预算上可信，并能在地图与时间线上验证”。

建议周期：6-9 个有效开发日。

**实现检查点（2026-08-30）：** M2 已完成可离线验收。四个显式骨架覆盖 2/3/4 站，多站每角色最多 8 个候选后完整枚举；D4 包含餐时锚点、返程真实终点/截止校验、独立全程距离、24 Route Leg 公平预算与未解析截止时间的阻断澄清；E 的未知价格不参与 `low_cost`，六种策略均有行为回归；F 已有模板 Presenter、同一 RouteLeg 索引的地图/时间线互相定位、Vitest/RTL 和离线 Playwright 桌面/375px 主路径。Geocoding 通过 Mock/Replay/高德/降级 Provider 归一化显式地点；Availability 只对 finalist 做有预算批量复核，verified unavailable 为 violation，unknown/stale/degraded 为 warning；最终 CandidateSet/API 只聚合仍被返回方案的 warning。内部 Repair Contract 将 availability、营业、单段路线和可归因返程失败统一为 `LOCAL_REPLACEMENT / NEXT_FINALIST / TERMINAL`：每条 root finalist chain 最多两轮，预算显式传入，耗尽只关闭该链，不能阻塞独立 finalist；天气仍保持可靠的组合前剪枝。19 条声明式离线 smoke 中 13 条硬约束路径均通过（100%），其中动态地点、可用性与路线场景经真实 Enrichment → Provider → Planner/Verifier 链路运行。HTTP/SQLite 离线 E2E、组件与两条 Playwright 路径也存在。M2.5 UI 和 M3.5 记忆现在可按路线图顺序进入，但不得倒改 M2 的可信边界。详细证据见 `docs/status/m2_trustworthy_planning_plan_2026-08-20.md`。

### 4.1 范围

**数据与 Provider**

- 使用可重建 OSM 快照形成自包含北京 POI 基线，并为后续审核数据保留替换 seam。
- 增加来源、采集时间、验证状态和动态 Mock 字段。
- 实现高德 Geocoding、Weather、Route Provider；浏览器地图通过独立 WebMap 配置/安全代理和前端 Adapter 消费 `RouteLeg.geometry`，不重复请求路线。
- 实现缓存和 `live/record/replay/mock` 模式。
- 实现高德 -> 缓存 -> 本地估算的降级链。

**规划引擎**

- 统一通用 `StopCandidate`、`Stop` 和 `RouteLeg`。
- 定义 `PlanningIntent`、`StopRole` 和 `PlanSkeleton`；M2 先提供确定性构造与版本化显式骨架，LLM Adapter 不作为可信基线的前置条件。
- 支持短时、半日、一日的 2/3/4 站骨架；时长只是资格与先验信号，不硬映射唯一站数。
- 根据已判定为 hard 的站数/角色/先后关系、时间锚点、最低停留与路线下界先硬筛骨架，再按需求覆盖、节奏、时间利用、缓冲和资源可得性计算结构分，并保留多个骨架进入组合。
- 先用通用有界序列组合器完整枚举最多 4 站的空间；每类召回、单资源硬剪枝、部分行程估分、完整方案重评分和完整 Verifier 分层实现。
- 动态选择 `balanced`、`low_cost`、`low_travel`、`experience`、`family_safe`、`weather_safe`。
- 从全部可行候选中生成最多 3 个具备实际差异的方案，不机械复制总分最高的近似组合。
- 只对 finalist 路段调用高德；统一 Repair Contract 在既有 finalist 池内按每条 root chain 最多局部重规划 2 次，并始终受 Route Leg / Availability 调用预算限制。
- 无解时返回结构化冲突与放宽选项。

**Presenter / UI**

- 模板 Presenter 必须可用，可选 LLM 只能基于证据润色。
- 前端增加桌面 3 方案对比、行程时间线、地图和路线来源标识。
- 降级路线必须显示“估算”及原因。

### 4.2 验收标准

- 支持 2、3、4 核心停靠点的合法时间线。
- 同一可用时长可以保留不同站数或不同顺序的骨架；强度为 hard 的站数、角色和先后关系不会被结构分覆盖，其他用户表达按 Planning Preference 排序。
- 骨架结构分、单站角色分、部分行程启发分和完整方案分职责清楚；真实路线返回后必须重新计算完整方案分，长行程不会因简单累加单站分天然胜出。
- 方案硬约束通过率在 smoke eval 中达到预设门槛，建议先以 95% 为目标。
- 三方案不会只是标题不同，至少在策略、成本、路程、类别或 POI 上存在显著差异。
- 高德不可用时仍能完成规划，且结果明确标记降级来源。
- 雨天、儿童年龄、营业时间、预算和距离冲突可被 verifier 拦截。
- 无可行方案时不会静默放宽用户硬约束。

### 4.3 必须测试

- Provider contract、缓存 TTL、失败短缓存和 replay 测试。
- 骨架资格、结构评分、显式顺序、跨站数比较和候选资源不足测试。
- 通用组合器的硬约束剪枝、部分行程未来可完成性和边界时间测试。
- finalist 路线复核后的时间线重建测试。
- 多样性与重复方案测试。
- 无解及放宽建议测试。
- 地图与时间线联动的 Playwright 桌面/移动截图测试。
- Geocoding、Availability、Route/营业局部 repair 的离线声明式 smoke，以及失败链耗尽后独立 finalist 继续验证的行为测试。

### 4.4 里程碑演示

推荐演示“北京雨天亲子半日”：输入含孩子年龄、饮食偏好和距离要求；系统展示 3 个差异方案、高德路线、天气适配证据，并在关闭高德后完成可见降级。

## 4.5 M2.5 产品呈现桥接切片

**目标：** 不改变 Planner 可行性语义，把已经可信的结构化结果呈现为“家庭管家在帮我安排”，而不是调试控制台或约束报表。

**实现检查点（2026-08-31，Demo World V1）：** 作品演示默认使用 `DemoCatalog`，仍保持 `Catalog.recall(NormalizedConstraints) -> CatalogResult` seam 和可切回的 `SnapshotCatalog`。它以 200 条 OSM 名称/类别/坐标锚点叠加版本化、固定种子的商业模拟世界；模拟价格、营业、亲子和天气适配真实进入 Catalog 剪枝、Planner 评分和 Verifier，而图片、评分、评论、画廊和文案经独立 `PoiPresentation` 按 `resource_id` 进入 API/SQLite 会话快照。这里的可信含义是约束、路线、状态与修复可解释可复现，不把模拟商业数据伪装为实时商户事实。默认 UI 使用一次克制的数据模式提示；已有 Wikimedia 图片保留归属，无图为明确的类别示意图。

建议周期：2-4 个有效开发日；必须在 M2 验收后进入，不与餐时、返程、多样化和 M2 eval 并行抢占主线。

### 4.5.1 范围

- 中栏改为管家式对话；模板 Presenter 根据已存在的事实、评分、warning、tradeoff 和 assumption 先给结论与关键取舍。
- 约束摘要采用渐进披露：默认只展示关键假设、阻塞风险和可修改项，来源、规则和置信度进入详情抽屉。
- 重做方案卡信息层级：主题、总时长、全程距离、预算、风险和差异优先，完整证据次级展开。
- 增加 `PoiPresentation` 展示模型和 POI 详情抽屉；展示标签、营业摘要、演示评分/评论、图片、预约/排队风险和高德导航入口，但不把 UI 字段塞回 `StopCandidate`。`Demo World V1` 已落地；真实库存、支付和商户履约仍不在本切片。
- Route Leg 可以点击展开，地图 marker、路线段和时间线双向选中；前端复用 Planner 已消费的 `RouteLeg.geometry`。
- 加入阶段化等待动效与错误/空/降级状态；只展示可公开阶段，不展示 chain-of-thought。
- 左侧历史会话继续有界展示，下半部分只预留记忆入口，本切片不提前实现长期记忆。

### 4.5.2 验收标准

- 用户不展开详情也能在 10 秒内理解三个方案的主要差异、推荐理由和最大风险。
- 约束来源和 Provider 证据仍可查，但不占据主对话首屏的主要视觉注意力。
- 点击 POI、Route Leg、地图 marker 或时间线会定位同一结构化对象，不产生第二套路线事实。
- 无图片、未知营业、路线降级和少于三个方案均有诚实且可操作的展示。
- 无 LLM 时模板 Presenter 和全部主交互仍完整可用。

### 4.5.3 必须测试

- Presenter 结构快照和未知/降级事实断言。
- 约束折叠、POI 抽屉、方案切换和地图时间线联动测试。
- 桌面与 375px 移动端 Playwright 主路径、加载、错误和空状态截图。

## 5. M3 对话控制、受约束语义规划与可修改体验

**目标：** 用户可以用开放自然语言创建、查询、解释和定向修改方案；LLM 的结构化决策能够实质改变检索方向、计划结构或可行方案排序，同时所有事实、硬约束、预算、终止和副作用继续受 Harness 控制。

建议周期：8-12 个有效开发日，按 M3-A、M3-B、M3-C 三个可独立回归的纵向切片推进，不一次性替换 M2 主链路。

### 5.1 范围

**M3-A：对话控制与上下文基础**

- 定义 `ConversationCommand`、`TargetReference`、`ConstraintPatch`、`QuestionSpec`、`SessionSnapshot`、`DecisionContext`、`InformationNeed` 和 `AgentDecision`。
- 将 `RouterExtractor` 渐进演进为单一 `TurnInterpreter`；迁移期使用 Adapter 兼容旧 `Interpretation`，不串联两次重复语义抽取。
- 建立代码拥有的 `StateMerger` 与 `CapabilityRegistry`；按 `operation + subject + target + patch` 路由到 Inquiry、Planning、Explanation 或后续 Execution 能力。
- 增加 `ContextAssembler`，按节点用途只注入最新输入、相关会话摘要、当前约束/Plan Version/锁定、授权 MemoryContext 和已获得观察；记录来源、版本、截断和 token 预算。
- 把 `departure_at`、可用时间窗、`duration_minutes` 和 `return_by` 分开建模；支持“14:30 精确出发”，冲突时间锚点进入 Gate。
- 先实现无副作用的查询与解释最小闭环，例如查目标时段天气、解释首选理由；界面结构化操作直接产生命令，不经过 LLM。
- `QuestionGate` 按 Capability Contract 判断阻塞字段，`QuestionComposer` 只可优化文案和合法选项排序。

**M3-B：定向修改与版本差异**

- 定义 `PlanDiff`、`LockedStop` 和不可变 Plan Version 关系。
- 支持“第二站换近一点”“晚饭换川菜”“其他不变”，将自然语言和约束面板统一成同一 `ConstraintPatch`。
- 支持锁定停靠点、替换一个停靠点、改变时间/预算/距离后局部重规划；只重新获取和验证受影响路线、时间线、预算和 Availability。
- 展示“原方案 vs 新方案”的停靠点、时间、价格、路线、warning 和执行影响差异；支持撤销尚未执行的最近修改。
- 无法唯一解析“这个/第二个/晚饭”时反问，不让模型凭文本猜测资源 ID。

**M3-C：真正影响方案的语义决策层**

- 为 `PlanningIntent` 提供 `RuleBasedPlanningIntent` 与可选 `LlmPlanningIntent` Adapter；模型输出角色覆盖、先后偏好、站数范围、节奏、主题和语义查询，代码确定 Constraint Strength。
- 将四个显式骨架演进为有限规划语法/角色状态机，支持 1-4 站、必选/可选角色、先后关系和 `FINISH`；“只安排晚饭”不再被迫进入双站结构。
- 建立 `CandidateRetriever` Interface 和现有规则 Adapter，先证明模型生成的语义查询能改变候选池或软排序；Hybrid RAG Adapter 在 M3.5 接入同一 Seam。
- 建立 `ToolBroker`、能力白名单与预算，只开放有选择价值的只读能力，例如 `search_poi_semantic`、`get_poi_evidence` 和候选体验比较；天气、地理编码、路线、营业与 Availability 仍由代码调度。
- 仅在模糊语义、召回置信不足或比较任务中启用最多 2-3 轮观察循环；模型可以提议请求能力、询问或 `FINISH`，Harness 验证完成合同并执行实际终止。
- 在少量已通过 Verifier 的候选上按门槛启用 `PlanCritic`；它只影响可行候选顺序，不能复活违规方案。语义需求不强或 deterministic 分差明显时跳过。
- 所有 LLM 节点均有 schema、一次格式修复、确定性 fallback、功能开关和 trace；不恢复全流程 ReAct。

### 5.2 验收标准

- `ConversationCommand` 能用有限枚举和组合参数覆盖创建、查询、解释、比较和修改，不为每种句式增加 Graph 分支。
- “下午两点半出发”以分钟级进入时间线；“只安排一家晚饭”能生成合法单站方案。
- “带父母出去玩，喜欢有新鲜感但别太累”能产生可追溯的 PlanningIntent 和语义查询，并在规则基线之外改变候选或排序。
- “保留餐厅，活动换近一点”只改变必要站点；LockedStop 保持不变，PlanDiff 准确，新方案重新通过完整 Verifier。
- ContextAssembler 不依赖全量聊天文本恢复状态；刷新后 Plan Version、锁定和待回答问题可从结构化 SessionSnapshot 恢复。
- 必需 Provider 不经过模型循环；非法工具、越权 scope、写能力和超预算请求 100% 被 Harness 拦截。
- 模型关闭、超时、输出非法或 Tool Loop 预算耗尽时，规则路径仍能规划、解释和返回结构化降级。
- 至少一组离线消融证明 LLM PlanningIntent 相对规则基线提高语义偏好命中；hard constraint pass 不下降，P95 延迟和 token 在预设预算内。
- UI 可撤销尚未执行的最近一次修改，所有命令、模型决定、工具观察、停止原因和 Plan Version 均可回放。

### 5.3 必须测试

- ConversationCommand schema、operation/subject 路由、旧 Interpretation Adapter 和 unsupported capability 测试。
- ContextAssembler 的 purpose 裁剪、token 预算、来源、跨用户隔离、删除后不再注入和长对话摘要回归。
- `departure_at`、可用窗口、duration、return_by 的组合与冲突表测试。
- Patch 合并、引用解析、来源优先级、锁定冲突、局部无解和 PlanDiff 测试。
- PlanningIntent 的证据一致性、1/2/3/4 站语法、单餐请求和确定性 fallback 测试。
- ToolBroker 白名单、参数 schema、只读/写隔离、超时、缓存、最大轮数、无进展和停止合同测试。
- PlanCritic 只接收 verified finalist、不能改变 Verifier 结论和失败回退测试。
- 多轮“创建 -> 解释 -> 修改 -> 刷新恢复”HTTP/SQLite 与桌面/移动 Playwright E2E。
- 规则基线 vs LLM PlanningIntent 的离线对照评测，记录质量、P50/P95、调用数和 token。

## 5.5 M3.5 可控记忆与 Hybrid RAG 桥接切片

**目标：** 当用户没有明确想法时，系统能在用户授权范围内利用本人和家庭的稳定偏好，并通过有证据的语义检索理解“新鲜感、适合父母、安静”等体验需求；每条记忆和检索证据都可追溯、可纠正、可删除或替换。

建议周期：5-8 个有效开发日。首版不要求独立向量数据库，可使用版本化本地 embedding index；不用自由文本画像替代结构化记忆，也不把 Fixture 评论伪装成实时商户评论。

### 5.5.1 范围

**Domain / Persistence**

- 定义 `PartyProfile`、`MemoryItem`、`MemoryCandidate`、`MemoryDecision`、`MemoryContext` 和 `MemoryInfluence`。
- 记忆区分本人、伴侣、孩子、家庭和临时同行组；保存 scope、kind、polarity、confidence、source/evidence、validity、sensitivity 和 status。
- 建立 `memory_subjects`、`memories`、`memory_events` 或等价可审计表；普通删除立即不再参与召回，审计记录按隐私策略脱敏。

**Memory Module**

- 建立小接口 `recall / propose / decide`；SQLite 和 In-memory 是同一 seam 的两个 Adapter。
- 当前用户明确要求 > 会话确认 > 安全/业务规则 > 长期记忆 > 默认值；冲突记忆标记 disputed，不覆盖当前输入。
- 一次行为只形成低置信度候选；“以后记住”、重复确认或多次一致反馈才能晋升稳定记忆。
- 过敏、儿童安全等敏感硬约束必须明确主体和确认；普通偏好默认只影响软排序。
- Planner 只消费有界 `MemoryContext`，输出 `MemoryInfluence[]`，不直接读取存储或依赖向量库结构。

**Hybrid RAG / Retrieval**

- 定义 `PoiSemanticProfile`、`ReviewAspectSummary`、`RetrievalQuery`、`RetrievedCandidate` 和 evidence reference；评论方面至少覆盖安静度、拥挤、步行负担、适合聊天、亲子与服务稳定性。
- 以明确标注的版本化 Fixture 评论/摘要补齐 Demo World 语义资料；保存生成规则、支持片段、置信度和更新时间，不冒充真实大众点评数据。
- 在 M3 的 `CandidateRetriever` Seam 下增加 `HybridRagRetriever`：先执行结构化 Hard Constraint 过滤，再做关键词 + 向量召回，可选 LLM rerank 只处理 Top-K 摘要和 evidence id。
- 将 `search_poi_semantic`、`get_poi_evidence`、`retrieve_memory` 接入同一个只读 ToolBroker；MemoryService 仍负责 subject/scope/权限过滤，模型不能直接查询记忆表或向量索引。
- RAG 结果只影响候选召回与软排序；路线、天气、营业、库存、严格预算和 Verifier 继续使用结构化 Provider 与规则。
- 对规则标签、Hybrid RAG、Hybrid RAG + rerank 做可回放消融，记录 Recall@K、nDCG、偏好命中、错误证据率、延迟和 token。

**Feedback / UI**

- 记录完成、跳过、替换、评分和原因；LLM 可以提出结构化记忆候选，但不能自行提交敏感或长期硬约束。
- 左栏下半部分提供记忆摘要入口；完整页面支持查看、纠正、删除、仅本次使用和“为什么影响本方案”。
- 方案解释明确区分当前需求、历史记忆和系统默认，避免黑箱画像。

### 5.5.2 验收标准

- 同一句“周末随便安排下”在存在相关家庭记忆时产生可解释的软排序差异，无记忆时仍能正常规划。
- 用户说“这次不要按以前的来”时，当前输入覆盖记忆且不会被后台再次覆盖。
- 一次跳过某地点不会自动形成“永远不喜欢”硬规则；明确删除后后续规划不再使用。
- 伴侣或孩子的偏好只在对应同行范围生效；跨用户、跨家庭不可读取或影响规划。
- 每条被 Planner 使用的记忆都能追溯到 `MemoryInfluence`，Presenter 不声称未实际发生的影响。
- “适合父母、不要太累但有新鲜感”等模糊请求在固定评测集上相对标签基线有可复现的 Top-K 或成对偏好提升，并能指出实际使用的摘要证据。
- 删除或替换 POI 语义资料、评论 Fixture 或记忆后，下一次检索不再返回已删除证据；向量索引可由版本化源数据重建。
- RAG/embedding/rerank 全部关闭或失败时，RuleBasedRetriever 和结构化记忆仍能完成规划，Hard Constraint 结果保持一致。

### 5.5.3 必须测试

- scope、主体、优先级、过期、冲突、删除和敏感记忆确认测试。
- SQLite/In-memory Adapter 的 Memory Module 接口契约测试。
- 同一请求有/无记忆的 counterfactual eval，以及错误记忆影响率。
- 当前用户覆盖长期记忆、跨用户隔离和删除后不再召回测试。
- 从反馈生成候选、用户确认/拒绝到下一次规划的 Playwright E2E。
- CandidateRetriever 接口契约、结构化先过滤、Top-K 稳定性、evidence citation、索引重建和删除传播测试。
- 标签基线 vs Hybrid RAG vs 可选 rerank 的离线检索与端到端方案对照评测。

## 6. M4 执行闭环

**目标：** 用户选择方案后，可以安全预览和确认模拟交易；系统对重复请求、部分失败、取消和补偿有明确行为。

建议周期：6-9 个有效开发日。

### 6.1 范围

**Domain / Persistence**

- 定义 `ExecutionAction`、`ExecutionPlan`、`ConfirmationSnapshot`。
- 建立 `orders`、`order_events` 和必要库存表。
- 实现订单状态机和 append-only 事件。

**ExecutionSubgraph**

- 从已选方案确定性生成执行动作。
- 全量预检、执行预览、一次确认和高风险二次确认。
- 校验 snapshot hash，变化后强制重新确认。
- 使用幂等键执行核心票、餐厅和定时 Mock 打车。
- 实现部分成功后的自动补偿、损失确认和人工关注状态。
- 支持结构化取消目标和已执行方案变更 Saga。

**UI**

- 增加执行预览、确认对话框、订单 Tab、取消与失败处置。
- 清楚标识“模拟下单”，展示价格、时间、取消政策和每步状态。

### 6.2 验收标准

- 未选择方案不能进入执行。
- 相同确认请求重复提交不会重复创建订单或扣减库存。
- 价格、库存或动作发生变化后旧确认失效。
- 订单可以跨进程重启恢复和查询。
- 故意制造第二步失败时，系统能按策略补偿第一步或暂停请用户决定。
- 取消成功、退款失败和人工关注都有可追踪状态。
- LLM 在任何路径上都不能直接获得写操作能力。

### 6.3 必须测试

- 状态转移表测试和非法转移测试。
- 幂等并发测试。
- checkpoint 恢复后不重复执行测试。
- 部分成功、补偿成功、补偿失败故障注入测试。
- 用户隔离和越权访问测试。
- 从选择方案到订单完成的 Playwright E2E。

### 6.4 里程碑演示

推荐演示两个分支：正常完成订票与订座；餐厅预订失败后自动取消无损活动票，或展示有损取消并等待用户决定。

## 7. M5 产品化与评测

**目标：** 把完整主链路整理成可稳定运行、可量化说明、可录制演示和可选择部署的简历项目。

建议周期：5-8 个有效开发日。

### 7.1 范围

**产品化**

- 匿名身份 Cookie、多会话隔离、会话归档和删除。
- 加固 M3.5 记忆的导出、保留期、敏感字段脱敏和跨设备身份合并策略。
- 响应式前端、加载/错误/空状态和无障碍检查。
- 运行轨迹抽屉、Provider 来源、降级和订单审计展示。
- Docker、本地一键启动、环境模板、种子数据和运维说明。
- 无 LLM 演示模式和稳定录屏脚本。

**受约束智能层加固**

- 固化 M3 的 ConversationCommand、DecisionContext、PlanningIntent、ToolBroker、PlanCritic 与停止合同版本；完成 Prompt/模型/规则兼容和回放迁移策略。
- 以消融结果调整 LLM 激活门槛：简单明确请求优先规则路径，只有模糊语义、低召回置信或候选难分时增加模型调用。
- Presenter 基于结构化分数、事实、warning、tradeoff、assumption 和 MemoryInfluence 解释站数、顺序与地点选择；模板路径始终完整可用。
- LLM 可以从反馈中提出 `MemoryCandidate`，但是否提交、scope、敏感性和 Constraint Strength 仍由确定性规则与用户确认决定。
- 只有组合量、延迟或调用成本指标证明穷举成为瓶颈后，才把内部组合器替换为 Beam Search；外部 Provider 按 Route Leg 预算限制，禁止无界搜索。
- 可选增加 MCP Client Adapter 接入一个有业务价值的只读能力，或 MCP Server Adapter 暴露 HappyFreeTime 只读检索；两者必须复用 Capability Contract、Actor scope、ToolBroker 预算和 trace，不为展示协议强行接入。

**评测**

- 扩充到 20-30 个核心 cases，并为关键 case 增加语言变体。
- 指标：task success、hard constraint pass、invalid question、extraction F1、`pass^3`、degraded completion、compensation success、latency、LLM calls、token。
- 完成 V1/V2 至少一次可复现对比。
- 使用 record/replay 固定外部响应。
- 生成版本化 JSON 与 Markdown 报告。
- 增加 Provider 超时、库存变化、补偿失败等故障注入。
- 增加上下文泄露、越权 Capability、无进展 Tool Loop、非法 FINISH 和 RAG 错误证据故障注入。

**部署决策**

- 先完成 Docker 本地验证和录制演示。
- 只有在健康检查、配额保护、隐私脱敏和重启恢复通过后，才短租服务器公开部署。
- 简历可优先提供 GitHub、架构图、演示视频和评测报告；在线链接是加分项，不是完成项目的前提。

### 7.2 验收标准

- 新用户无需注册即可获得隔离会话，跨用户不可读写资源。
- 关键偏好只有在明确保存或重复确认后进入长期记忆。
- 记忆影响可追溯，删除、过期、冲突和当前用户覆盖在完整回归中成立。
- 桌面和移动主路径无明显溢出、遮挡和不可操作状态。
- 评测可一条命令运行并生成报告，结果包含代码、Prompt、规则和数据集版本。
- V2 在主要成功率、硬约束、无效反问和 LLM 调用数上相对 V1 有可解释提升。
- LLM PlanningIntent/PlanCritic 与 Hybrid RAG 相对确定性基线在语义偏好匹配上有可复现实验收益；关闭或调用失败时结果仍可规划、可验证、可解释。
- LLM 影响的每个候选顺序都能追溯模型、Prompt、输入证据和结构化输出，且不会改变 Verifier 结论。
- ToolBroker 的允许/拒绝、观察、循环预算与终止原因可回放；模型无法通过 MCP 或内部 Tool 绕过 Actor scope、确认和写操作边界。
- README 能让面试官在数分钟内理解问题、架构、取舍、量化结果和演示入口。

## 8. 评测布局

完整评测细节可以在 M2-M5 逐步调整，但现在必须固定三件事：数据结构、轨迹字段、结果导向断言。

### 8.1 EvalCase 最小结构

```text
case_id
user_turns
initial_context
memory_fixture
provider_fixture
active_plan_fixture
expected_command
expected_target_and_patch
expected_information_needs
allowed_capabilities
forbidden_capabilities
required_constraints
forbidden_assumptions
hard_invariants
allowed_relaxations
expected_execution_effects
expected_memory_effects
forbidden_memory_effects
tags
```

### 8.2 核心 smoke 场景

1. 亲子雨天半日，孩子年龄明确。
2. 朋友晚间聚会，普通预算缺失可默认。
3. 情侣一日安排，包含咖啡和晚餐。
4. 严格预算但无金额，必须反问。
5. 用户只说“带小朋友”，遇到年龄限制资源才反问。
6. 指定户外活动但天气不适合，返回冲突或室内替代。
7. 已有方案后“餐厅保留，活动换近一点”。
8. 执行第二步失败，验证幂等与补偿。
9. 模糊家庭周末请求命中已确认记忆，只改变软排序并公开影响证据。
10. 当前输入与旧记忆冲突，当前输入获胜；删除后旧记忆不再影响规划。
11. “14:30 出发，18:00 前到家”保留两个精确时间锚点，返程恰好截止可通过、超时一分钟淘汰。
12. “只安排一家晚饭”生成单站方案，不隐式增加活动。
13. “带父母、想有新鲜感但不要太累”触发语义召回，证据来自允许的 POI 摘要，路线与步行硬边界仍由代码验证。
14. 已有方案后“第二站换近一点，其他不变”，正确解析引用、锁定其他站点并产生 PlanDiff。
15. 模型反复请求同一只读 Tool 或请求写能力，Harness 因无进展/权限停止且规则路径可继续。

### 8.3 不采用精确答案评测

同一需求可能有多个合理方案，因此主要断言应是：

- 所有硬约束满足。
- 事实来自 Catalog 或 Provider。
- 时间线无重叠且路线时间已计入。
- 总预算、时长和距离计算正确。
- 反问只针对阻塞字段。
- 模型只调用 Capability Contract 允许的工具，并在预算内终止。
- LLM 影响可以追溯到 PlanningIntent、retrieval evidence 或 PlanCritic，关闭模型时存在可用基线。
- 执行前有有效确认，副作用符合幂等和状态机。
- 记忆只在正确主体和 scope 生效，当前表达覆盖历史，影响可追溯且可撤销。

POI 名称精确匹配只用于 Provider fixture 或特定回归，不作为通用成功条件。

## 9. 全局完成定义

每项功能只有同时满足以下条件才算完成：

- 领域契约明确，API 和 Graph 不传自由结构。
- 正常路径、关键错误路径和降级路径都有测试。
- 状态可持久化，重试和恢复不会制造重复副作用。
- UI 有加载、空、错误、确认和移动端状态。
- 运行轨迹包含来源、版本、延迟和公开错误信息。
- 任何长期记忆写入都有证据、主体、scope、置信度和用户可撤销路径。
- 相关 smoke eval 能运行，报告没有无法解释的退化。
- 文档、配置示例和启动命令同步更新。

## 10. 时间紧张时的截断策略

为了秋招竞争力，建议优先保证 M1-M4 的完整闭环，同时保留 M2.5 的产品可读性、M3 的最小受约束智能和 M3.5 的最小可控记忆；再做 M5 中最能形成证据的部分。

| 优先级 | 必须保留 | 可以后置 |
| --- | --- | --- |
| P0 | TurnInterpreter/Enrichment/Gate、Pydantic、确定性规划与 Verifier、真实路线降级、前端主路径 | Beam Search |
| P1 | M2.5 产品呈现；M3 DecisionContext、精确时间、1-4 站规划语法、PlanningIntent Adapter、局部修改与规则 fallback | 独立 LLM StructureRanker |
| P1 | M3.5 结构化记忆、影响证据、查看/删除、最小 Hybrid RAG；M4 确认快照、幂等、Saga；基础消融评测 | 跨设备画像合并和大规模向量数据库 |
| P2 | PlanCritic 默认开启、轻登录、MCP Adapter、评测 UI、更多城市 | 微调或强化学习 |

一个真正有竞争力的最小终态是：能从开放自然语言生成可信的 1-4 站计划，模型能在 Harness 边界内影响语义检索、结构意图和软取舍；用户可局部修改并利用可控家庭记忆，系统接入真实路线并可降级，经过确认完成有状态 Mock 交易，在失败时补偿，并能用消融评测与 trace 证明“智能层有收益且不破坏硬约束”。这个闭环比堆叠更多 Agent 名称或训练方法更能体现工程能力。

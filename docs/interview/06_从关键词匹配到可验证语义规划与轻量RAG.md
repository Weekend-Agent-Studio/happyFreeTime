# 从关键词匹配到可验证语义规划：统一语义中间层与轻量 RAG

_HappyFreeTime 设计复盘与面试材料 · 基于 2026-09-09 代码审查和需求讨论 · 本文描述提案，不代表能力已经实现_

---

## 📋 一页结论

### 核心判断

HappyFreeTime 当前的问题不只是“LLM 权限太小”，而是 **LLM 理解出的开放语义没有一条稳定通路进入候选召回、排序、修改和解释**。语义会先被 `RawConstraints` 的有限字段承接，再经过 Enrichment 的关键词规则、`PlanningIntent` 的有限结构字段以及 Planner 的精确标签或子串匹配，逐层损失。

因此，下一步不应继续为“全天”“不累”“适合聊天”“少辣”等每种表达增加专用布尔字段，也不应让 LLM 直接生成并裁决完整方案。更合适的调整是增加一个统一、有限、可验证的语义中间层：

- 用结构化硬约束表示日期、明确时间、人数、预算、角色和地点等可验证条件
- 用有限的 `SoftObjective` 表示真正会改变下游策略的常见软目标
- 用开放文本 `SemanticQuery` 承接长尾体验表达，避免枚举无限增长
- 用 `EvidenceRef` 保留用户原话和 POI 资料的证据来源
- 用同一个 `CandidateRetriever` 服务创建方案和单站修改
- 用轻量 Hybrid RAG 提升候选相关性，但继续由 Provider、Planner 和 Verifier 保证事实与可行性

> 📌 **定性：** 这是一次内部职责收拢，不是再造 Main Graph。它把 [架构 V2](../canonical/architecture_v2.md) 和 [V2 Roadmap](../canonical/v2_roadmap.md) 中已经提出、但尚未落地完整的 `CandidateRetriever` 与语义规划 Seam 具体化。

### 证据口径

本文使用三种明确标签：

- **代码事实：** 当前仓库可以直接验证的实现
- **合理推断：** 由代码路径和已复现反例推导出的结果
- **个人建议：** 尚未实现、需要通过评测决定是否保留的方案

### 最重要的边界

这次调整不应放松以下机制：

- Pydantic Schema 与 `extra="forbid"`
- 日期、路线、天气、营业、Availability、预算等事实的 Provider 或确定性处理
- Planner 的合法角色语法、组合预算与确定性时间线
- Verifier 的硬约束裁决和有界 Repair
- Session、Plan Version、选中方案、锁定站点与幂等语义
- 模型不得猜测 `resource_id`、不得伪造证据、不得直接执行写操作

真正需要放宽的是 **模型表达语义的出口**，不是事实正确性与业务边界。

## 🔍 当前实现与问题证据

### 当前 POI 数据到底在哪里

**代码事实：** 当前 POI 主数据不在 SQLite 业务表中。`SnapshotCatalog` 从 `data/catalog/pois.json` 加载 OSM 锚点；Demo 模式再由 `DemoCatalog` 合并 `data/demo_world/v1/enrichment.json` 中的演示资料。SQLite 保存用户、会话、消息、Planning Run、Plan、Plan Version、Session Snapshot 等业务状态，`PlanRecord` 保存的是方案 Payload，而不是 POI 主数据。

相关代码：

- [Catalog 实现](../../app/services/catalog.py)
- [Demo World 适配](../../app/services/demo_world.py)
- [POI 领域模型](../../app/domain/catalog.py)
- [展示资料模型](../../app/domain/presentation.py)
- [持久化模型](../../app/persistence/models.py)

这意味着当前“召回”并不是数据库模糊查询，而是把 JSON 候选加载到内存后进行过滤和排序。

### 当前语义是如何逐层丢失的

```mermaid
flowchart LR
    accTitle: 当前语义损失链路
    accDescr: 用户开放表达经过受限抽取、关键词规范化和标签匹配后，只有少量语义能够真正影响最终候选方案

    user_input([用户开放表达]) --> turn_interpreter[TurnInterpreter 结构化抽取]
    turn_interpreter --> enrichment[Enrichment 规则规范化]
    enrichment --> hard_filter[Catalog 硬条件过滤]
    hard_filter --> planning_intent[PlanningIntent 选择结构]
    planning_intent --> tag_match[标签与子串匹配]
    tag_match --> planner[Planner 组合与排序]
    planner --> verified_plan([Verifier 后方案])

    classDef input_style fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#3b0764
    classDef process_style fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a5f
    classDef risk_style fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    class user_input,verified_plan input_style
    class turn_interpreter,enrichment,hard_filter,planning_intent,planner process_style
    class tag_match risk_style
```

| 环节 | 代码事实 | 造成的限制 |
| --- | --- | --- |
| **TurnInterpreter** | 已通过结构化输出生成 `Interpretation` 和 `ConversationCommand`，但 Prompt 明确要求模糊日期、时间、距离只保留文本，中文时钟主要交给 Enrichment | 模型即使理解“一整天”“三点二十出发”，也未必有被允许的稳定字段表达全部语义 |
| **Demo Router** | 离线 Demo 依赖有限词表和正则，适合作为可复现 Adapter | 如果运行时实际走 Demo Adapter，用户会误以为 LLM 已调用，长尾表达会直接漏掉 |
| **Enrichment** | 严格区分“未提及可默认”和“已提及但无法解析应澄清”；日期、时段、距离、中文时钟仍依赖有限规则 | 原则正确，但语义解析能力过窄；上游没保留 `time_text` 时，“一整天”可能被当成未指定而落入默认下午 |
| **PlanningIntent** | RuleBased/LLM Adapter 已能在受限角色、站数、顺序、`pace` 内改变结构，并可回退 | 当前还没有完整输出语义查询和可执行软目标；模型对具体 POI 召回的影响有限 |
| **Catalog** | 遍历内存候选，按预算、人数、儿童、基础营业、半径等条件过滤 | 这是合理的硬过滤，但不是语义召回，不理解“新鲜感”“慢慢聊天”等体验相似性 |
| **Planner** | `_matching_terms(...)` 主要依赖精确标签、单向子串和少量 Alias | POI 没有完全对应标签时，即使模型理解了需求，也无法让相关候选稳定上升 |
| **单站修改** | 状态锚点、锁定其余站点、重新验证和候选级 `PlanDiff` 已较完整；自由文本标准仍进入同类标签匹配 | “修改哪一站”可靠，但“应该换成什么”仍不够智能；创建与修改重复承担语义匹配问题 |
| **Evidence** | `PlanningIntent` 的净化逻辑只保留可回溯值，并把单个值写入固定的 `soft_preference` | 防幻觉方向正确，但“约会 + 新鲜感 + 不累”等多个需求可能在决策证据中被压缩 |
| **方案解释** | 当前主要由确定性摘要、Highlights 和 Diff 事实组成 | 能说明事实变化，但还不能系统回答“哪些需求被满足、为何推荐这个方案、做了什么取舍” |

### 代表性反例

#### “明天跟女朋友约会，一整天，不希望太累”

**合理推断：** 模型可能识别“约会”和“不累”，但如果“一整天”只以自由文本出现、又没有进入 Enrichment 可识别的时间结构，系统就可能使用默认 `14:00–18:00`。即便 `PlanningIntent` 选择 `relaxed`，当前标签匹配也不保证“情侣约会”“低体力负担”真正改变 POI 排名。

#### “活动保留，晚餐想吃不那么辣的”

**合理推断：** 目标识别和单站替换机制可以复用，但 Demo World 若缺少可靠的辣度资料，系统既无法证明“少辣”，也不能只凭 LLM 生成该事实。正确行为应是：把“少辣”作为软查询检索有证据的候选；没有证据时明确未知，而不是假装满足。

#### “带父母去有新鲜感但不累的地方”

**合理推断：** 这里同时存在开放语义、同行人群与路线负担。单纯扩充 Alias 会让相同表达散落在 Router、Enrichment、Planner 和修改链路；单纯向量检索又不能证明路线、步行或无障碍条件。它需要语义检索和结构化事实验证共同完成。

## 🎯 统一语义中间层

### 为什么不能把所有内容都塞进 `preferences`

“近一点”“少辣”“安静”都可以保留用户原话，但它们会触发不同的下游处理：

- “近一点”需要比较完整方案的路线距离，不能只看 POI 标签
- “少辣”需要餐厅角色和饮食资料，当前无证据时只能作为偏好或未知
- “安静”适合通过 POI 语义资料召回和排序
- “不累”可能同时影响节奏、站数、路线权重、场地特征和解释

若全部只是字符串列表，下游仍需再次用关键词猜测每个字符串代表什么，死板逻辑只是从 Enrichment 移到了 Planner。因此需要将 **有限且可执行的目标** 与 **开放的检索表达** 分开。

### 建议的数据契约

**个人建议：** 新增以下概念，但不要为每种自然语言增加字段。

```python
class EvidenceRef(BaseModel):
    evidence_id: str
    source_type: Literal["user_message", "poi_profile", "fixture_aspect", "provider"]
    source_field: str
    summary: str
    source_ref: str | None = None
    confidence: float


class SoftObjective(BaseModel):
    kind: Literal[
        "low_fatigue",
        "shorter_travel",
        "fewer_stops",
        "novelty",
        "quiet",
        "conversation_friendly",
        "romantic",
        "family_friendly",
        "low_spice",
    ]
    strength: Literal["preferred", "required"]
    target_role: StopRole | None = None
    evidence_refs: tuple[str, ...]


class SemanticQuery(BaseModel):
    query_id: str
    text: str
    target_role: StopRole | None = None
    evidence_refs: tuple[str, ...] = ()
```

`SoftObjective.kind` 应是有限集合，只有当某个目标需要不同的下游计算或业务策略时才增加。例如：

- `shorter_travel` 会启用路线距离比较
- `fewer_stops` 会影响合法结构评分
- `low_fatigue` 会提高轻松节奏、少站点、短通勤和低步行资料的权重
- `quiet` 与 `conversation_friendly` 主要影响语义召回和软排序

长尾表达不需要进入 `kind`。例如“适合慢慢逛、能偶尔坐下来、不用一直排队”可以原样进入 `SemanticQuery.text`，由检索器与 POI Semantic Profile 计算相关性。

> 📌 **关键区别：** `kind` 是有限的执行词汇；`SemanticQuery` 是开放的检索语言；`EvidenceRef` 负责证明它们来自哪里。这样字段不会随用户措辞无限膨胀。

### “全天”应该如何表示

“全天”不一定需要一个永久的 `is_all_day` 布尔字段。它更适合作为有限时间语义中的一个值，例如：

```python
class TimeScope(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    ALL_DAY = "all_day"
    EXPLICIT_RANGE = "explicit_range"
```

TurnInterpreter 可以把“一整天”“从早玩到晚”等表达归一为 `ALL_DAY`，并保留原文证据；Enrichment 不再重复匹配这些汉语表达，只负责按产品政策把 `ALL_DAY` 编译为一个透明、可追踪的时间窗，例如 `09:00–21:00`。如果产品尚未决定“全天”的默认边界，应在 UI 中显示假设或进入澄清，而不是静默使用下午默认值。

“下午三点二十准时出发”则应同时产生显式 `departure_at=15:20` 和对应证据。Harness 校验时钟、与可用窗口和 `return_by` 的关系；模型负责语言理解，不负责决定冲突如何消解。

### Enrichment 的新职责

Enrichment 不应消失，也不应继续成长为中文 NLP 词典。调整后的职责应是：

- 验证模型输出的结构和取值范围
- 合并用户显式值、会话状态、环境事实与透明默认值
- 把 `time_scope` 等有限语义编译成领域约束
- 标记来源、置信度、假设和未解决冲突
- 对“用户已提及但无法可靠结构化”的字段触发澄清

它不再需要重新读取“全天”“不累”“适合聊天”的原始字符串并逐个匹配。

## 🔄 建议的规划链路

### 目标数据流

```mermaid
flowchart LR
    accTitle: 可验证语义规划链路
    accDescr: 模型把开放语言映射为有限语义契约，代码完成政策编译和硬过滤，混合检索提升相关性，确定性规划与验证保证最终方案可执行

    user_turn([用户请求或修改]) --> interpret[TurnInterpreter]
    interpret --> semantic_contract[语义契约与证据]
    semantic_contract --> enrich[Enrichment 与政策编译]
    enrich --> hard_filter[Catalog 硬过滤]
    hard_filter --> retrieve[CandidateRetriever 混合召回]
    retrieve --> plan[确定性 Planner]
    plan --> verify[Provider 与 Verifier]
    verify --> advise[受约束推荐解释]
    advise --> response([方案或修改候选])

    classDef input_style fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#3b0764
    classDef model_style fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    classDef harness_style fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a5f
    classDef safe_style fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d
    class user_turn,response input_style
    class interpret,advise model_style
    class semantic_contract,enrich,hard_filter,retrieve,plan harness_style
    class verify safe_style
```

这条链路不要求新增 Graph 节点。`CandidateRetriever` 可以先作为 `PlanningService` 内部注入的深 Module；只有未来出现独立中断、人工确认或跨轮工具循环时，才有理由把某一步提升为 Graph 节点。

### 创建方案与修改方案如何复用

两条业务链的差别只在输入范围，不应各自维护一套语义匹配：

```python
class CandidateRetriever(Protocol):
    def retrieve(
        self,
        candidates: Sequence[StopCandidate],
        semantic_queries: Sequence[SemanticQuery],
        soft_objectives: Sequence[SoftObjective],
        target_role: StopRole,
        exclusions: set[str],
        limit: int,
    ) -> RetrievedCandidateSet: ...
```

创建方案时：

- Catalog 先按结构化硬约束过滤
- Planner 为每个角色调用同一个 Retriever
- Retriever 返回有证据和分数组成的 Top-K 候选
- Planner 组合、路线估算、Provider 复核、Verifier 裁决

单站修改时：

- 使用选中 Plan Version、目标站点和锁定站点作为状态锚点
- Catalog 仍先执行同样的硬过滤
- 只为目标角色调用同一个 Retriever
- 排除原 POI 和已锁定资源
- 复用路线、Availability、Verifier 和 PlanDiff 生成

这样既不会复制完整 Planning Graph，也不会让修改链路变成独立的“第二个 Planner”。

## 📚 轻量 Hybrid RAG 设计

### 这是不是向量数据库

`PoiSemanticProfile` 本身只是可检索的语义资料，不是向量数据库。把这些资料编码为向量并保存为本地索引，才构成向量检索能力；即使如此，200 个 POI 的规模也不需要部署专门的向量数据库。

**代码事实：** 当前约 200 个 POI 已有名称、类型、标签、基础地址、设施或展示描述等资料，但部分描述是通用 Demo 文案，区分度不足。动态路线、天气、营业和 Availability 不应被写进静态语义 Profile 当作事实。

### 建议的 POI Semantic Profile

```text
名称：中国地质博物馆
类型：博物馆、室内文化活动
适合场景：亲子探索、周末放松
设施：休息区、无障碍通道
体验摘要：室内参观为主，可按楼层自由控制时长；
相比大型户外景区，步行与天气负担较低。
```

建议 Profile 只包含有来源、相对稳定的信息：

- 名称、资源类型和分类标签
- 场景、体验、饮食和设施标签
- 有来源的策展描述或稳定 Aspect 摘要
- 证据 ID、来源类型、置信度和更新时间

不要把实时营业、路线时长、当前价格、库存或天气嵌入成可直接相信的事实；这些内容仍由结构化字段和 Provider 处理。

### 本地索引而非重型基础设施

**个人建议：** 第一版使用版本化文件即可：

```text
data/retrieval/
├── poi_profiles.json
├── poi_embeddings.npz
└── manifest.json
```

`manifest.json` 至少记录：

- Profile Schema 版本
- Embedding 模型与向量维度
- 源数据 Hash
- POI 数量
- 构建时间

运行时只需对查询生成向量，并对约 200 个 POI 做余弦相似度计算。这样更容易离线回放、重建、删除传播和单元测试，也避免为了简历标签引入 Qdrant、Milvus 或 `pgvector` 的运维复杂度。

### 混合召回流程

建议保留两个真实 Adapter：

- `RuleBasedRetriever`：封装当前标签、Alias 和确定性排序，作为默认基线与降级路径
- `HybridRagRetriever`：组合词法相关性、向量相似度与结构化元数据，并返回可追踪证据

第一版流程：

1. Catalog 用硬条件排除明确不可用候选
2. 对剩余候选计算标签或词法分
3. 对 `SemanticQuery` 与 `PoiSemanticProfile` 计算向量相似度
4. 用 Reciprocal Rank Fusion 或固定、可配置权重融合排名
5. 按 `target_role`、排除集和 Top-K 预算输出候选
6. Planner 继续做组合和粗路线估算
7. Provider 与 Verifier 复核 finalist

第一版不需要 LLM Reranker。只有离线评测证明 Hybrid 召回仍无法区分 Top-K，且增加一次模型调用有稳定收益时，再对少量摘要做有界 Rerank。

### 是否应该伪造评论

不建议批量伪造评论并包装成真实用户数据。可以选择：

1. 首版不使用评论，只使用现有标签、设施和人工整理的稳定简介
2. 为 30–50 个关键 POI 添加明确标注为 Fixture 的 `ReviewAspectSummary`

Fixture 可覆盖安静程度、排队、步行负担、聊天适配、亲子、父母友好、辣度和服务稳定性，但必须包含 `source_type="fixture"`、置信度、证据引用和更新时间。UI 与文档都不能把它称为实时点评或真实用户评论。

### 成本与价值

**合理推断：** 对当前规模，最小 Hybrid RAG 的实现复杂度为中等，主要成本不在向量计算，而在数据质量、证据契约、创建/修改统一接入和评测集。若不引入向量数据库、实时评论采集、LLM Rerank 和 Tool Loop，可控制在约 3–5 个有效开发日；完整商业化数据链则会扩大到一至数周，不适合 Resume Release V1。

它值得做的前提不是“简历需要 RAG”，而是下列反例在标签基线中确实失败，并且 Hybrid 方案在离线评测中稳定改善：

- “带父母、有新鲜感但不累”
- “适合约会、能慢慢聊天”
- “换一个类似但更安静的地方”
- “晚餐想少辣一点”

## ⚙️ 模型与 Harness 的职责边界

| 能力 | 模型可做 | Harness 必须做 |
| --- | --- | --- |
| **语言理解** | 识别表达变体、指代、软目标和语义查询 | Schema 校验、置信门槛、一次修复、澄清或 RuleBased 回退 |
| **时间语义** | 把“一整天”“三点二十准时出发”映射为有限类型或明确时钟 | 定义全天产品政策、校验日期与时钟、检测冲突、记录假设 |
| **规划意图** | 提议角色覆盖、站数范围、节奏、主题和软目标 | 约束强度、合法结构编译、组合预算和停止条件 |
| **检索** | 生成开放 `SemanticQuery`，解释语义相关性 | 硬过滤、索引版本、Top-K、去重、证据来源与回退 |
| **事实** | 根据已提供证据总结取舍 | 调用路线、天气、营业和 Availability Provider；模型不得补写未知事实 |
| **修改** | 解释用户想换哪站、偏好什么 | 校验 Session/Plan 锚点、锁定其他站、生成新版本并完整复验 |
| **推荐解释** | 在 Verified Plans 之间解释需求匹配和软取舍 | 校验 Plan ID 与 Evidence ID，数值由代码渲染，失败时模板降级 |

这仍然是受约束 Agent 设计：模型基于状态作出会改变检索、结构或已验证候选排序的决策，但不能突破事实、权限和硬约束边界。仅仅接入 RAG 并不会自动把系统变成 Agent；必须证明模型决策影响任务结果、能够观察状态、有有界失败路径，并通过评测优于关闭模型的基线。

## 📊 证据、解释与可观测性

### 为什么要从字典升级为 `EvidenceRef[]`

当前单值 Evidence Map 能阻止模型随意写入幻觉证据，但无法自然表示：

- 一个需求对应多个原文片段
- 一个目标由多个资料共同支持
- 一个候选同时满足或牺牲多个需求
- 创建、修改、检索和解释共享同一证据

建议的新链路是：

```text
用户原话
→ EvidenceRef[user_message]
→ SoftObjective / SemanticQuery 引用 evidence_id
→ Retriever 返回 POI evidence_id 与分数组成
→ Planner 保存贡献项
→ RecommendationAdvisor 只能引用现有 evidence_id
```

第一版可以把 `EvidenceRef[]` 保存在现有 Planning Run、Plan Response 或 Trace JSON 中，不必立即创建数据库迁移；旧 `evidence_map` 可暂时保留为兼容视图。

### 推荐解释的合理落点

建议后续增加一个有界 `RecommendationAdvisor`，但不要同时新增 `PlanCritic`、LLM Presenter 和另一个 Explanation Agent。它只接收 2–3 个已通过 Verifier 的方案、结构化需求与证据，输出：

- `understood_needs`
- `recommended_plan_id`
- `overall_reason`
- 每个方案的 `matched_needs`
- 每个方案的 `advantages`
- 每个方案的 `tradeoffs`
- `evidence_refs`
- `confidence`

Harness 必须验证 Plan ID、Evidence ID 和字段长度；路线缩短多少、费用差多少、营业状态等数值仍由代码从 Plan/PlanDiff 渲染。模型失败时回退到确定性模板。

## 🧪 评测与消融方案

### 分层数据集

建议建立 30–50 条人工标注案例，至少覆盖：

- 精确时间与全天语义
- 单站、两站和三至四站规划
- 情侣、父母、儿童和朋友场景
- 新鲜感、安静、聊天、低疲劳和少辣
- 单站修改、目标指代和锁定保持
- 无证据偏好与冲突约束

不要只测最终方案。应拆成三层：

| 层级 | 主要指标 | 回答的问题 |
| --- | --- | --- |
| **解释层** | 字段准确率、证据覆盖率、澄清准确率 | 模型是否理解了需求 |
| **检索层** | Recall@K、nDCG、偏好命中率、错误证据率 | 相关 POI 是否被召回并有证据 |
| **端到端层** | Hard Constraint Pass、人工成对偏好、修改目标正确率、锁定保持率 | 更好的检索是否真的产生更好的完整方案 |

所有实验同时记录：

- P50/P95 延迟
- 模型调用数与 Token
- Fallback 率
- 降级后任务完成率
- Provider 调用预算
- 硬约束不干扰率

### 最小消融矩阵

| 实验组 | TurnInterpreter | Retriever | 推荐解释 | 目的 |
| --- | --- | --- | --- | --- |
| **A 规则基线** | RuleBased/Demo | 标签与 Alias | 确定性模板 | 保留当前可用下限 |
| **B 语义入口** | LLM | 标签与 Alias | 确定性模板 | 判断理解提升是否会被下游吞掉 |
| **C 轻量 RAG** | RuleBased 或标注查询 | Hybrid | 确定性模板 | 单独证明检索收益 |
| **D 完整语义通路** | LLM | Hybrid | 受约束 Advisor | 评估整体用户价值与成本 |

若 C 相对 A 没有稳定改善 Recall@K、nDCG 或端到端人工偏好，`HybridRagRetriever` 不应默认开启。若 D 只改善文案而不改变需求命中或推荐一致性，就不能把它描述为规划智能提升。

## ✍️ 推荐实施顺序

### 切片 0：先建立运行真值

- 在 Trace 或开发 UI 显示实际使用的 TurnInterpreter Adapter、PlanningIntent Adapter、模型名、调用次数和 Fallback 原因
- 保证 Demo 启动、生产 LLM 模式和 Replay 模式不会被误认
- 不修改规划语义

**退出条件：** 一次真实演示可以证明是否调用了 LLM、在哪一步调用、为什么回退。

### 切片 1：修通语义入口

- 增加有限 `TimeScope`，支持 `ALL_DAY`
- 允许模型在明确时直接输出规范化 `departure_at`
- 保留原始文本和 `EvidenceRef`
- 坚持“已提及但未解析则澄清；真正缺失才默认”

**退出条件：** “一整天”和“三点二十准时出发”不依赖枚举所有中文句式，也不会静默回到下午默认。

### 切片 2：建立统一语义契约

- 增加 `SoftObjective[]`、`SemanticQuery[]` 和 `EvidenceRef[]`
- 让 LLM PlanningIntent 输出这些受限对象
- RuleBased Adapter 生成同一契约，保留可回放基线
- 只选 6–9 个有真实下游行为的初始 `kind`

**退出条件：** 创建和修改输入都能表达“不累、近一点、安静、少辣”，且不会为每个同义句新增字段。

### 切片 3：接入最小 CandidateRetriever

- 抽取 `RuleBasedRetriever`，保持当前结果等价
- 建立少量高质量 `PoiSemanticProfile`
- 增加本地 Embedding 索引和 `HybridRagRetriever`
- 创建方案与单站修改共同接入
- 不增加向量数据库、LLM Rerank、ToolBroker 或新 Graph

**退出条件：** RuleBased 可一键回退；Hybrid 在独立检索评测和至少一个端到端反例上优于标签基线，且硬约束不下降。

### 切片 4：增加受约束推荐解释

- 只解释 Verified Plans
- 推荐一个方案，同时解释每个候选的优势和取舍
- 所有事实引用 Plan 或 Evidence ID
- 模型失败回退确定性模板

**退出条件：** 解释逐条回应用户需求，不创造 POI、路线、价格或营业事实。

### 切片 5：评测收口与简历证据

- 固化 30–50 条案例与人工标注
- 跑 A/B/C/D 消融
- 报告语义命中、硬约束、延迟、Token 与 Fallback
- 只把真实完成和真实指标写入 README 与简历

**退出条件：** 能回答“为什么需要 LLM”“为什么需要 RAG”“收益是否值得成本”“失败如何降级”。

## ⚠️ 风险、删减与回退

| 风险 | 控制方式 | 回退路径 |
| --- | --- | --- |
| **字段继续膨胀** | `SoftObjective.kind` 只为不同下游行为扩展；长尾进入 `SemanticQuery` | 删除低价值 Kind，不影响查询文本 |
| **语义资料伪事实化** | Profile 记录来源；动态事实不向量化为真值；Fixture 明示 | 只保留现有可靠标签和描述 |
| **向量相似但业务错误** | Catalog 硬过滤在前，Verifier 在后 | 退回 `RuleBasedRetriever` |
| **LLM 调用增加延迟** | 明确门槛、一次调用预算、缓存和超时 | 简单请求直接走规则路径 |
| **重复层级** | Retriever 留在 Service；Advisor 合并 Critic/解释职责 | 关闭 Adapter，不改变 Main Graph |
| **解释幻觉** | 只允许引用 Plan ID 和 Evidence ID；数值代码渲染 | 确定性摘要与 PlanDiff |
| **评测过拟合 Demo World** | 划分开发集与独立保留集，记录数据版本 | 不默认开启未通过保留集的能力 |

本阶段应继续推迟：

- MCP
- 长期向量记忆
- 通用 Tool Loop
- 多 Agent 协商
- 大型向量数据库
- 实时评论抓取
- LLM Rerank
- 独立 `PlanCritic` 与独立 LLM Presenter 同时存在

## 🎓 面试叙事

### 60 秒版本

> 我先把原型中的自由 ReAct 规划拆成确定性 Provider、Planner、Verifier 和有界 Repair，解决路线、营业、预算与降级不可验证的问题。完成可信基线后，我用“全天约会但不累”“父母有新鲜感”“保留其他站只换一个更安静的地点”等反例复盘，发现新瓶颈不是模型调用少，而是模型理解出的开放语义被 Enrichment 关键词、固定 PlanningIntent 字段和标签匹配逐层丢失。我的调整不是重新让模型生成最终方案，而是增加有限 `SoftObjective`、开放 `SemanticQuery` 和可追溯 `EvidenceRef`，再通过可替换 `CandidateRetriever` 把创建与修改统一接到轻量 Hybrid RAG。结构化硬过滤仍在检索前，Provider 和 Verifier 仍在方案后。最后用规则、LLM Intent、Hybrid RAG 和完整语义通路做消融，只有语义质量提升且硬约束、延迟和成本达标才默认开启。

### 为什么这是结构演进而不是技术堆砌

这次调整由三个真实失败模式推动：

1. 相同用户语义需要在多个模块重复增加关键词
2. LLM 已理解的偏好无法改变具体候选召回
3. 创建与修改重复使用脆弱的标签匹配

新的 Seam 可以独立替换和删除：关闭 LLM 后 RuleBased 仍输出相同契约；关闭 RAG 后标签召回仍可工作；关闭推荐解释后确定性摘要仍存在。可回退性说明它隔离了变化，而不是把复杂度散到全系统。

### 面试高频追问

#### 为什么不让 LLM 直接生成方案？

因为本地生活方案不仅是语言生成，还涉及 POI 身份、路线、时间、营业、预算、可用性、版本修改和后续执行。LLM 适合理解开放语义与软取舍；确定性代码更适合保证组合合法、事实来源和硬约束。两者通过受限契约协作，比让单个模型同时生成和裁决更容易测试、回放与降级。

#### 为什么不是所有偏好都用一个字符串数组？

字符串适合保留开放表达，但不能告诉 Harness 应调用路线比较、减少站数还是只做语义检索。有限 `SoftObjective` 提供可执行语义，开放 `SemanticQuery` 处理长尾，两者并存才能避免字段和关键词同时膨胀。

#### 为什么 200 个 POI 也要 RAG？

RAG 的必要性由语义检索问题决定，不由数据量决定。200 个 POI 不需要向量数据库，但仍可通过本地 Embedding 索引改善“新鲜感、不累、适合聊天”等无法用精确标签覆盖的召回。是否保留必须由标签基线与 Hybrid 的离线对照决定。

#### RAG 会不会把错误资料带进方案？

RAG 只产生候选和软相关性，不证明路线、天气、营业、库存或严格预算。结构化硬过滤先执行，动态事实由 Provider 获取，最终方案仍需 Verifier 通过；解释只能引用带来源的 Evidence ID。

#### 这能称为 Agent 吗？

仅有 RAG 不能。完成后若模型能基于会话状态解释命令、形成会真实改变结构或召回的语义决策、在失败时进入有限修复或规则回退，并且整个过程有 Trace 与评测证据，可以诚实称为“受约束的多轮规划 Agent”。如果模型只生成查询或文案，仍应称为 LLM-enhanced workflow。

## 🔗 主会话交接清单

主会话在拆执行计划前需要确认以下决策：

1. `ALL_DAY` 的产品时间窗和是否允许用户确认假设
2. 首版 `SoftObjective.kind` 的最小集合及每个 Kind 的明确下游行为
3. `PoiSemanticProfile` 使用哪些现有字段，哪些 POI 需要人工补充可靠资料
4. 首版 Embedding 模型、缓存方式和离线可复现策略
5. Hybrid RAG 默认开启所需的质量、延迟和错误证据门槛
6. `RecommendationAdvisor` 是否替代未来独立 `PlanCritic + LLM Presenter`

建议主会话按“运行真值 → 语义入口 → 统一契约 → Retriever → 推荐解释 → 消融评测”的顺序生成任务卡。每个切片必须独立测试、可关闭、可回退，并明确非目标，避免再把 Resume Release V1 扩展成通用 Agent 平台。

## 📚 仓库证据索引

- [约束与 ConversationCommand](../../app/domain/constraints.py)
- [规划领域对象](../../app/domain/planning.py)
- [TurnInterpreter](../../app/services/router_extractor.py)
- [Enrichment](../../app/services/enrichment.py)
- [Catalog](../../app/services/catalog.py)
- [PlanningIntent Adapter](../../app/services/planning_intent.py)
- [PlanningService 与标签匹配](../../app/services/planning.py)
- [Entry Graph](../../app/orchestration/entry_graph.py)
- [架构 V2](../canonical/architecture_v2.md)
- [V2 Roadmap](../canonical/v2_roadmap.md)
- [Resume Release V1 Roadmap](../canonical/resume_release_v1_roadmap.md)
- [三阶段架构演进讲稿](05_三阶段架构演进_从ReAct原型到受约束规划Agent.md)

---

_本文是设计审查与面试叙事草案。所有“建议”“目标链路”和预计成本在实现与评测完成前，都不能作为仓库已具备能力或简历量化成果。_

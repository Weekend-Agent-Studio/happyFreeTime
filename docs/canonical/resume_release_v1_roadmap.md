# HappyFreeTime Resume Release V1 交付路线图

> 状态：秋招投递范围冻结基线  
> 基线：`codex/m2-s0-native-planning` / `78792c0`  
> 日期：2026-09-05  
> 关系：本文只收敛近期交付顺序；长期能力仍以 `v2_roadmap.md` 为目标，不代表其中 M3-M5 已实现。

> 2026-09-15 进展补充：S0-S4 的核心产品链路、轻量 Hybrid RAG、单站修改和受约束推荐解释已经实现，当前处于离线评测收口阶段。真实 Structured Output 差分诊断中，`deepseek-flash`（DeepSeek Provider）在 Full `Interpretation` 上取得 8/9 最终成功、P50/P95 约 1.69s/1.84s；样本仍小，只作为选择 Pilot 配置的工程证据，不作为简历效果指标。最终消融前不放宽 Harness 校验。

> **历史说明：** Resume V1 路线图已经由 planner-v2-eval-baseline 和 Resume V2 发布评测 supersede。本文保留 S0–S5、S4-E4–E8 和 Gate A/B 的演进过程，不作为当前施工清单。

## 1. 发布目标

Resume Release V1 只证明一条可演示、可回放、可评测的纵向链路：

```text
开放自然语言需求
  -> 受约束的结构与语义决策
  -> 候选召回、确定性组合、Provider 事实、Verifier / Repair
  -> 选择一个 Plan Version
  -> “保留餐厅，只把活动换近一点”
  -> 锁定未修改对象、局部重规划、完整复验、PlanDiff
  -> 模拟执行预览与确认
  -> 幂等 Mock 下单；失败时补偿或明确停止
  -> Trace 与离线评测证明收益、成本和降级行为
```

演示主案例：

> “明天下午两点半带父母出去玩，想有点新鲜感但别太累，六点前回家。”  
> “餐厅保留，只把活动换近一点。”  
> “就选这个，帮我下单。”

## 2. 两个发布门，不再共用一个完成终点

### Gate A：可投递版本

完成切片 S0-S4 即可打标签、录演示并开始集中投递。必须包含：可信生成、模型真实影响、定向修改、证据、Trace 和离线评测。

模拟下单不阻塞 Gate A。原因是 Agent 应用竞争力首先取决于模型决策是否有业务作用、Harness 是否能约束它，以及这些结论是否有评测证据。

### Gate B：产品闭环版本

在 Gate A 稳定后完成 S5。增加很薄但真实有状态的模拟执行：预览、确认快照、幂等、一次故障注入和补偿。它用于证明安全副作用工程，不扩展成完整订单平台。

建议现在就开始投递；Gate A 是近期开发终点，Gate B 是紧随其后的加分版本。

## 3. 职责冻结

### 模型拥有

- 将自然语言解释为有 Schema 的 `ConversationCommand`。
- 在允许的规划语法内给出 `PlanningIntent`：角色、站数范围、节奏、先后偏好、主题和语义查询。
- 在证据充足时解释软取舍；输出非法、超时或低置信时允许失败。

### Harness 拥有

- 用户显式约束的强度、默认值、合并优先级和冲突判定。
- Session、active/selected Plan Version、锁定对象与引用解析。
- Catalog 和 Provider 事实、硬过滤、时间线、预算与路线计算。
- Verifier 结论、Repair 预算、模型调用预算、停止条件和确定性回退。
- 所有写副作用、确认、幂等、权限和补偿。模型不得直接调用写能力。

### Module 与 Graph 边界

- Main Graph 只表达业务状态转换：解释、补全/反问、规划、修改、确认和执行。
- `PlanningService` 保持深 Module；召回、组合、Verifier 和 Repair 可以有内部 seam，但不全部提升为 Graph 节点。
- `TurnInterpreter` 只负责 operation、subject、target 和 patch；不再创建第二份规划语义模型。
- `PlanningIntent` 只描述计划结构、语义召回和软目标；不重复保存对话命令。
- `StateMerger` 是唯一状态合并者；Enrichment 只做规范化、默认值和外部事实补全。
- `ContextAssembler` 第一版是纯投影 Module，不是 Agent，也不自行读写状态。
- Resume Release V1 不新增通用 `ToolBroker`、`PlanCritic` 或第二套 Planning Graph。天气、路线、营业和 Availability 继续由代码调度。
- 只有出现第二个真实 Adapter 或评测证明现有 seam 无法替换时，才新增公开 Interface。

## 4. 纵向切片

### S0：M2.5 产品真值收口（P0，1-2 天）

目标：当前 UI 不再表达代码没有证明的事实。

- 修正 `total_price` 是全体地点费用合计却显示“/人”的问题，并明确是否包含交通费用。
- 数据声明不再固定声称路线来自高德；按实际 Route Leg 来源和降级状态展示。
- 没有返程 Route Leg 时不显示“返”节点。
- 后端尚不支持保留对象修改时，不展示“餐厅保留，活动换近一点”等虚假能力入口。
- 增加前端行为测试和至少一个 HTTP 契约断言；不重构 Planner。

退出条件：默认 Mock、Replay 和 Live 模式下，界面价格、来源、返程与能力提示都和响应事实一致。

### S1：可修改所需的领域地基（P0，2-3 天）

目标：先让状态和用户语言有可靠落点。

- 分开建模 `departure_at`、可用时间窗、`duration_minutes` 和 `return_by`。
- 支持有限的单站活动、午饭和晚饭结构，不再强制活动 + 餐饮。
- 持久化 active/selected Plan Version、Plan Version 关系和最小 `SessionSnapshot`。
- 定义最小 `ConversationCommand`、`TargetReference`、`ConstraintPatch` 和 `LockedStop`。
- 首版命令只承诺 `CREATE`、`SELECT`、`KEEP`、`REPLACE`；其余明确返回 unsupported 或反问。

退出案例：

- “14:30 出发，18:00 前到家”保留两个独立时间锚点。
- “只安排一家晚饭”“只安排一顿午饭”“只安排一个活动”均产生一个对应角色的停靠点。
- 刷新后仍能恢复 selected Plan Version。

### S2：受约束生成（P1，2-4 天）

目标：只增加一个真正影响方案的模型决策点。

- 为 `PlanningIntent` 提供 RuleBased 与 LLM 两个 Adapter，保持同一 Interface。
- LLM 只能输出有限站点语法、角色、节奏、主题、语义查询和软目标；Harness 校验并允许一次格式修复。
- LLM 关闭、超时、非法或低置信时自动回退 RuleBased Adapter。
- 第一版复用当前 Catalog/标签和 Planner，不平行建设完整 Hybrid RAG 管线。
- “有新鲜感但不累”必须能改变结构、候选或软排序，并留下输入证据与决策 Trace。
- “如果下雨就改室内”先实现为显式领域策略，不引入通用条件 AST 或自由 Tool Loop。

退出条件：至少一个固定案例中，LLM PlanningIntent 相比规则基线产生可解释的方案差异，同时硬约束通过率不下降。

### S3：最小定向修改（P1，2-4 天）

目标：完整打通 KEEP + REPLACE，不提前实现通用编辑器。

- 将“餐厅保留，只把活动换近一点”解析为目标、锁定和 Patch。
- 无法唯一定位“这个/第二个/晚饭”时反问，模型不得猜 resource ID。
- 只重新召回和组合受影响对象；锁定对象必须保持 identity 不变。
- 重新计算相关路线、时间、预算和 Availability，并让完整 Verifier 复验。
- 保存新的不可变 Plan Version，返回结构化 `PlanDiff`。
- 不在本切片实现增加、删除、任意重排、多步撤销或执行后改订。

退出条件：HTTP/SQLite E2E 覆盖“创建 -> 选择 -> 修改 -> 刷新恢复”；失败路径不会静默解除锁定或放宽硬约束。

### S4：证据、评测与投递包装（P0，2-3 天）

目标：到这里形成 Gate A。

- 建立 30-50 条独立样本，覆盖精确时间、单站、父母/儿童、模糊体验、天气策略、定向修改和模型失败。
- 对照 RuleBased PlanningIntent、LLM PlanningIntent，以及语义能力开启/关闭。
- 记录结构选择正确率、硬约束通过率、语义偏好命中率、修改目标正确率、锁定保持率、降级完成率、P50/P95 延迟、模型调用数、token 和失败回退率。
- Trace 至少记录代码/Prompt/数据版本、模型结构化决定、Provider 来源、Verifier/Repair、fallback 与停止原因。
- 产出版本化 JSON/Markdown 报告、2-3 分钟演示视频脚本和简历可核验表述。

最低门槛：

- 硬约束通过率不低于当前确定性基线。
- KEEP/REPLACE 目标和锁定保持在核心用例中 100% 正确。
- LLM 对模糊语义的提升可复现；若没有提升，默认关闭该调用点并继续使用规则路径。
- 每种模型失败都能降级完成或返回诚实的结构化失败。

### S4-E4：时间真值与评测前收口（P0，阻塞正式消融）

目标：修正“用户时间硬约束”和“系统排程假设”混用导致的错误冲突；只收口当前评测反例，不在本切片重构完整时间领域模型。

- `departure_at` 和用户明确给出的 `return_by` 均为硬约束；先检查 `departure_at < return_by`，失败时返回 `DEPARTURE_NOT_BEFORE_RETURN_BY`。
- `DEPARTURE_OUTSIDE_TIME_WINDOW` 只保护用户明确给出的数值可用窗口，例如“只能 14:00–18:00”；不得由“上午/下午/晚上”或系统默认窗口触发。
- 用户只给出精确出发时间时，不推断其要求四小时后返程；默认时长只能作为内部 `planning_horizon`/搜索预算或可见假设，不能伪装成 `return_by`。
- 精确出发时间优先于模糊 `time_scope`；例如“下午出去玩，13:00 出发”以 13:00 开始，不产生窗口冲突。
- Resume V1 首先利用现有 `ConstraintValue.source/rule_id` 区分显式窗口与派生窗口；是否正式拆分 `availability_window` 和 `planning_horizon` 留给 S4.5 决定。
- 修正 `conflict_departure_after_return` 的代码/标签一致性，并增加经过 `TurnInterpreter fixture -> Enrichment -> Planning` 的回归测试，不能只测试手工构造的 `NormalizedConstraints`。
- 本切片不实现会话约束 Patch、顶部条件栏、新骨架或 Graph 新节点。

退出案例：

- “下午五点半准时出发，最晚 17:00 回家”稳定返回 `DEPARTURE_NOT_BEFORE_RETURN_BY`，字段为 `departure_at + return_by`，且不调用路线 Provider。
- “下午出去玩，13:00 准时出发”可以进入规划，不把 17:00/18:00 表述成用户返程要求。
- “只能 14:00–18:00，13:00 准时出发”返回显式窗口冲突。
- “13:00 准时出发”生成的方案可以自然延续到晚饭/晚上；若系统采用内部上限，Trace/UI 必须标识为假设而非用户约束。

### S4-E5：跨字段 Validator 稳定诊断码（P0，阻塞正式消融）

目标：把笼统的 `cross_field_contract_failed` 定位到固定业务不变量，同时保持原始响应、用户文本和异常正文不落盘。

- 为 `RawConstraints`、`Interpretation` 和 `ConversationCommand` 的跨字段校验定义有限错误类型；Runtime 继续使用安全诊断码、字段路径和错误类型。
- 真实模型格式修复只接收固定错误类型和字段路径，不接收原始 Provider 内容。
- 对 `all_day`、`quiet_date` 和 `modify_activity` 进行最多两轮小样本复测；同一规则稳定失败时才允许单点修复 Prompt 或 Wire Adapter。
- 不放宽 Pydantic 合同，不增加重试预算，不为通过评测吞掉非法字段。

退出条件：后续报告不再只出现无法定位的 `$ + value_error`；每个跨字段失败都能归入固定、可统计的业务规则码。

### S4-E6：“清淡”语义标签真值（P0，阻塞受控消融）

目标：区分“少辣”这一有限可执行目标与“清淡/清爽”这一开放语义查询，避免用错误标签夸大 PlanningIntent 能力。

- “少辣/不辣/微辣”可以要求 `low_spice`；“清淡/清爽/不重口”默认要求 SemanticQuery 保真，不自动等价为 `low_spice`。
- E2E 评测同时区分：语义是否进入查询、最终方案是否存在 POI/Profile 证据；只有用户证据而没有资源证据时不得算作偏好已满足。
- Rule 与 Hybrid 可以共享同一 Query，但允许在最终资源证据命中率上产生真实差异。
- 修改相同原则应用于“晚餐换成不那么辣的”：该表达仍可要求 `low_spice`。

退出条件：`plan_dinner_only_light` 不再因缺少错误的 `low_spice` 标签失败；报告能单独显示 Query 保真和方案证据命中，不能用推荐文案替代资源证据。

### S4-E7：冻结上游的受控 B0/B1 消融（P0）

目标：在相同、人工复核的 `Interpretation` 输入上只切换 PlanningIntent Adapter，隔离实时 Router 波动。

- 保留现有 B0/B1 作为 live E2E 可靠性 Variant；新增明确命名的 controlled Variant，不静默改变历史 Variant 语义。
- 增加评测专用 FrozenTurnInterpreter Adapter 和版本化 fixture set；每条 fixture 绑定 case/step、输入 hash、Schema 版本、生成模型和人工 review 状态。
- 只允许 reviewed fixture 进入正式受控报告；缺失或 hash 不匹配必须失败，不得退回 Live Router。
- C0/C1 的 Provider、Catalog、Retriever、Advisor、时钟和 fixture 完全相同，只允许 PlanningIntent `rule/llm` 不同。
- 报告分别统计 live E2E 可靠性与 controlled component uplift，不把两者合并成一个成功率。

退出条件：同一输入 fixture 可复现地运行 C0/C1；Router 调用数为 0；报告可以把 PlanningIntent 的收益、fallback、token 和延迟从上游抽取波动中分离出来。

### S4-E8：B2/B3 Pilot、完整消融与简历指标（P0，Gate A 最终门）

目标：在 S4-E4～E7 收口后证明 Hybrid Retrieval 与 Grounded Advice 的增量价值，并生成可引用的最终报告。

- 先在 8 条代表性案例上运行 controlled C0/C1/C2/C3 Pilot；通过门槛后再运行 35 条 reviewed 数据集。
- C1→C2 只切换 Rule/Hybrid Retriever；C2→C3 只切换 Rule/LLM Advisor。
- 检索继续单独报告 Recall@K、nDCG、MRR 和冷/热延迟；端到端报告资源证据命中、任务成功、硬约束、修改不变量和降级。
- Advisor 自动指标只声明结构化 grounded validity、拒绝/fallback 和 token/延迟；解释是否有帮助使用盲评小样本，不能由模型自评。
- 最终报告必须来自干净提交，记录代码、数据集、fixture set、Prompt、索引和模型版本；`dirty=True`、draft label 或 Provider fallback 不得被隐藏。

退出条件：形成一份受控消融报告、一份 live E2E 稳定性报告和简历指标表；任何未证明提升的模型调用点保持默认关闭或明确降级。

### S5：薄模拟执行闭环（P1 加分，2-3 天）

目标：形成 Gate B，但不复制完整 M4。

- 只能从 selected Plan Version 确定性生成 `ExecutionPreview`。
- 用户确认时保存 plan version、价格/动作快照和 snapshot hash；方案变化后旧确认失效。
- 使用幂等键创建 Mock 票务/订座动作，重复请求不得重复下单。
- 持久化最小状态与事件：previewed、confirmed、running、succeeded、failed、compensated/needs_attention。
- 固定注入“第二步失败”场景：已成功的第一步可补偿则自动补偿，否则停止并展示人工处理状态。
- UI 明确标识模拟下单；不接真实支付、库存、商户履约或写 MCP。

退出条件：一个成功 E2E、一个部分失败与补偿 E2E、一个重复确认幂等测试、一个旧快照拒绝测试。

### S4.5：可确认约束栏与 LLM Wire Contract 瘦身（P2，不阻塞 Gate A）

目标：让用户直接看到并纠正规划所依据的信息，同时降低模型一次输出完整 `Interpretation` 的复杂度。只有真实 Pilot 继续证明 Full Schema 不稳定，或演示可理解性不足时才实施。

已知缺陷与后续设计边界（2026-09-20 记录，本切片实施前需重新评审）：

- 平行 provenance 结构脆弱：同一语义值目前可能同时分散在 `raw_constraints`、`evidence_map`、`extraction_confidence` 和 `inferred_fields`，依赖动态字段名维持引用一致性；Provider Schema 无法完整表达这种跨字段不变量，容易出现字段级合法但整体合同失败。证据可追溯原则必须保留，但载体是否继续平行维护需要重新设计。
- `Interpretation` 承担了过多角色：它同时作为模型 Wire Contract、Graph 语义状态、checkpoint 对象、Enrichment 输入、Command 容器、部分诊断和回复载体。后续应区分模型提案、稳定领域语义表示和运行 Trace，但不默认增加模型调用、Graph 节点或第二套业务链路。
- 部分字段业务价值偏低：`intent_scores`、模型自报的 `extraction_confidence`、`requires_clarification`、`reply`、顶层 `target_reference` 等字段需按真实消费者和决策作用逐项审计；未经校准、未参与 Gate/Planner/Verifier 决策的值不得仅因“看起来完整”而继续增加 Wire 负担。
- 模型提案与应用状态必须分开：模型只表达用户语言中的目标、引用和修改条件；active/selected Plan、Plan Version、授权后的资源 ID、锁定结果及其他系统已知状态由 Harness/Resolver 绑定，不能要求模型重新预测。
- `UserAct` 与系统 `NextAction` 必须分开：模型可以解释用户在创建、修改、查询或执行什么，也可以指出歧义；是否反问、使用默认值、继续规划、调用 Provider 或终止，应由 Enrichment、Gate 和 Harness 结合上下文决定。
- 兼容旧 checkpoint 不得通过对实时模型合同全局设置 `extra="ignore"` 实现；未来若拆分 Wire DTO 或升级领域合同，应使用显式版本、Adapter 或迁移路径，同时保持实时输出严格校验。
- 本记录只确认问题，不预先选定 `TurnSemanticFrame`、通用 Constraint AST、动态多阶段 Schema 或多 Agent 作为答案。最终方案必须以诊断集上的首次成功率、repair/fallback、token、延迟、迁移成本和下游接口稳定性为依据。

- 在工作台顶部提供 `Departure`、`Return by`、`Time scope`、`Who`、`Where`、`Preferences`、`Plan Shape`、`Budget` 等可编辑条件项；不再用一个含义模糊的 `time_window` 按钮混合出发、返程和系统排程范围。
- 区分 `user_confirmed`、`model_inferred`、`system_defaulted` 和 `missing`；用户确认后的结构化值优先于模型推断。
- 顶部条件栏表示当前可编辑约束；每个 Plan Version 继续保存不可变的实际约束快照，两者不得混为一份可变状态。
- UI 产生的结构化 Patch 直接进入 Harness，不再经过自由文本重解析；自然语言入口仍可负责首次填充和长尾偏好理解。
- 增加有限 `UPDATE_CONSTRAINTS/REPLAN` 命令：方案完成后，用户补充“我想七点前回家”等约束时，以 active Plan Version 为基线生成 Patch；Harness 合并并展示变化，用户确认后完整重规划、复验并生成新的不可变 Plan Version。
- 约束变化不得原地覆盖旧方案；旧版本保留，新版本清空旧选择，并通过结构化 diff 说明新增、删除或改变的约束。
- 顶部条件栏修改与自然语言补充必须汇合到同一个 Patch/StateMerger Interface，不能维护两套合并语义。
- 保持严格领域 `Interpretation`/`ConversationCommand`，但为模型定义更小的 Wire DTO；Adapter 负责将模型提案编译为领域对象。
- 优先从模型输出中移除可由系统确定的字段：session/plan anchors、非目标站点 locks 和其他派生状态。
- 评审 `intent_scores`、`requires_clarification`、`reply`、`target_reference` 与 `conversation_command.target` 的实际消费者；无决策作用的字段不继续要求模型生成。
- 将 `inferred_fields + evidence_map + extraction_confidence` 的三方同步，逐步收敛为字段级来源/证据状态。未经校准且不影响 Gate 决策的浮点 confidence 不作为产品真值展示。

退出条件：关键规划条件在生成前可见、可确认、可修改；UI Patch 不经过 LLM；相同诊断集上的 Structured Output 稳定性不下降，并减少平均输入 token 或合同错误率。不得为此新增 Graph 层级。

### S4.6：有限骨架扩展 `activity -> dinner -> activity`（P2，不阻塞 Gate A）

目标：支持“先活动、吃晚饭、再参加夜间活动”的真实晚间行程，但继续使用封闭、可验证的骨架语法。

- 新增一个明确的 `activity-dinner-activity-v1` 骨架，不开放任意角色数组或通用重排。
- 两个活动站点必须使用不同资源；晚饭继续遵守餐时锚点，第二个活动必须经过营业、路线、返程与 Availability 复验。
- 用户只说“换活动”时，因为存在两个活动目标必须反问；“换第一个/晚饭后的活动”可以唯一解析。
- 单站修改继续锁定其余站点，不为该骨架新增独立修改链路。
- 增加显式结构编译、候选去重、夜间营业、目标歧义和 HTTP 恢复测试。

退出条件：“展览 -> 晚餐 -> 夜间演出”能够由现有 Planner/Provider/Verifier 产出；重复活动、晚饭后关闭、返程超限和含糊修改均得到可解释结果。

## 5. 当前长期路线图内容如何重排

| 长期路线图能力 | Resume Release 处理 |
| --- | --- |
| M2.5 产品真值 | S0，阻塞后续演示 |
| M3-A 对话控制、精确时间、上下文 | 缩到 S1，只保留修改所需字段和状态 |
| M3-B 定向修改 | S3，KEEP + REPLACE 完整纵向实现 |
| M3-C PlanningIntent | 缩到 S2，只保留一个模型决策点和确定性回退 |
| M3-C ToolBroker / PlanCritic | 推迟，不是首发依赖 |
| M3.5 记忆 / Hybrid RAG | 推迟；只有离线评测证明标签召回不足后再做最小语义检索 |
| M4 执行闭环 | 缩到 S5 的单一 Mock happy path + 单一失败补偿 |
| M5 评测与产品化 | 评测、Trace、演示前移到 S4；部署加固后置 |
| MCP | 删除出 Resume Release 范围；出现真实外部互操作需求后再评审 |

## 6. 明确保留、简化、推迟和不做

### 保留

- 当前 M2 Planner、Catalog/Provider Adapter、Verifier、有限 Repair、SQLite/HTTP E2E 和 Replay/Mock。
- LangGraph 的状态转换、interrupt/resume 和 checkpoint；不以节点数量证明 Agent 能力。
- 模板 Presenter 和无 LLM 降级路径。

### 简化

- 固定骨架演进只满足精确时间与 1-4 站有限语法，不重写整个 Planner。
- ContextAssembler 先做纯函数式有界投影。
- 模拟执行只做一个成功分支和一个失败补偿分支。

### 推迟

- 长期/向量记忆、完整 Hybrid RAG、LLM rerank、PlanCritic、通用 Tool Loop。
- 更多城市、真实商户库存、真实订单和支付、公开高可用部署。

### 不做

- Multi-Agent 拆分。
- 为简历关键词单独增加 MCP 或向量数据库。
- 把 Planner 内部每个步骤提升为 Graph 节点。
- 在没有失败用户故事或评测证据时再次整体改架构。

## 7. 进度控制规则

- 同时只允许一个核心切片在制；每个切片从干净提交开始并以可运行提交结束。
- 一次开发任务只承诺一个用户故事，必须列出允许修改、非目标、验收测试和设计门。
- 新增顶层 Module、Graph 子图、数据库核心表、外部依赖或模型调用点时先单独评审。
- 每个切片结束必须能回答：模型决定什么、代码决定什么、外部事实是什么、失败如何回退、增加了哪些 Interface。
- 测试通过不等于产品完成；必须同时验证 UI 陈述、持久化恢复、降级和 Trace。
- S0-S4 完成后冻结 Gate A，不因 S5 或未来 M3.5/M5 未完成而延期投递。

## 8. 推荐实施顺序

```text
S0 产品真值
 -> S1 状态与领域地基
 -> S2 受约束生成
 -> S3 KEEP/REPLACE 修改
 -> S4 评测与投递包装（Gate A）
 -> S5 薄模拟执行（Gate B）
 -> 根据评测决定是否需要 RAG、记忆、PlanCritic 或 MCP
```

预计 Gate A 需要约 9-16 个有效开发日；Gate B 再增加约 2-3 日。若任一切片超出估时 50%，先缩小验收面，不把后续里程碑基础设施提前搬入当前切片。

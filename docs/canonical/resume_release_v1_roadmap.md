# HappyFreeTime Resume Release V1 交付路线图

> 状态：秋招投递范围冻结基线  
> 基线：`codex/m2-s0-native-planning` / `78792c0`  
> 日期：2026-09-05  
> 关系：本文只收敛近期交付顺序；长期能力仍以 `v2_roadmap.md` 为目标，不代表其中 M3-M5 已实现。

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
- 支持单站晚饭，不再强制活动 + 餐饮。
- 持久化 active/selected Plan Version、Plan Version 关系和最小 `SessionSnapshot`。
- 定义最小 `ConversationCommand`、`TargetReference`、`ConstraintPatch` 和 `LockedStop`。
- 首版命令只承诺 `CREATE`、`SELECT`、`KEEP`、`REPLACE`；其余明确返回 unsupported 或反问。

退出案例：

- “14:30 出发，18:00 前到家”保留两个独立时间锚点。
- “只安排一家晚饭”产生一个停靠点。
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

### S5：薄模拟执行闭环（P1 加分，2-3 天）

目标：形成 Gate B，但不复制完整 M4。

- 只能从 selected Plan Version 确定性生成 `ExecutionPreview`。
- 用户确认时保存 plan version、价格/动作快照和 snapshot hash；方案变化后旧确认失效。
- 使用幂等键创建 Mock 票务/订座动作，重复请求不得重复下单。
- 持久化最小状态与事件：previewed、confirmed、running、succeeded、failed、compensated/needs_attention。
- 固定注入“第二步失败”场景：已成功的第一步可补偿则自动补偿，否则停止并展示人工处理状态。
- UI 明确标识模拟下单；不接真实支付、库存、商户履约或写 MCP。

退出条件：一个成功 E2E、一个部分失败与补偿 E2E、一个重复确认幂等测试、一个旧快照拒绝测试。

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

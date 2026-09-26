# 从万能 Interpretation 到受约束 Wire Proposal：一次评测驱动的语义入口重构

_HappyFreeTime 面试复盘 · 对应提交 `4455cdc`、`d478ac4`、`d7b21ba` · 2026-09-20_

---

## 一页结论

这次重构解决的不是“Prompt 写得不够好”，而是一个更根本的接口设计问题：**此前实时 LLM 被要求直接生成系统内部的完整 `Interpretation` 领域对象，同时还要为自己的结果生成意图分数、置信度、推断标记、证据、对话命令和自然语言回复。**

这些字段单独看都有理由，但组合在一个模型 Wire Contract 中后，模型不仅要理解用户，还要维护多份平行结构之间的引用完整性。一旦某个值、证据、置信度或推断标记不同步，整轮输入就会被判为 `invalid_output`，即使真正的业务语义已经理解正确。

最终采用的方案不是放宽领域校验，也不是让模型直接执行修改，而是把链路拆成三个语义层次：

```text
用户语言
  ↓
LlmInterpretationProposal        模型提案：用户表达了什么
  ↓  Pydantic + deterministic compiler
Interpretation                   系统内部兼容对象：Graph 能消费什么
  ↓  Session / Graph / Resolver
ConversationCommand + Action     获得应用状态后：系统允许做什么
```

核心变化可以概括为：

- 模型只输出它有资格判断的语义，不再预测应用已经掌握的状态。
- Harness 负责把模型提案编译成严格领域对象，并决定执行、忽略还是反问。
- `resource_id`、Plan ID、Plan Version ID 和置信授权不再出现在模型 Wire Schema 中。
- 创建规划不再要求无意义的 `create command`。
- 不完整但合法的修改目标不再导致整轮失败，而是安全降级为反问。
- Graph、Planner、API、数据库和既有 checkpoint 领域对象保持兼容，没有为了重构重新搭一套系统。

真实 DeepSeek 小型诊断从重构中段的 6/8 最终成功，收口到 8/8 首次结构化成功、8 次 Provider attempt、0 fallback。这个数字只证明本轮 Wire Contract 诊断目标已达到，**不是正式产品成功率，也不能直接写入简历指标**。

---

## 1. 先解释：Wire Contract 到底是什么

这里的 Wire Contract 不是法律意义上的合同，也不是系统所有内部数据结构。

它指的是：

> 模型或外部 Provider 跨越接口 seam 时，必须返回的数据形状、字段语义、允许值和错误行为。

在 HappyFreeTime 中，它主要体现为传给 `with_structured_output(...)` 的 Pydantic Schema。模型输出必须先通过这个 Schema，才能进入应用内部。

Wire Contract 与领域对象的区别是：

| 概念 | 回答的问题 | 谁产生 | 谁消费 |
| --- | --- | --- | --- |
| `LlmInterpretationProposal` | 用户这句话表达了什么 | LLM | 确定性 Compiler |
| `Interpretation` | 当前系统内部如何统一表示这一轮语义 | Compiler、Demo Adapter、兼容层 | Graph、Enrichment、Gate |
| `ConversationCommand` | 当前会话中允许尝试什么受限操作 | Compiler + 应用状态解析 | 修改链路 |
| 最终业务 Action | 是否真的修改、持久化或产生副作用 | Harness | Planner、Repository、Provider |

此前的问题，就是把这四层中太多职责压进了一个模型输出对象。

相关实现：

- [实时语义入口与 Wire Proposal](../../app/services/router_extractor.py)
- [内部 Interpretation 与 ConversationCommand](../../app/domain/constraints.py)
- [Graph 路由和修改反问](../../app/orchestration/entry_graph.py)

---

## 2. 重构前的设计是什么样

### 2.1 模型直接生成内部 Interpretation

重构前，实时模型基本被要求一次性生成完整 `Interpretation`：

```text
Interpretation
├── primary_intent
├── intent_scores
├── raw_constraints
├── selected_plan_index
├── target_reference
├── conversation_command
│   ├── operation
│   ├── base_plan_version_id
│   ├── base_plan_id
│   ├── target
│   ├── locked_targets
│   ├── constraint_patch
│   ├── replacement_criteria
│   ├── evidence
│   └── confidence
├── extraction_confidence
├── evidence_map
├── inferred_fields
├── reply
└── requires_clarification
```

它混合了至少六类职责：

1. 意图路由；
2. 用户约束抽取；
3. 归一化后的结构化值；
4. 修改命令提案；
5. 模型自我审计信息；
6. 面向用户的回复和下一步控制判断。

问题不只是字段多，而是模型要保证它们互相一致。

### 2.2 平行 provenance 结构

同一个语义字段可能同时出现在四处：

```text
raw_constraints.<field>         实际值
evidence_map[<field>]           用户原话证据
extraction_confidence[<field>]  模型自报置信度
inferred_fields                 是否由模型推断
```

例如模型若声明：

```json
{
  "inferred_fields": ["party"]
}
```

原合同要求同时存在：

- 一个能够代表 `party` 的实际值；
- `evidence_map["party"]`；
- `extraction_confidence["party"]`。

这本质上要求模型自己维护一次小型数据库 join。模型即使理解了“和女朋友约会”，只要漏掉其中一个平行字段，整个结构就会失败。

### 2.3 模型提案与应用状态混在一起

原 `ConversationCommand` 还包含：

- `base_plan_id`
- `base_plan_version_id`
- `resource_id`
- `confidence`

但这些字段不应该由模型决定：

- Plan ID 和 Version ID 已经存在于 Session Snapshot；
- `resource_id` 来自当前已选方案和 Catalog；
- 是否允许修改由 active version、selected plan 和租户归属共同决定；
- 模型自报 confidence 不是授权依据。

让模型重新输出应用已经知道的状态，既增加失败概率，又容易模糊权限边界。

### 2.4 创建与修改共用一个大命令对象

原 Prompt 要求创建、选择和修改都输出 `conversation_command`。于是普通创建请求也可能生成：

```json
{
  "operation": "create",
  "target": null
}
```

但创建链路实际依赖的是：

```text
primary_intent + raw_constraints
```

Graph 并不需要一个 `create command` 才能进入 Enrichment 和 Planning。这部分模型输出没有业务收益，却可能因为嵌套字段不合法导致整轮创建失败。

---

## 3. 问题是如何暴露出来的

这次重构不是凭感觉进行的，而是由真实 Pilot 和结构化输出诊断推动。

### 3.1 第一类问题：Provider 兼容

AIHubMix/Qwen 路径曾把嵌套的 `conversation_command` 返回成 JSON 字符串，而不是对象。顶层结构化输出成功，但嵌套字段无法直接通过 Pydantic。

系统只增加了一个很窄的兼容处理：

- 仅允许解码 `conversation_command` 这一字段；
- 解码后仍完整进入 Pydantic；
- 不接受普通文本兜底；
- 不跳过领域校验。

这说明“OpenAI 协议兼容”不等于“复杂 JSON Schema 行为完全一致”。

### 3.2 第二类问题：party 合同不一致

Prompt 允许模型从“女朋友”“对象”“父母”提取同行人语义，并标记 `inferred_fields=["party"]`。

但原 Validator 判断 `party` 是否有值时只检查：

```text
adults / children / child_age
```

没有把 `members=["女朋友"]` 视为有效同行人语义。于是出现：

```text
模型：我抽取到了 party
数据：members=["女朋友"]
Validator：party 没有值
结果：inferred_value_missing
```

`4455cdc` 先修复了这个明确的领域不一致：非空 `members` 可以证明存在同行人语义，但不会因此自动推断 `adults=2`。

然而修复后，部分模型输出仍然只声明 `inferred_fields=["party"]`，却没有提供任何同行人值。这说明局部修补无法解决根因：**要求模型同时输出值和自我审计记录，本身就是一个脆弱接口。**

### 3.3 第三类问题：不完整 Target 被误判为整轮无效

分离 Wire Proposal 后，剩余失败集中到：

```text
conversation_command.target
```

例如用户说：

> 把那个地方换一下。

模型可能正确识别这是 `replace`，也保留了 `raw_text="那个地方"`，但无法唯一判断它是活动、餐厅还是第几站。

旧合同要求 Target 必须立即具有：

```text
role / resource_type / stop_index / resource_id
```

至少一个维度。于是“语义理解正确但引用尚不明确”被错误归类为 `invalid_output`。

实际上这不是模型格式错误，而是一个正常的产品状态：系统应该追问用户，而不是判定模型失败。

---

## 4. 三个提交分别做了什么

### 4.1 `4455cdc`：先修复可证明的 Party Contract Bug

这一提交只做最小事实修复：

```text
party 有有效语义值 =
    adults 非空
    或 children 非空
    或 child_age 非空
    或 members 非空
```

同时增强安全诊断，只记录：

- 固定诊断码；
- 有限字段路径；
- Pydantic 错误类型。

不记录原始模型输出、用户全文、Prompt 或密钥。

这一步的价值在于区分：

- 明确的代码合同 Bug；
- 模型偶发波动；
- Wire Schema 设计问题。

### 4.2 `d478ac4`：分离 Wire Proposal 与内部 Interpretation

新增模型专用的 `LlmInterpretationProposal`：

```python
class LlmInterpretationProposal(BaseModel):
    primary_intent: Intent
    raw_constraints: RawConstraints
    selected_plan_index: int | None
    conversation_command: ConversationCommandProposal | None
    evidence_map: dict[str, str]
```

模型不再输出：

- `intent_scores`
- `extraction_confidence`
- `inferred_fields`
- `reply`
- `requires_clarification`
- 顶层 `target_reference`

`ConversationCommandProposal` 也不再包含：

- `base_plan_id`
- `base_plan_version_id`
- `confidence`
- 模型生成的 `resource_id`

确定性 Compiler 再把 Proposal 转换成原有内部 `Interpretation`：

```text
intent_scores = {primary_intent: 1.0}  兼容字段，不声称是概率
extraction_confidence = {}             不制造假置信度
inferred_fields = set()                不制造推断声明
reply = ""                             回复不再由解析模型负责
requires_clarification = False         真正是否反问交给下游 Policy/Gate
```

这里最重要的取舍是：**没有立刻删除内部 `Interpretation` 的旧字段。**

原因是 Graph、测试 Fake、checkpoint 和历史持久化数据仍依赖它。通过增加 Provider-facing DTO 和 Compiler，可以先修正模型接口，同时保持系统内部兼容；后续是否迁移内部领域对象，再由单独切片处理。

### 4.3 `d7b21ba`：让不完整 Command 安全地“执行或反问”

这一提交把模型 Target 明确为 Proposal：

```python
class ConversationTargetProposal(BaseModel):
    role: StopRole | None
    resource_type: ResourceType | None
    stop_index: int | None
    raw_text: str
```

Proposal 可以只保留 `raw_text`。Compiler 使用有限、确定性的词汇解析：

```text
活动   → role=activity
晚饭   → role=dinner
第二站 → stop_index=1
餐厅   → resource_type=restaurant
```

这里的 Alias 不是用来替代 LLM 理解，而是一个安全的引用解析器：只有能唯一映射到系统有限语法的表达才会被编译。

三种结果如下：

| 模型提案 | Compiler 结果 | 系统行为 |
| --- | --- | --- |
| `target.role=activity` | 严格 `TargetReference` | 进入修改链路 |
| `target.raw_text="活动"` | 确定性解析为 activity | 进入修改链路 |
| `target.raw_text="那个地方"` | 无法唯一解析 | 不产生 Command，Graph 反问 |

创建请求如果多输出了 `operation=create`：

- Harness 丢弃它；
- 保留合法 `Interpretation`；
- Trace 记录 `command_ignored_for_plan`；
- 不因为无价值的嵌套结构阻断规划。

修改目标无法唯一解析时：

- 不进入严格 `ConversationCommand`；
- 不触发修改执行；
- Runtime 标记 `target_resolution_required`；
- `primary_intent=refine_plan` 仍让 Graph 进入 `modify_plan`；
- 修改节点发现没有可执行 Command 后返回阻塞式问题。

此外，实时模型字典不再允许通过旧 `Interpretation` 键自动绕过新 Wire DTO。测试 Fake 仍可直接返回内部 `Interpretation`，但外部字典必须经过新 Proposal 校验。

---

## 5. 重构后的完整代码链路

### 5.1 总链路

```mermaid
flowchart TD
    user([用户输入]) --> llm[LLM Structured Output]
    llm --> wire[LlmInterpretationProposal]
    wire --> validate{Pydantic Wire 校验}
    validate -->|非法 JSON/非法字段| repair[最多一次格式修复]
    repair --> validate2{再次校验}
    validate2 -->|仍失败| fallback[安全澄清 fallback]
    validate -->|合法| compiler[Deterministic Compiler]
    validate2 -->|合法| compiler
    compiler --> interpretation[内部 Interpretation]
    interpretation --> graph{Main Graph 路由}
    graph -->|创建| enrichment[Enrichment / Gate / Planning]
    graph -->|明确修改| modify[Target 解析 / 修改 / 完整复验]
    graph -->|修改目标不明确| question[阻塞式反问]

    classDef model fill:#ede9fe,stroke:#7c3aed,color:#3b0764
    classDef harness fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef safe fill:#dcfce7,stroke:#16a34a,color:#14532d
    class llm,wire model
    class validate,validate2,compiler,interpretation,graph,enrichment,modify harness
    class fallback,question safe
```

### 5.2 创建规划示例

用户：

> 明天和女朋友约会一整天，不要排得太累。

模型只需要输出类似：

```json
{
  "primary_intent": "plan_outing",
  "raw_constraints": {
    "date_text": "明天",
    "date_reference": "tomorrow",
    "time_text": "一整天",
    "time_scope": "all_day",
    "members": ["女朋友"],
    "preferences": ["不要排得太累"]
  },
  "selected_plan_index": null,
  "conversation_command": null,
  "evidence_map": {
    "date_reference": "明天",
    "time_scope": "一整天",
    "members": "跟女朋友",
    "preferences": "不要排得太累"
  }
}
```

模型不需要同时生成：

- 每个意图的伪概率；
- `party` 推断标记；
- 每个字段的自报置信度；
- `create command`；
- 是否应该反问；
- 面向用户的回复。

Compiler 生成内部兼容对象，Graph 再进入 Enrichment、Question Gate、StructureCompiler、PlanningIntent、CandidateRetriever、Provider、Planner 和 Verifier。

### 5.3 明确修改示例

用户：

> 餐厅保留，只把活动换近一点。

模型 Proposal：

```json
{
  "primary_intent": "refine_plan",
  "conversation_command": {
    "operation": "replace",
    "target": {
      "role": "activity",
      "raw_text": "活动"
    },
    "locked_targets": [
      {
        "resource_type": "restaurant",
        "raw_text": "餐厅"
      }
    ],
    "constraint_patch": {
      "prefer_shorter_travel": true
    }
  }
}
```

Compiler 只产生受限 `ConversationCommand`。它仍然没有：

- selected Plan ID；
- active Plan Version ID；
- 具体活动或餐厅的 `resource_id`；
- 数据库写权限。

后续 Harness 才会：

1. 从 Session Snapshot 取得当前 active version 和 selected plan；
2. 在当前 Plan 中把 activity/restaurant 引用解析为真实站点；
3. 锁定非目标站点；
4. 只重新召回目标角色；
5. 重算路线、营业和 Availability；
6. 经过 Verifier；
7. 生成新的不可变 Plan Version 和候选级 `PlanDiff`。

### 5.4 模糊修改示例

用户：

> 把那个地方换一下。

这时模型识别出 `replace` 并不是失败，但 `那个地方` 无法唯一绑定到某一站。

新系统的处理是：

```text
合法 Proposal
  ↓
Target 无法唯一解析
  ↓
不产生严格 ConversationCommand
  ↓
不执行修改
  ↓
Graph 询问要替换哪一站
```

这体现了一个重要原则：

> “需要用户补充信息”是正常业务状态，不应被伪装成模型格式错误。

---

## 6. 模型与 Harness 的新职责边界

| 能力 | LLM | Harness |
| --- | --- | --- |
| 理解用户大致意图 | 提案 | 校验并路由 |
| 抽取日期、时间、人数、预算和偏好 | 输出有限结构与证据 | 规范化、补默认、判断冲突 |
| 判断“活动”“第二站”等语言引用 | 可输出语义 Target | 在有限词汇和当前 Plan 中解析 |
| 生成 Plan/Version/resource ID | 禁止 | 从 Session、Repository、Catalog 获取 |
| 决定是否执行修改 | 无权 | 校验 active version、selected plan 和目标唯一性 |
| 路线、天气、营业、库存 | 不生成事实 | Provider 获取并记录来源 |
| 判断方案是否可行 | 可参与软语义 | Planner/Verifier 裁决硬约束 |
| 输出置信概率 | 不再作为业务合同 | 使用验证结果和明确状态，不信任未校准自报概率 |
| 决定是否反问 | 可暴露缺失语义 | Gate/Graph 根据业务状态决定 |
| 用户回复 | 不在 TurnInterpreter 中生成 | Presenter/Question/Advisor 负责 |

这不是简单地“削弱模型”。模型仍负责最有价值的开放语义理解；Harness 则负责模型无法可靠拥有的事实、状态、权限和可验证后置条件。

---

## 7. 为什么这比继续改 Prompt 更有效

Prompt 可以提醒模型“别忘了 evidence、confidence 和 inferred field”，但不能消除接口本身的多重引用完整性。

如果继续在 Prompt 中追加规则，会产生三个问题：

1. 模型要记忆更多字段组合，漏项概率可能上升；
2. 不同 Provider 对复杂嵌套 Schema 的实现差异会被放大；
3. 业务正常的不确定状态仍会被归类为格式失败。

这次改造是在正确 seam 上减少模型必须知道的接口面积：

```text
大而浅的接口：模型填很多字段，系统只是转发

        ↓

小一些的模型接口 + 更深的 Compiler：
模型表达语义，系统集中处理兼容、编译、诊断和安全降级
```

删除 Compiler 后，这些复杂度会重新散落到 Graph、Prompt、Provider 兼容层和修改节点中，因此它是一个真正有深度的 module，而不是无意义的转发层。

---

## 8. 为什么没有一次性做成更“大”的新架构

### 8.1 没有拆成 Router + 多个 Extractor 模型调用

可以先用一个小 Router 判断 create/modify/execute，再动态选择不同 Schema。但当前这样做会：

- 增加一次模型调用和延迟；
- 增加 Provider 成本与新失败点；
- 要求修改 Main Graph；
- 当前 Resume V1 只有创建和有限单站修改，收益尚未证明。

因此目前仍保持一次模型调用，只缩小其 Wire Contract。

### 8.2 没有立刻引入通用 Constraint AST

`eq/lte/gte/before/after` 形式的 Constraint Union 更可扩展，但会影响 Enrichment、Planner、Verifier、持久化和历史 checkpoint。当前已支持的关键反例可以由现有强类型字段表达，直接重写会扩大风险和工期。

### 8.3 没有删除内部 Interpretation 的兼容字段

内部 `Interpretation` 仍包含历史字段，这并不理想，但它不再是实时模型必须生成的 Wire Schema。当前优先解决 Provider 稳定性，避免在同一切片同时迁移 Graph 状态与 checkpoint。

### 8.4 没有让 raw text 直接授权执行

模型输出 `raw_text="那个地方"` 只代表用户说了什么，不代表系统已经知道是哪一站。只有有限、唯一的引用才会编译；其余情况必须反问。

---

## 9. 证据与评测结果应该如何表述

### 已有代码证据

- `4455cdc`：Party Contract 与安全诊断；
- `d478ac4`：Wire Proposal 与内部领域对象分离；
- `d7b21ba`：不完整 Command 的确定性编译与安全反问；
- 当前后端测试报告：370 passed，30 subtests；
- 定向 Router/Frozen/Graph 报告：42 passed，12 subtests；
- DeepSeek 8 个诊断案例最终达到 8/8 首次结构化成功；
- `evals/frozen_interpretations_pilot_v1.json` 已生成，但仍是 draft。

### 8/8 能证明什么

- 当前 DeepSeek + Provider 组合可以稳定产生这 8 条创建、澄清和冲突案例的 Wire Proposal；
- 原 `party/inferred_fields` 与 `conversation_command.target` 失败已不再出现；
- 不需要额外模型调用即可完成收口。

### 8/8 不能证明什么

- 不能证明所有用户表达都能正确理解；
- 不能当成端到端任务成功率；
- 不能证明 Hybrid Retrieval、Planner 或 Recommendation Advisor 的质量；
- 修改案例的真实模型步骤当前走结构化 UI，8 条 Fixture 只捕获了初始规划；
- 还没有验证自然语言“餐厅保留，只换活动”的真实 Provider 稳定性；
- Fixture 仍需人工复核，不能直接进入正式消融。

因此这批数字适合在面试中作为“诊断驱动重构的工程证据”，不适合当作简历主指标。

---

## 10. 当前仍然存在的限制

### 10.1 RawConstraints 仍然较大

模型 Wire Contract 已明显收敛，但 `RawConstraints` 仍包含较多可空字段和日期组合约束。未来功能继续增长时，可以评估 intent-specific Schema 或受限的 discriminated union；现在不应为了形式先进再次整体重写。

### 10.2 UserAct 与 NextAction 还没有完全分离成新领域类型

当前 `primary_intent` 仍同时承担部分用户行为分类和 Graph 路由。长期应该明确：

```text
UserAct    = 用户表达了什么
NextAction = 系统结合状态后决定做什么
```

本次通过 Compiler、Gate 和 Graph 已经在行为上加强了这一区分，但尚未迁移成全新的领域对象。

### 10.3 ConversationCommandProposal 仍是一个共享可空对象

未来若增加执行、取消、预订等能力，应该考虑：

```text
CreatePlanProposal | ModifyPlanProposal | ExecuteProposal
```

这样的 discriminated union，避免每个操作共享所有字段。Resume V1 暂时只有有限修改，不值得现在扩展。

### 10.4 自然语言修改仍缺真实模型探针

应额外验证：

- “餐厅保留，只把活动换近一点”应编译执行；
- “把第二站换掉”应编译为 `stop_index=1`；
- “把那个地方换一下”应进入反问而非 fallback。

### 10.5 Draft Fixture 需要人工修正

当前 Fixture 中至少需要复核：

- `modify_activity_shorter` 把“约会”放进 `members`，语义不正确；
- `plan_dinner_only_light` 同时把“清淡”放进 `preferences` 与 `diet_tags`，可能造成重复语义查询。

---

## 11. 面试时怎么讲

### 30 秒版本

> 项目早期让 LLM 直接输出完整领域 Interpretation，里面同时包含约束、证据、置信度、推断标记、命令和回复。真实评测发现，模型往往已经理解业务，却因为这些平行字段不同步而被判 invalid output。我没有放宽业务校验，而是新增模型专用 Wire Proposal 和确定性 Compiler：模型只表达用户语义，Harness 注入应用状态、解析有限目标并决定执行或反问。这样保留了 Provider、Verifier 和会话权限边界，同时把 8 条 DeepSeek 诊断案例从中段的 6/8 收口到 8/8 首次结构化成功。

### 两分钟版本

> 这次重构的核心是区分模型提案、内部领域对象和可执行业务命令。旧设计让模型直接生成 Interpretation，一个字段可能同时要在 raw value、evidence、confidence 和 inferred_fields 四个位置保持一致；ConversationCommand 里还混入 Plan ID、Version ID 和 resource ID 等应用状态。最典型的问题是“女朋友”已经进入 members，但 party Validator 不认可；局部修好后，模型仍会只声明 inferred party 而不给实际值，说明问题在接口而不是单个 Prompt。
>
> 我先用安全诊断定位失败字段，再把 Provider Schema 改成 LlmInterpretationProposal，只保留 intent、raw constraints、evidence 和有限 command proposal。确定性 Compiler 继续输出旧 Interpretation，因此 Graph、API、数据库和 checkpoint 不需要迁移。对于修改目标，模型可以给 role/index，也可以只保留 raw text；Harness 只解析“活动、晚饭、第二站、餐厅”等有限唯一引用，模糊表达不会执行，而是进入 Graph 反问。模型仍负责开放语义，代码负责状态、权限、事实和硬约束。这个改造没有增加模型调用，却显著降低了结构化输出失败。

### 一句话技术取舍

> 我没有通过降低 Schema 严格度换成功率，而是缩小模型必须负责的接口，把正常的不确定性从“模型错误”重新建模成“系统需要反问的业务状态”。

---

## 12. 高频追问

### Q1：为什么不让 LLM 直接输出最终 Plan？

因为最终 Plan 包含路线、营业、Availability、预算、时间线和真实 POI 身份。模型可以理解偏好，但不能成为这些外部事实和硬约束的来源。直接输出 Plan 还会让后续修改、恢复、版本化和下单缺少稳定对象身份。

### Q2：为什么不直接删掉所有 Validator？

Validator 保护的是领域真值，例如日期组合、时间顺序、修改目标和未知字段。真正的问题不是校验太严格，而是模型被要求产生它不应该负责的字段。新设计保留严格校验，只把校验放到正确层次。

### Q3：为什么模型不能输出 resource_id？

`resource_id` 是 Catalog 和当前 Plan 的真实身份。模型生成它既可能幻觉，也绕过了会话和候选范围。模型只能说“活动”或“第二站”，Harness 再在当前已选方案中解析。

### Q4：有限 Alias 不还是关键词匹配吗？

它不是用来理解开放需求，而是用来把已经识别出的引用安全绑定到有限领域语法。它只接受能唯一确定的表达；不确定就反问。开放偏好仍由 PlanningIntent 和 Hybrid Retrieval 处理。

### Q5：为什么不相信模型输出的 confidence？

未经项目数据校准的自报置信度不能解释为真实概率，也不应授予业务权限。当前用 Schema、证据、当前状态和后置校验决定能否继续，而不是使用一个看似精确的 `0.87`。

### Q6：Compiler 是不是多余的中间层？

不是。它集中承担 Provider 兼容、字段收敛、应用状态隔离、有限引用解析、诊断和安全降级。删除它后，这些逻辑会重新散落到 Prompt、Graph 和每个业务节点，调用方需要理解更多细节。

### Q7：这能叫 Agent Engineering 吗？

单独这一项不能证明完整 Agent 能力，但它属于关键的 Agent Harness Engineering：模型产生有边界的语义提案，系统结合状态决定下一步，失败可观测、可反问、可回退，且模型不能越权生成事实或执行副作用。完整项目还需要结合多轮状态、工具 Provider、Hybrid Retrieval、Verifier、Repair、Plan Version 和离线消融一起说明。

### Q8：下一步还要继续重构 Interpretation 吗？

先完成 Frozen Fixture 人工复核和正式 C0–C3 消融。只有评测继续证明当前 `RawConstraints` 或共享 Command Proposal 是主要失败来源，才启动后续 S4.5。架构演进应由失败案例驱动，而不是为了拥有更多类型或 Graph 节点。

---

## 13. 可用于简历或项目介绍的一条表述

> 将实时 LLM 与内部领域模型解耦，设计受限 `Wire Proposal → Deterministic Compiler → Domain Command` 链路：移除模型生成的应用状态、未校准置信度和重复 provenance 字段，对不完整修改目标执行有限引用解析并安全转为反问；在不增加模型调用、不迁移 Graph/数据库的前提下，将 8 条结构化输出诊断样例收口至 8/8 首次成功，并保留严格 Schema、状态授权与确定性降级。

使用这条表述时，应把“8/8”明确称为诊断样例，而不是产品成功率。正式简历指标应等待 Frozen Interpretation、Rule/LLM PlanningIntent、Rule/Hybrid Retrieval 和完整链路消融完成后再填写。


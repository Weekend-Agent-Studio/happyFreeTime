# RouterExtractor Architecture Design

_归档状态：RouterExtractor 后期草稿 · 已被 V2 总体架构和当前代码替代_

---

> ⚠️ **历史文档：** 其中仍可能包含未采用的设计和未验证数字。当前依据见 [`../../canonical/architecture_v2.md`](../../canonical/architecture_v2.md) 和 `app/services/router_extractor.py`。

## 1. 背景

当前系统采用：

```text
IntentAgent -> SlotAgent -> PlannerAgent -> ExecutorAgent
```

这个拆分在概念上清晰，但前两步存在一定冗余：

- `IntentAgent` 用一次 LLM 判断用户意图。
- `SlotAgent` 再用一次或多次 LLM 解析用户信息，并通过 ReAct tool calling 补充时间、位置、天气。

这会带来几个问题：

1. 同一句用户输入被两个 Agent 重复理解。
2. `SlotAgent` 为了调用固定工具产生多轮 LLM 调用，耗时较长。
3. 反问逻辑依赖 LLM 判断，容易过度反问。
4. 时间、位置、天气、默认预算、默认距离等本应由代码稳定补全的内容，被放进了 LLM 流程。

因此建议将 `IntentAgent` 和 `SlotAgent` 合并成一个轻量的 `RouterExtractor`，并把环境补全与反问判断移到确定性代码中。

## 2. 新整体架构

推荐目标架构：

```text
User Input
  -> RouterExtractor
       一次 LLM 调用：识别意图、抽取显式约束
       （环境信息由代码提前注入 prompt，不通过 LLM tool calling）
  -> Enrichment Service
       代码补全：时间解析、默认位置、天气、预算、距离、时间窗、人群标准化
  -> NeedQuestion Gate
       代码判断：缺关键字段 → Disambiguator → interrupt 反问
       信息齐全 → Planner
  -> Disambiguator（可选，Phase 1 可用模板）
       一次极短 LLM 调用：把 Gate 的结构化缺字段转成自然反问
  -> Planning Service / Planner
       生成候选方案
  -> Presenter
       可选 LLM：解释方案、展示取舍、说明 assumptions
  -> Executor
       用户确认后执行预约、订票、下单
```

第一阶段可以先只改前三部分：

```text
RouterExtractor -> Enrichment Service -> NeedQuestion Gate
```

Planner 和 Executor 暂时沿用现有实现，后续再逐步改造成 service-first 的规划方式。

## 3. 设计目标

### 3.1 性能目标

将原来的：

```text
IntentAgent: 1 次 LLM
SlotAgent: 多次 LLM + 多次 tool calling
```

收敛为：

```text
RouterExtractor: 1 次 LLM（环境信息由代码预注入）
Enrichment Service: 0 次 LLM
NeedQuestion Gate: 0 次 LLM
Disambiguator: 0-1 次短 LLM（仅当需要反问时，Phase 1 可用模板替代）
```

普通规划请求在进入 Planner 前只需 1 次 LLM；需要反问时最多 2 次。

### 3.2 稳定性目标

- 时间、位置、天气等环境信息由代码补全。
- 默认值由规则决定。
- 是否反问由规则判断。
- LLM 只负责自然语言理解，不负责环境工具调用。

### 3.3 产品体验目标

不要因为信息不完整就频繁反问。优先策略是：

```text
能默认就默认，能规划就先规划；
只有关键字段缺失时才反问；
反问一次只问最重要的问题。
```

## 4. 三个核心模块

## 4.1 RouterExtractor

### 职责

`RouterExtractor` 是一次 LLM 结构化解析模块，负责从用户输入中提取：

- 当前意图
- 多意图置信度
- 用户是否在选择已有方案
- 用户是否在修改已有方案
- 显式约束
- 用户明确缺失但不能默认的信息
- 用户原话中的偏好、排斥、预算、时间、人群

它不负责：

- 调用天气、位置、时间工具（工具由调用方代码提前调用并注入 prompt）
- 搜索活动或餐厅
- 生成具体方案
- 判断最终是否必须反问
- 执行预约或下单

### 环境信息预注入

RouterExtractor **不通过 LLM tool calling 获取环境信息**，而是由调用方在构造 prompt 之前，由代码调用工具并注入：

```text
调用方（graph / orchestrator）:
  1. now = get_cur_time()           ← 代码直接调，不走 LLM
  2. loc = get_cur_loc()            ← 代码直接调，不走 LLM
  3. weather = get_weather(loc)     ← 代码直接调，不走 LLM
  4. 把结果拼进 prompt

LLM 看到的 prompt:
  当前环境：
    - 日期：2026-05-28（星期三）
    - 当前时间：15:30
    - 默认位置：北京市朝阳区建国路88号（国贸附近）
    - 当前天气：晴朗，24°C

  用户输入：今天下午想带老婆孩子出去玩 4 小时

  请从用户输入中提取意图和约束...
```

这样 LLM 看到环境信息后可以从"今天下午"直接解析出 `date_text="今天"` + `time_text="下午"`，不需要为"今天几号"浪费一轮 tool calling。

**覆盖规则：用户明确说的优先，由 Enrichment 负责合并。**

- 用户说"明天去海淀" → Enrichment 用"明天"覆盖今天的日期，用"海淀"覆盖默认的朝阳区
- 用户说"下午2点" → Enrichment 用 14:00 覆盖默认时间窗起点
- Router 不需要处理覆盖逻辑——它只如实抽取用户原话里的信息

**对比旧方案（SlotAgent ReAct）：**

```text
旧: LLM 驱动工具调用                  新: 代码驱动工具调用
─────────────────────                ─────────────────────
LLM: "我需要知道今天几号"             代码: now = get_cur_time()
  → tool: get_cur_time              代码: loc = get_cur_loc()
LLM: "我还需要位置"                   代码: weather = get_weather(loc)
  → tool: get_cur_loc               代码: 拼进 prompt
LLM: "再查天气"                       LLM: 一次性看到所有环境信息
  → tool: get_cur_weather              → 只做抽取，不调工具
LLM: 现在可以抽取了

LLM 推理轮次: 3-4 次                  LLM 推理轮次: 1 次
工具调用次数: 3（LLM 发起）            工具调用次数: 3（代码发起，结果相同）
```

工具还是那三个工具，区别仅在于**谁来决定调用的时机**。

### 输入

```json
{
  "user_input": "今天下午想带老婆孩子出去玩 4 小时，别太远，老婆最近减肥，孩子 5 岁",
  "context": {
    "has_plans": false,
    "prev_intent": "",
    "prev_slot": {},
    "prev_plans_summary": "",
    "last_question": ""
  }
}
```

上下文建议尽量精简，不要把完整旧方案塞给 RouterExtractor。它只需要知道：

- 当前是否已有方案
- 上一轮是否在等用户补充
- 用户是否可能是在选择/修改方案
- 上一轮关键摘要

### 输出

Router 输出统一 JSON。`raw_constraints` 保持扁平，置信度和证据以两个顶层辅助 map 存在——Gate 读取零成本，prompt 复杂度不增加：

```json
{
  "intent": "plan_outing",
  "intents": {
    "plan_outing": 0.95,
    "check_weather": 0.45
  },
  "selected_index": -2,
  "raw_constraints": {
    "date_text": "今天",
    "time_text": "下午",
    "duration_minutes": 240,
    "location_text": "",
    "companions": {
      "adults": 2,
      "children": 1,
      "child_age": 5,
      "members": ["self", "spouse", "child"]
    },
    "budget_text": "",
    "budget_per_person": null,
    "max_distance_text": "别太远",
    "max_distance_km": null,
    "preferences": ["亲子", "轻松"],
    "diet_tags": ["低卡", "健康", "减脂"],
    "scene_tags": ["家庭"],
    "avoid": ["距离太远", "排队太久"],
    "specific_resource": "",
    "requires_execution": false
  },
  "extraction_confidence": {
    "companions": 0.9,
    "child_age": 0.9,
    "max_distance": 0.4
  },
  "evidence_map": {
    "companions": "老婆孩子",
    "child_age": "孩子 5 岁",
    "max_distance": "别离家太远"
  },
  "reply": ""
}
```

**为什么用顶层 map 而不是字段内嵌：**

```text
不推荐（嵌套）:                      推荐（并行 map）:
companions: {                       raw_constraints: { companions: {...} }
  adults: 2,                        extraction_confidence: { companions: 0.9 }
  confidence: 0.9,                  evidence_map: { companions: "老婆孩子" }
  evidence: "老婆孩子"
}
→ 每个字段都多两层，阅读疲劳          → raw_constraints 结构不变，Gate 按需查辅助 map
```

**置信度指南：**

- `0.9+`：原文明确提及，如"老婆孩子，孩子5岁"
- `0.5-0.9`：可从上下文推断，如"一家人"→ 至少2大1小
- `0.3-0.5`：模糊暗示，如"几个兄弟"→ 大概3-5人
- `0-0.3` 或不存在：纯默认，不应出现在 map 中

**Gate 如何使用置信度：**

```text
字段无值 且 不可默认 → blocking → 反问
字段有值 且 confidence >= 0.5 → 直接用
字段有值 但 confidence < 0.5 → 标记为 assumption 说明"推测"
字段无值 但 evidence 可推断 → 用默认值 + assumption 说明
```

示例：

```text
场景 A: "老婆孩子，孩子5岁"
  → companions 有值, confidence=0.9 → 直接用，不反问

场景 B: "带小朋友去"
  → children=1 有值, child_age=null, extraction_confidence 无 child_age
  → Gate: child_age 缺失但 companions 低置信度 → 反问"孩子大概几岁？"

场景 C: "几个兄弟聚一下"
  → adults=null, evidence="几个兄弟"
  → Gate: 默认为4人，assumption 附上"按4人默认规划"
```

### 意图枚举

建议保留当前意图类型，但语义要更明确：

```text
plan_outing
find_activity
check_weather
refine_plan
confirm_execution
cancel_execution
chitchat
```

说明：

- `plan_outing`：完整或半完整的出行规划。
- `find_activity`：只找某类资源，不一定生成完整路线。
- `check_weather`：只查天气或天气作为主要问题。
- `refine_plan`：已有方案后，用户要求调整。
- `confirm_execution`：用户确认某个方案并希望执行。
- `cancel_execution`：用户明确不执行或取消。
- `chitchat`：闲聊。

### selected_index 规则

```text
0   选择第一个方案 / 方案A
1   选择第二个方案 / 方案B
2   选择第三个方案 / 方案C
-1  不执行 / 算了 / 不订了
-2  非选择意图，或无法判断选择哪个
-3  选择最后一个方案
```

### RouterExtractor Prompt 设计要点

Prompt 应强调：

1. 只做识别和抽取，不做规划。
2. 不要编造天气、位置、餐厅、活动。
3. 没说的信息保持 `null` 或空值，不要强行填。
4. 对“别太远”“预算别太高”这类模糊表达，保留原文并给出可选解析，但最终默认值由 Enrichment Service 决定。
5. 输出必须是纯 JSON。

### 为什么合并 Intent 和 Slot

合并后的优势：

- 同一句话只理解一次。
- 意图和约束天然一致，减少前后矛盾。
- 减少一次或多次 LLM 调用。
- 后续反问可以基于统一结构判断。
- 更适合做多轮：用户补充信息可以直接合并到 constraints。

潜在代价：

- RouterExtractor prompt 会比原 IntentAgent 更长。
- 输出结构更复杂，JSON 解析要求更高。
- 如果用户只闲聊，也会经过一个稍大的解析 prompt。

应对方式：

- 使用低温模型。
- 使用 JSON schema 或强格式 prompt。
- 对闲聊场景允许快速输出最小 JSON。

## 4.2 Enrichment Service

### 职责

`Enrichment Service` 是纯代码模块，负责把 RouterExtractor 输出的 `raw_constraints` 补全为 Planner 可用的标准 `constraints`。

它负责：

- 解析相对日期和时间文本
- 获取当前时间
- 获取默认位置
- 获取天气
- 填充默认预算
- 填充默认距离
- 推断标准人群结构
- 标准化偏好标签
- 构造 Planner 所需的 `time_window`、`party_profile`、`diet_tags`、`scene_tags`

它不负责：

- 判断用户是不是闲聊
- 生成方案
- 反问用户
- 自己调用 LLM

### 输入

```json
{
  "intent": "plan_outing",
  "raw_constraints": {
    "date_text": "今天",
    "time_text": "下午",
    "duration_minutes": 240,
    "companions": {
      "adults": 2,
      "children": 1,
      "child_age": 5
    },
    "budget_text": "",
    "budget_per_person": null,
    "max_distance_text": "别太远",
    "max_distance_km": null,
    "preferences": ["亲子", "轻松"],
    "diet_tags": ["低卡", "健康"],
    "scene_tags": ["家庭"]
  }
}
```

### 输出

```json
{
  "constraints": {
    "date": "2026-05-28",
    "time_window": {
      "date": "2026-05-28",
      "start": "14:00",
      "end": "18:00"
    },
    "location": {
      "address": "北京市朝阳区建国路 88 号",
      "lat": 39.9087,
      "lng": 116.4713,
      "district": "朝阳区"
    },
    "weather": {
      "condition": "cloudy",
      "temperature": "24C",
      "rain_probability": 0.3
    },
    "party_profile": {
      "adults": 2,
      "children": 1,
      "child_age": 5,
      "total_people": 3
    },
    "budget_per_person": 120,
    "max_distance_km": 8,
    "preferences": ["亲子", "轻松", "低体力消耗"],
    "diet_tags": ["低卡", "健康", "儿童餐"],
    "scene_tags": ["家庭"],
    "avoid": ["距离太远", "排队太久"]
  },
  "assumptions": [
    "未指定出发位置，默认使用当前位置。",
    "未指定预算，默认按中等预算人均 120 元规划。",
    "“别太远”按 8km 内处理。"
  ],
  "enrichment_warnings": []
}
```

### 默认值建议

#### 位置

```text
用户明确给位置 -> 使用用户位置
用户没给位置 -> 使用 get_cur_loc() 默认位置
```

第一阶段可以固定为北京朝阳区国贸附近。

#### 日期

```text
今天 -> 当前日期
明天 -> 当前日期 + 1
这周六/周日 -> 最近的对应周末
没说日期 -> 默认最近的周末；如果用户说“今天下午”，按今天
```

#### 时间窗

```text
上午 -> 09:00-12:00
中午 -> 11:30-14:00
下午 -> 14:00-18:00
晚上 -> 18:00-22:00
半天 -> 4 小时
一天 -> 8 小时
没说时间 -> 周末下午 14:00-18:00
```

#### 预算

```text
没说预算 -> 中等预算，人均 120
预算低/便宜 -> 人均 80
预算中等 -> 人均 120
预算高/体验好 -> 人均 200
明确总预算 -> 结合人数换算成人均
```

#### 距离

```text
别太远/附近 -> 8km
很近/走路 -> 3km
远点也行 -> 15km
没说距离 -> 8km
```

#### 饮食标签

可做简单规则映射：

```text
减肥/减脂/清淡 -> 低卡、健康、少油
带孩子 -> 儿童餐、家庭
约会 -> 安静、环境好
朋友聚会 -> 聚餐、热闹、多人
```

### 天气获取策略

第一阶段可以有两种实现：

```text
MockWeatherProvider
RealWeatherProvider
```

对外统一：

```text
get_weather(location, date)
```

如果当前天气工具很慢，建议先做缓存：

```text
cache_key = date + lat + lng
```

同一轮规划中不要重复查天气。

### Enrichment 的关键价值

它把“默认策略”从 LLM prompt 中拿出来，变成可测试、可解释、可调的规则。

例如用户没说预算时，不需要问：

> 您的预算是多少？

而是默认：

> 我先按中等预算人均 120 元规划。

这会显著减少无意义反问。

## 4.3 NeedQuestion Gate

### 职责

`NeedQuestion Gate` 是确定性规则模块，负责判断：

```text
当前信息是否足够进入 Planner？
如果不够，应该问用户哪一个问题？
```

它不依赖 LLM。

### 核心原则

```text
只问关键缺口。
非关键缺口用默认值。
一次只问一个最重要的问题。
```

### 输入

```json
{
  "intent": "plan_outing",
  "constraints": {...},
  "raw_constraints": {...},
  "assumptions": [...]
}
```

### 输出

```json
{
  "need_question": false,
  "question": "",
  "missing_fields": [],
  "severity": "none"
}
```

如果需要反问：

```json
{
  "need_question": true,
  "question": "你们大概几个人出行？需要我按几个人来安排订座和门票？",
  "missing_fields": ["party_profile.total_people"],
  "severity": "blocking"
}
```

### 字段分级

#### Blocking：必须问

以下情况建议反问：

1. **预约/下单意图明确，但人数未知**
   例如用户说“帮我订个餐厅”，但没说几个人。

2. **用户要求严格预算，但没有数值**
   例如“千万别超预算”，但完全没说预算是多少。

3. **用户指定城市/区域缺失且默认位置不可信**
   如果系统没有默认位置，也没有用户画像。

4. **用户要修改已有方案，但没有指出修改方向**
   例如“这个不太行”，但没说哪里不行。

5. **执行确认不明确**
   例如“可以吧”，但无法判断选择哪个方案。

#### Defaultable：可以默认

以下情况不建议反问：

```text
预算没说 -> 默认中等预算
距离没说 -> 默认 8km
时间没说 -> 默认最近周末下午
交通方式没说 -> 默认打车/步行组合
餐饮偏好没说 -> 根据场景推断
天气没说 -> 工具查询
当前位置没说 -> 默认当前位置
```

#### Optional：不影响规划

```text
是否需要停车
是否需要儿童椅
是否要包间
是否有忌口
是否要甜品/鲜花
是否要避开人多
```

这些可以在方案里通过假设或候选差异体现，不必一开始阻塞。

### 不同意图的 Gate 规则

#### chitchat

```text
不进入 Enrichment，不反问，直接返回 reply。
```

#### check_weather

最低需要：

```text
date
location
```

如果系统有默认位置和默认日期，就不反问。

#### find_activity

最低需要：

```text
resource_type 或 preference
location
date/time 可默认
```

如果用户只说“推荐个餐厅”，可以默认位置和晚餐时间，不必反问。

#### plan_outing

最低需要：

```text
date/time_window
location
party_profile
```

但其中 date/time/location 都可以默认。真正常见的 blocking 字段通常是人数，尤其涉及订座、订票时。

#### refine_plan

需要：

```text
已有方案
修改方向
```

如果用户只说“换一个”，可以反问：

> 你想主要换活动、餐厅，还是希望整体重新安排？

#### confirm_execution

需要：

```text
已有方案
selected_index
```

如果有多个方案但 selected_index = -2，需要反问：

> 你想执行哪个方案？可以说”第一个”或”方案A”。

## 4.4 Disambiguator（反问措辞）

### 职责

当 Gate 判断 `need_question = true` 时，Gate 只输出结构化信息（`missing_fields`、`severity`），**不拼自然语言问题字符串**。反问的措辞交给 `Disambiguator` 生成。

### 为什么需要

Gate 拼出来的问题往往是模板化的表单校验语气：

```text
Gate 直接拼: “请问出行人数是多少？请提供预算信息。”
```

而 Disambiguator 拿到上下文后可以生成更自然的反问：

```text
Disambiguator: “对了，你们一共几个人呀？我好帮你安排座位和门票~”
```

这对黑客松 Demo 的体验很重要。

### 实现

Disambiguator 是一次极短的 LLM 调用（不需要工具，不需要 chain），输入极简：

```python
def disambiguate(gate_output: dict, context: dict) -> str:
    prompt = f”””你是活动规划助手。用户正在规划周末出行。你需要用一句自然的话向用户了解缺失的信息。

上下文：
  用户需求：{context.get('user_input', '')}
  当前已知：{json.dumps(context.get('constraints', {}), ensure_ascii=False)}
  缺失信息：{gate_output.get('missing_fields', [])}

要求：
  - 一句话问完，不要分段
  - 语气友好自然，像朋友聊天
  - 不要列清单
  - 如果有系统默认值可以先告知，再确认

示例：
  缺人数 → “对了，一共几个人一起去呀？”
  缺预算 → “预算方面你有什么想法吗？没有的话我先按人均120帮你安排~”
  两个都缺 → “你们几个人一起？预算有要求吗？没有的话我先按人均120安排～”
“””
    return llm.invoke(prompt)
```

### 复杂度对比

```
Disambiguator 的 LLM 调用:
  - 输入: ~200 tokens
  - 输出: 一句话 (~30 tokens)
  - 耗时: <1s
  - 工具: 无
  - 温度: 0.3（可以有点变化）

SlotAgent 的 LLM 调用（对比）:
  - 输入: 整个 ReAct 循环的 messages
  - 输出: 分析 + JSON + 反问
  - 耗时: 10-15s
  - 工具: 3 个
  - 温度: 0.0
```

**Phase 1 可以先用模板句**（`f”请问{missing_fields}？”`），Phase 2 再接入 Disambiguator。不影响架构其他部分。

### 在 Graph 中的位置

```
Gate
  ├─ need_question = false → Planner
  └─ need_question = true  → Disambiguator → interrupt(question)
                                                      ↓
                                              用户回答
                                                      ↓
                                              RouterExtractor
                                             （合并上下文重新抽取）
```

## 5. 新 State 设计

建议 Graph State 调整为：

```python
class State(TypedDict):
    user_input: str

    intent: str
    intents: dict
    selected_index: int
    reply: str

    raw_constraints: dict
    extraction_confidence: dict   # 新增: 字段级置信度
    evidence_map: dict             # 新增: 字段原文证据

    constraints: dict
    assumptions: list[str]

    need_question: bool
    question: str
    missing_fields: list[str]

    plans: dict
    execution: dict
```

其中：

- `raw_constraints`：LLM 原始抽取结果。
- `extraction_confidence`：各字段抽取置信度（Gate 用于判断是否该问）。
- `evidence_map`：各字段在用户原话中的原文证据。
- `constraints`：Enrichment 后的标准结构。
- `assumptions`：系统默认假设，用于最后展示给用户。
- `need_question`：Gate 结果。
- `question`：Disambiguator 生成的自然语言反问。如果需要 interrupt，就问这个。

## 6. 新 Graph 流程

建议流程：

```text
router_extractor
  -> route_after_router

route_after_router:
  chitchat -> END
  confirm_execution -> executor
  cancel_execution -> END
  otherwise -> enrichment

enrichment
  -> need_question_gate

need_question_gate:
  need_question = true -> disambiguator -> interrupt -> router_extractor
  need_question = false -> planner

planner -> END
executor -> END
```

更细的版本：

```text
User
  -> [代码: get_cur_time + get_cur_loc + get_weather]
       ↓ 注入环境信息到 prompt
  -> RouterExtractor (1 LLM)
       output: intent + raw_constraints + extraction_confidence + evidence_map
  -> EnrichmentService (0 LLM)
       output: constraints + assumptions
  -> NeedQuestionGate (0 LLM)
       if blocking missing:
         -> Disambiguator (1 短 LLM, 可选, Phase 1 可用模板)
              output: 自然语言反问
         -> interrupt(question)
         -> merge user answer into user_input
         -> 回到 RouterExtractor（合并上下文重新抽取）
       else:
         -> Planner
```

### 用户补充后的处理

当 Gate 触发反问后，用户补充信息可能只是一句话：

```text
“三个人，一个 5 岁孩子”
```

建议处理方式：

```text
原始 user_input 保留
last_question 保留
user_answer 作为补充输入
RouterExtractor 看到上下文后合并抽取
```

可以构造：

```text
原始需求：今天下午想出去玩，别太远
系统追问：你们几个人出行？
用户补充：三个人，一个 5 岁孩子
```

让 RouterExtractor 输出合并后的 `raw_constraints`。

## 7. 和现有模块的迁移关系

### 可以替换的部分

现有：

```text
IntentAgent.intent_agent.classify_intent()
SlotAgent.slot_agent.run_slot()
```

可替换为：

```text
RouterExtractor.run_router_extractor()
EnrichmentService.enrich_constraints()
NeedQuestionGate.evaluate()
```

### 可以保留的部分

暂时保留：

```text
PlannerAgent.run_planner()
ExecutorAgent.run_executor()
services/*
Tools.py
```

后续再优化 Planner。

### 迁移顺序

推荐分三步：

1. 新增 RouterExtractor（含环境预注入 + extraction_confidence + evidence_map），但不接入 Graph，先用测试脚本验证输出。
2. 新增 Enrichment 和 Gate，验证它们能产出现有 Planner 可用的 `slot/constraints`。
3. 新增 Disambiguator（或先用模板），修改 `Agents/graph.py`，把 `intent -> slot` 替换为 `router -> enrichment -> gate -> (disambiguator)`。

## 8. 与 Planner 的数据兼容

当前 Planner 接收：

```python
run_planner(user_input, intent, intents, slots, prev_plans=None, prev_execution=None)
```

新结构中可以短期把 `constraints` 包装成旧的 `slot`：

```json
{
  "date": "2026-05-28",
  "location": {...},
  "weather": {...},
  "companions": {
    "count": 3,
    "adults": 2,
    "children": 1,
    "child_age": 5
  },
  "budget": "中等",
  "preferences": {...},
  "time_hint": "14:00-18:00",
  "is_complete": true,
  "ask_question": ""
}
```

长期建议 Planner 改为直接接收标准 `constraints`：

```json
{
  "location": {...},
  "time_window": {...},
  "party_profile": {...},
  "budget_per_person": 120,
  "max_distance_km": 8,
  "preferences": [...],
  "diet_tags": [...],
  "scene_tags": [...],
  "avoid": [...],
  "weather": {...}
}
```

## 9. 示例流程

### 输入

```text
今天下午想带老婆孩子出去玩 4 小时，别太远，老婆最近减肥，孩子 5 岁
```

### RouterExtractor 输出

```json
{
  "intent": "plan_outing",
  "intents": {
    "plan_outing": 0.95
  },
  "selected_index": -2,
  "raw_constraints": {
    "date_text": "今天",
    "time_text": "下午",
    "duration_minutes": 240,
    "companions": {
      "adults": 2,
      "children": 1,
      "child_age": 5
    },
    "preferences": ["亲子", "轻松"],
    "diet_tags": ["低卡", "健康"],
    "scene_tags": ["家庭"],
    "max_distance_text": "别太远",
    "max_distance_km": null
  },
  "extraction_confidence": {
    "companions": 0.9,
    "child_age": 0.9,
    "max_distance": 0.4
  },
  "evidence_map": {
    "companions": "老婆孩子",
    "child_age": "孩子 5 岁",
    "max_distance": "别离家太远"
  },
  "reply": ""
}
```

### Enrichment 输出

```json
{
  "constraints": {
    "date": "2026-05-28",
    "time_window": {
      "date": "2026-05-28",
      "start": "14:00",
      "end": "18:00"
    },
    "location": {
      "address": "北京市朝阳区建国路 88 号",
      "lat": 39.9087,
      "lng": 116.4713
    },
    "party_profile": {
      "adults": 2,
      "children": 1,
      "child_age": 5,
      "total_people": 3
    },
    "budget_per_person": 120,
    "max_distance_km": 8,
    "preferences": ["亲子", "轻松", "低体力消耗"],
    "diet_tags": ["低卡", "健康", "儿童餐"],
    "scene_tags": ["家庭"]
  },
  "assumptions": [
    "未指定预算，默认按中等预算人均 120 元规划。",
    "“别太远”按 8km 内处理。"
  ]
}
```

### Gate 输出

```json
{
  "need_question": false,
  "question": "",
  "missing_fields": []
}
```

置信度检查结果：`companions=0.9`、`child_age=0.9` 均高于 0.5 阈值，无需反问。进入 Planner。

### Disambiguator 输出（仅在 Gate 判断 need_question=true 时触发）

假设用户输入变为 "带小朋友出去玩一下"，Router 输出 `children=1, child_age=null`，Gate 判断 `child_age` 缺失 → blocking：

```json
{
  "need_question": true,
  "missing_fields": ["child_age"],
  "severity": "blocking"
}
```

Disambiguator 将结构化信息转为自然反问：

> "孩子大概多大呀？我好帮你挑适合他年龄的活动~"

## 10. 反问示例

### 场景：确认执行但未选择方案

输入：

```text
就这个吧，帮我订
```

如果当前有多个方案，但 RouterExtractor 无法判断 `selected_index`：

```json
{
  "need_question": true,
  "question": "你想执行哪个方案？可以说“第一个”“方案A”或“最后一个”。",
  "missing_fields": ["selected_index"],
  "severity": "blocking"
}
```

### 场景：严格预算但没有金额

输入：

```text
帮我安排一个周末约会，但千万别超预算
```

如果没有用户画像里的预算：

```json
{
  "need_question": true,
  "question": "你的预算大概是多少？可以告诉我总预算或人均预算。",
  "missing_fields": ["budget_per_person"],
  "severity": "blocking"
}
```

### 场景：普通规划缺预算

输入：

```text
周末想和朋友出去玩一下
```

不反问，默认：

```text
人均 120，附近 8km，周末下午。
```

## 11. 测试建议

至少准备以下测试：

1. 家庭亲子完整输入（”老婆孩子，孩子5岁”），不应反问，companions 置信度 > 0.5。
2. 朋友聚会缺预算，不应反问，自动默认 120。
3. 确认执行但没选方案，应反问（selected_index = -2）。
4. 已有方案后用户说”太贵了”，应识别为 `refine_plan`。
5. 用户说”算了不订了”，应识别为 `cancel_execution` 或 `confirm_execution + selected_index=-1`。
6. 用户只问天气，应只补 location/date/weather，不进入 Planner。
7. 用户补充回答”3个人，一个孩子”，应能合并到原始约束。
8. 模糊输入”带小朋友去”，child_age 置信度低或缺失 → Gate 应触发反问。
9. 模糊输入”几个兄弟聚一下”，adults 无值但 evidence 可推断 → 默认 4 人，不反问。

## 12. 风险与注意点

### 12.1 RouterExtractor 输出结构变复杂

需要做好 JSON 解析失败兜底。建议：

- 提取首尾 `{}`。
- 解析失败时返回 `chitchat` 或 `unknown`。
- 不要用第二次 LLM 修 JSON 作为常规路径，否则又会变慢。

### 12.2 默认值不能悄悄影响用户

所有默认值应进入 `assumptions`，最终展示时可以轻量说明：

```text
我先按当前位置、下午 14:00-18:00、人均 120 元来安排。
```

### 12.3 Gate 不要过度保守

如果 Gate 过度反问，就会回到旧问题。建议默认策略偏向：

```text
先规划，再允许用户修改。
```

### 12.4 Planner 接口需要逐步统一

短期兼容旧 `slot` 可以减少改动，但长期最好统一到 `constraints`，否则系统会同时维护两套语义。

## 13. 总结

新的前三部分可以概括为：

```text
RouterExtractor：LLM 做一次语义解析。
Enrichment Service：代码补全环境信息和默认值。
NeedQuestion Gate：代码判断是否真的需要反问。
```

它的核心收益是：

- 减少 LLM 调用次数
- 降低 Slot 阶段耗时
- 减少无意义反问
- 提高输出稳定性
- 为后续 service-first Planner 打基础

这不是简单地把 Intent 和 Slot 拼在一起，而是把“语言理解”和“系统补全/规则判断”分离开。大模型只做它擅长的语义理解，代码负责确定性的业务规则。

# RouterExtractor 面试问答

_归档状态：旧实现对应的问答 · 部分工具链路和性能数字没有当前证据_

---

> ⚠️ **不要用于当前面试答案：** 文中“固定注入天气、位置”和延迟数字与当前 M1 证据不一致。请从 [`../../learning/milestones/m1_entry_loop.md`](../../learning/milestones/m1_entry_loop.md) 和当前测试重新整理回答。

## Q1: 为什么不用 ReAct Agent 做意图+约束抽取？

**问法变体:** "你之前用 LangGraph 的 create_react_agent 做 Slot，为什么后来改成单次 LLM 调用？ReAct 不是更灵活吗？"

**答:**

ReAct 适合"LLM 需要在不确定的环境中探索"的场景，比如先搜活动、根据结果决定要不要调路线估算。但时间、位置、天气这三个工具**每次都会被调、且顺序固定**，没有探索空间。

实测 ReAct 为了这三个固定调用消耗了 3 轮 LLM 推理，每次都要重新读上下文、重新理解、决定下一步。改成代码提前调并把结果注入 prompt，省掉了这 3 轮，延迟从 13s 降到 5s。

**核心判断:** 如果工具调用顺序是可预知的，就应该由代码驱动而非 LLM 驱动。ReAct 的价值在于"下一步不确定"时。

---

## Q2: 环境信息预注入具体怎么做的？

**问法变体:** "你说 LLM 不调工具，那时间、位置、天气怎么获取的？"

**答:**

在构造 LLM prompt 之前，代码直接调三个工具拿到结果，拼成一个环境信息块放到用户消息前面（不是 System Prompt 里，是 HumanMessage 里）：

```
调用的代码（router_extractor.py 的 _collect_env_info）:
  1. get_cur_time.invoke({})  → {date_iso: "2026-05-28", weekday: "星期三", ...}
  2. get_cur_loc.invoke({})   → {address: "北京市朝阳区建国路88号", lat: 39.9, ...}
  3. get_weather.invoke({latitude: "39.9087", longitude: "116.4713"}) → "Sunny, 24°C"

拼成:
  当前环境:
    - 日期：2026-05-28（星期三）
    - 当前时间：15:30
    - 默认位置：北京市朝阳区建国路88号（朝阳区）
    - 天气：Sunny，24

  用户输入：今天下午想带老婆孩子出去玩4小时...
```

这样就变成了一次普通的 LLM 调用——没有 tool calling，没有多轮循环。LLM 看到环境信息后从"今天下午"知道是今天，从"别太远"保留原文留给 Enrichment 处理。

**天气失败不影响流程** —— try/except 包着，失败了就写"未获取"，不影响意图分类和约束抽取。

---

## Q3: 合并 Intent + Slot 不会让 prompt 太长、JSON 太复杂吗？

**问法变体:** "原来 Intent 只输出一个枚举，现在要输出完整的约束 JSON，LLM 会不会更容易出错？"

**答:**

复杂度确实增加了——prompt 从 20 行到了 40 行，JSON 从 4 个字段到了嵌套结构。但有两件事保证了稳定性：

1. **温度 = 0.0 + DeepSeek 的 thinking disabled。** 低温 + 无推理链，输出是确定性的。10 个场景跑下来 37/37 断言全部通过。

2. **两层 JSON 解析兜底。** 第一层提取 `{}` 直接 `json.loads`。失败了进第二层——用一次短 LLM 修复格式不正确的 JSON。再失败就 fallback 到 `intent=chitchat`。实际测试中从未触发修复层。

3. **闲聊快速短路。** 纯闲聊时 `raw_constraints` 只需要输出 `{}`，不需要走完整的约束抽取逻辑。Prompt 里声明了这点，LLM 能做到。

---

## ==Q4: extraction_confidence 和 evidence_map 有什么用？==

**问法变体:** "为什么需要置信度？直接抽约束不就够了吗？"

**答:**

举个例子："几个兄弟聚一下吃顿饭"。Router 抽出来的 `companions.adults` 是 null——因为"几个"不是数字。但 `evidence_map` 里有 `"companions": "几个兄弟"`。

没有置信度的 Gate：字段为空 → blocking → 反问"几个人？"

有置信度的 Gate：看到 evidence 存在、虽然无精确值但场景可推断 → 默认 4 人 → 在 assumptions 里说明"按 4 人默认规划，可调整" → 不反问。

**置信度让 Gate 从"字段有无"的二元判断升级到"信息可靠度"的三元判断：高置信直接用、低置信默认值兜底 + 说明、无证据才问。** ==这是减少无意义反问的关键设计。==

---

## Q5: 如果用户输入特别模糊，这套设计会不会漏掉关键信息？

**问法变体:** "比如用户就说'出去玩'，你怎么办？"

**答:**

Router 的输出会是：
```json
{
  "intent": "plan_outing",
  "raw_constraints": {
    "preferences": ["出去玩"]
  },
  "extraction_confidence": {},
  "evidence_map": {}
}
```

约束几乎是空的，置信度也是空的。但这不是 Router 的失败——Router 的职责就是"如实抽取，没说的不编造"。空信息会流到 Enrichment（填默认值：周末下午、人均 120、8km 内）→ Gate（检查 blocking 字段：时间有默认、位置有默认、人数未知）→ Gate 判断 party_size 缺失 → 反问。

整个流程仍然能产生方案，只是会多一次反问。**模糊输入导致反问是合理的，不应该为了"不问"而编造信息。**

---

## Q6: 如果要在生产环境扩展这个设计，你会怎么做？

**答:**

三点：

1. **加用户记忆（Memory Service）。** 用户上次说了"老婆孩子，孩子5岁"，下次 Enrichment 直接读取 profile，不需要 Router 反复抽。记忆让默认值从"系统规则"升级到"个人画像"，反问频率进一步降低。

2. **Router 改 JSON Mode。** 目前用 prompt 约束 JSON 格式，但如果模型支持 structured output / JSON mode，改过去会更稳定——不再需要 _parse_result 里的两层兜底。

3. **把置信度做成可观测指标。** 记录每次抽取的 confidence 分布，如果发现某类字段（如 child_age）长期低置信度 → 优化 prompt 中的对应抽取指引；如果某字段置信度经常虚高但实际抽错 → 调整置信度描述让 LLM 更保守。

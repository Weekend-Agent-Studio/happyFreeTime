# RouterExtractor：意图识别与约束抽取合并设计

_归档状态：早期紧凑设计稿 · 部分职责与当前实现不一致_

---

> ⚠️ **历史文档：** 文中的环境信息注入和职责边界不再是当前契约。请以 `app/services/router_extractor.py`、自动测试和 [`../../learning/milestones/m1_entry_loop.md`](../../learning/milestones/m1_entry_loop.md) 为准。

## 背景问题

原架构用两个 Agent 串联：

```
IntentAgent → SlotAgent
  (1次LLM)     (3-5次LLM，ReAct工具调用)
```

实测暴露了两个问题：

1. **重复理解。** Intent 跑一次 LLM 判"这是规划请求"，Slot 再跑多次 LLM 重新读同一句话抽约束。同一句用户输入被理解了两遍。

2. **LLM 干了机械活。** Slot 用 ReAct loop 调 `get_cur_time`、`get_cur_loc`、`get_weather`。每次 tool calling 触发一整轮 LLM 推理——"先查时间→看到结果→再查位置→看到结果→再查天气→好了现在可以抽了"。三个确定性函数调用变成了 3 次 LLM，增加 ~10s 延迟。

## 解决思路

**合并 Intent + Slot 为一次 LLM 调用，环境信息由代码提前注入，不走 tool calling。**

```
[代码: get_cur_time + get_cur_loc + get_weather]  ← 0次LLM
                    ↓ 结果注入 prompt
           RouterExtractor (1次LLM)
```

LLM 直接看到完整的环境上下文，一次性完成意图分类 + 约束抽取，不需要思考"我应该调哪个工具"。

## 输出设计

三个关键设计决策：

### 1. raw_constraints 只存"用户说了什么"

LLM 不填默认值、不猜数字。用户说"几个兄弟"就原样保留，不做推断。默认值填充交给下游 Enrichment（代码规则）。

### 2. 置信度 + 证据用顶层并行 map，不做字段内嵌

```json
{
  "raw_constraints": {
    "companions": {"adults": 2, "children": 1, "child_age": 5}
  },
  "extraction_confidence": {
    "companions": 0.95,
    "child_age": 0.9
  },
  "evidence_map": {
    "companions": "老婆孩子",
    "child_age": "孩子5岁"
  }
}
```

不做 `"companions": {"adults": 2, "confidence": 0.9, "evidence": "..."}` —— 每个字段多两层嵌套，阅读疲劳。并行 map 的好处：`raw_constraints` 结构保持扁平，Gate 按需查询置信度。

置信度分档：
- `0.9+` 原文明确提及（"老婆孩子，孩子5岁"）
- `0.5-0.9` 可推断（"一家人" → 至少2大1小）
- `0.3-0.5` 模糊暗示（"几个兄弟"）

Gate 使用规则：字段有值且 confidence >= 0.5 → 直接用；有值但 confidence < 0.5 → 标记为 assumption；无值但有 evidence → 用默认值 + assumption 说明；无值且不可默认 → blocking 反问。

### 3. 环境预注入，而非 tool calling

工具还是那三个，区别只在于**谁决定调用时机**：

```
旧（LLM驱动）:                    新（代码驱动）:

LLM: "我需要知道今天几号"          代码: now = get_cur_time()
  → tool: get_cur_time           代码: loc = get_cur_loc()
LLM: "还需要位置"                  代码: weather = get_weather(loc)
  → tool: get_cur_loc            代码: 拼进 prompt
LLM: "还有天气"                    LLM: 一次看到所有环境信息
  → tool: get_cur_weather           → 只做抽取，不调工具
LLM: 现在可以抽取了

LLM调用: 3-4次                    LLM调用: 1次
```

## 实测对比

| 指标 | 旧 (Intent+Slot) | 新 (RouterExtractor) |
|------|-------------------|---------------------|
| Planner 前 LLM 调用次数 | 4-5 | 1 |
| 完整输入耗时 | ~13s | ~5s |
| 闲聊耗时 | ~3s | ~3s |
| 10场景意图准确率 | 未系统测试 | 100%（37/37断言） |
| JSON解析失败 | 无（ReAct output） | 两层兜底（提取+修复） |

## 容错设计

```
_parse_result(raw)
  → 解析成功 → 返回
  → 解析失败 → _repair_json(raw)
      → LLM修复成功 → 返回
      → 修复失败 → _fallback_result()
          → intent=chitchat, reply="不好意思我没太理解..."
```

## 与上下游的边界

```
RouterExtractor (LLM做语言理解)
  → 输出: raw_constraints（只说用户说了什么）
Enrichment (代码规则，0次LLM)
  → 输出: constraints（补全默认值+时间解析+天气标准化）
Gate (代码规则，0次LLM)
  → 输出: need_question / 放行
Planner
  → 生成方案
```

Router 只管"用户说了什么"，Enrichment 管"系统补什么"，Gate 管"要不要问"。三层职责清晰，各自独立可测。

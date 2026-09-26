# Graph、结构化输出与 Pydantic

_M1 基础学习笔记 · 内容需持续以当前代码和测试校准_

---

你现在混在一起的是三个不同层面的概念：

```
Graph：程序按照什么流程运行、节点之间传什么
结构化输出：LLM 如何按指定字段返回结果
Pydantic：Python 如何定义并校验这些字段
```

它们有关联，但不是同一个东西。

**先给结论**

我们确实需要结构化输出，尤其是 `RouterExtractor`。

Pydantic既可以用来定义 LLM 的结构化输出，也可以用来定义普通代码节点之间的数据契约。所以我之前说“它不只是结构化输出”，不是说它不用于结构化输出，而是说它的用途更广。

```
LLM 结构化输出
        ↓
Pydantic 校验
        ↓
放入 Graph State
        ↓
后续节点继续读取
```

**1. Graph 是怎么运行的**

LangGraph里有一个共享的 `State`。每个节点：

1. 读取 State 中自己需要的字段。
2. 完成自己的任务。
3. 返回需要新增或修改的字段。
4. LangGraph把返回值合并回 State。

简化示例：

```
class GraphState(TypedDict, total=False):
    user_input: str
    interpretation: Interpretation
    constraints: NormalizedConstraints
    question: QuestionDecision
    plans: list[Plan]
```

Router节点：

```
def router_node(state: GraphState):
    result = run_router(state["user_input"])
    return {"interpretation": result}
```

Enrichment节点：

```
def enrichment_node(state: GraphState):
    constraints = enrich(state["interpretation"])
    return {"constraints": constraints}
```

Graph运行过程中，State逐渐变化：

```
# 初始
{
    "user_input": "明天下午带5岁孩子出去玩，别太远"
}

# Router之后
{
    "user_input": "...",
    "interpretation": Interpretation(...)
}

# Enrichment之后
{
    "user_input": "...",
    "interpretation": Interpretation(...),
    "constraints": NormalizedConstraints(...)
}

# Gate之后
{
    ...,
    "question": QuestionDecision(should_ask=False)
}

# Planner之后
{
    ...,
    "plans": [Plan(...), Plan(...)]
}
```

所以不是把一个 State 变成四个独立 State，而是：

> 仍然只有一个共享 Graph State，但里面增加多个职责清晰的结构化字段。

**2. 什么是结构化输出**

普通 LLM 输出是一段文本：

```
用户想在明天下午带孩子出去玩，距离不要太远。
```

程序很难稳定地从这段话中获取字段。

结构化输出要求模型按照固定 Schema 返回：

```
{
  "intent": "plan_outing",
  "raw_constraints": {
    "date_text": "明天",
    "time_text": "下午",
    "child_age": 5,
    "max_distance_text": "别太远"
  }
}
```

最简单的做法是在 Prompt 里说“请返回 JSON”，但模型仍可能返回 Markdown、漏字段或者类型错误。

更可靠的方式是：

```
structured_llm = llm.with_structured_output(Interpretation)
result = structured_llm.invoke(messages)
```

这里 `Interpretation` 就可以是 Pydantic模型。

**3. 能否真正结构化，取决于谁**

取决于两部分：

```
模型服务负责：尽量按照 JSON Schema 生成
Pydantic负责：检查生成结果是否合法
```

如果模型 API 原生支持 JSON Schema、Structured Output或 Tool Calling，可靠性会比较高。

如果模型不支持，LangChain可能只是把 Schema 写进 Prompt，再解析模型返回的文本。这时仍然算结构化输出，但可靠性弱一些。

Pydantic不能控制模型的大脑。它只能在模型返回之后检查：

```
字段有没有缺失
类型是否正确
数字是否在合理范围
嵌套结构是否合法
```

因此准确说法是：

> 我们需要结构化输出；Pydantic负责定义和验证结构，最终生成可靠性还取决于模型及其 API 能力。

**4. TypedDict 和 BaseModel 有什么区别**

`TypedDict` 主要是“写给编辑器和类型检查工具看的”：

```
class UserData(TypedDict):
    age: int
```

运行时它仍然只是普通字典：

```
data = {"age": "五岁"}       # Python允许
data = {"agee": 5}          # 字段拼错也允许
```

Pydantic `BaseModel` 会在运行时检查：

```
class UserData(BaseModel):
    age: int
UserData(age="五岁")  # 立即报验证错误
UserData(agee=5)      # 缺少 age，立即报错
```

可以这样理解：

```
TypedDict：告诉开发者“理论上应该长这样”
BaseModel：实际检查“传进来的东西是否真的这样”
```

因此推荐：

```
最外层 GraphState：TypedDict
跨节点的复杂字段：Pydantic BaseModel
```

**5. 为什么要拆开原来的 slot**

原来的 `slot: dict` 同时装了很多不同性质的数据：

```
用户原话抽取结果
默认值
工具查询结果
是否完整
反问内容
给 Planner 的最终参数
```

新设计不是单纯为了区分“来源”，也是为了区分“处理阶段”：

```
Interpretation / RawConstraints
表示 Router 从用户表达中理解到了什么

EnvironmentContext
表示系统获得的当前时间、位置和天气

NormalizedConstraints
表示综合用户输入、环境、记忆和默认规则后，最终采用什么约束

QuestionDecision
表示这些约束是否足够，以及要不要反问

Plan
表示 Planner 最终生成的业务方案
```

这样 Router不会决定默认预算，Enrichment不会决定是否反问，Gate也不会自己修改约束。

**6. 一个模型里的字段能否来自不同节点**

可以，但要区分两个概念：

```
生产者：哪个节点最终创建这个对象
数据来源：对象中的值最初来自哪里
```

例如 `NormalizedConstraints` 由 Enrichment节点统一创建，但其中：

```
date：来自用户“明天”
location：来自系统默认位置
weather：来自高德
budget：来自默认规则
child_age：来自 Router 抽取
```

因此可以给字段记录来源：

```
class ConstraintValue(BaseModel):
    value: object
    source: str
    raw_text: str | None = None
```

这不代表五个节点同时写 `constraints`。更推荐由 Enrichment统一组装，避免多个节点争用同一个 State 字段。

如果多个节点确实写同一个字段：

- 普通字段通常是后写覆盖前写。
- `messages`、`trace` 等列表可以配置 reducer 进行追加。
- 并行节点同时写同一个无 reducer 字段，可能产生冲突。

最简单的心智模型是：

```
Graph State = 一份不断补充的流程档案
TypedDict = 档案有哪些栏目
Pydantic = 每个栏目必须遵守的表格格式
结构化输出 = 让 LLM 按表格填写，而不是写一篇自由作文
```

在这个项目里，只有 Router等 LLM 节点涉及“LLM结构化输出”；Enrichment、Gate、Planner虽然也返回 Pydantic对象，但那属于普通代码产生的结构化数据。

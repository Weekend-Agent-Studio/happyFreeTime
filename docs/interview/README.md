# HappyFreeTime 面试材料索引

_把 Resume V2 的代码证据、架构取舍和评测结果转化为可复述的系统设计。本文是学习入口，不是当前实现契约。_

---

## 📋 先建立当前版本概念

开始面试准备前，先阅读：

1. [Resume V2 当前架构](../current/resume_v2_architecture.md)
2. [Resume V2 发布评测](../releases/resume_v2_release_report.md)
3. 根目录 [README.md](../../README.md)

面试时只把当前架构和发布报告中有代码/测试/评测证据的内容说成“已实现”。目标架构、未来路线图和历史 V1 设计要明确使用“计划”“原型”或“历史版本”。

## 📚 当前材料

| 文件 | 主要内容 | 使用方式 |
| --- | --- | --- |
| [00_Graph和结构化输出和Pydantic.md](00_Graph和结构化输出和Pydantic.md) | Graph、结构化输出和 Pydantic | 学习基础链路 |
| [01_关于顶层的一些问答.md](01_关于顶层的一些问答.md) | 系统设计高频追问 | 题库，和 03 有部分重叠 |
| [02_项目的价值.md](02_项目的价值.md) | 项目定位、Agent Loop 和固定 Workflow 取舍 | 项目价值表达 |
| [03_设计阶段八股总结.md](03_设计阶段八股总结.md) | Graph、Provider、数据库、MCP、评测 | 基础知识主索引 |
| [04_测试与评测基础.md](04_测试与评测基础.md) | 测试层次、证据边界、Flaky 和 Eval | 测试追问 |
| [05_三阶段架构演进_从ReAct原型到受约束规划Agent.md](05_三阶段架构演进_从ReAct原型到受约束规划Agent.md) | V1 Multi-Agent → V2 可信内核 → 受约束语义决策 | **主讲稿** |
| [06_从关键词匹配到可验证语义规划与轻量RAG.md](06_从关键词匹配到可验证语义规划与轻量RAG.md) | 语义中间层、Hybrid Retrieval、Grounding、消融 | 语义与检索专题 |
| [07_从万能Interpretation到受约束WireProposal与CommandCompiler.md](07_从万能Interpretation到受约束WireProposal与CommandCompiler.md) | Wire Contract、Proposal/领域对象分离、CommandCompiler | Structured Output 专题 |
| [当前 Planner 全流程](v2版本的方案生成的全流程.md) | 规划输入、召回、Beam、路线、Verifier 和 Top 3 | 需先确认版本标记 |

## 🧭 推荐学习路径

### 第一阶段：能讲清楚一条请求

README → 当前架构 → 00 → 05 → 当前 Planner 全流程。

目标是能解释：

- 用户输入经过哪些节点；
- 什么时候会反问；
- PlanningIntent、PlanSpecCompiler 和 Planner 的边界；
- 路线和营业事实为什么由 Harness 复核。

### 第二阶段：能讲清楚为什么这样设计

06 → 07 → 03。

目标是能解释：

- 为什么不让 LLM 直接生成最终行程；
- 为什么要分离 Wire Proposal 和应用状态；
- 为什么 Hybrid Retrieval 不等于向量数据库；
- 为什么 Advisor 必须引用 Evidence ID 并支持回退。

### 第三阶段：能讲清楚证据边界

04 → 发布评测 → 对应代码测试。

目标是能区分：

- Frozen 组件消融和 Live 端到端稳定性；
- Demo World 和真实商户数据；
- 任务完成率、硬约束安全率、grounding 和延迟；
- 已证明的能力与尚未证明的假设。

## ⚠️ 版本边界

- 05 中的 V1 是早期 Agents/IntentAgent/SlotAgent/PlannerAgent/ExecutorAgent 设计；它不是 Resume V2 主链。
- 06 和 07 包含设计推导，但最终指标以发布报告为准。
- 当前 Planner 全流程文档已更新为 Beam 默认主路径，并保留 Legacy Search 的历史对照说明。
- 03 中关于 MCP、Saga、长期记忆的内容主要是基础知识或目标设计，不代表当前已接入。
- 旧 Router 方案已经移到 [archive/router](../archive/router/)，其中的指标和字段不要直接引用。

## ✍️ 面试答案格式

每个问题尽量按以下顺序回答：

1. 一句话结论
2. 当前项目中的代码边界
3. 一个测试或评测证据
4. 为什么没有采用另一个方案
5. 当前限制和下一步

推荐的主叙事是：

> 我先用 V1 验证了多 Agent/ReAct 形态，但模型直接决定工具和最终方案时，事实、状态和失败边界难以控制。Resume V2 把 Graph 用于状态和恢复，把 Planner/Provider/Verifier 做成确定性内核，再让 LLM 只在语义理解、结构提案、语义召回和证据化解释这些可验证边界内发挥作用。

## 🔗 证据入口

- [当前架构](../current/resume_v2_architecture.md)
- [发布评测](../releases/resume_v2_release_report.md)
- [Resume V2 tag 说明](../README.md)
- [V1 代码](../../Agents/)
- [V2 主实现](../../app/)

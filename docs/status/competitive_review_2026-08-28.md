# 相近项目代码与产品评审快照

_状态快照 · 2026-08-28；用于解释本轮路线图调整的依据，不替代 canonical 架构与路线图_

## 评审范围

本次同时检查 README 与本地完整代码，不根据“Graph”“多 Agent”“Monte Carlo”“记忆”等宣传词直接判断能力。评审项目：

- [weekend_leisure_plan](https://github.com/SkyAndIcy/weekend_leisure_plan)
- [TodayPersona](https://github.com/Debra2559/TodayPersona)
- [xiaoman](https://github.com/bcefghj/xiaoman)
- [meituan-ai-hackathon-eleyao-butler](https://github.com/nightt5879/meituan-ai-hackathon-eleyao-butler)

评审维度为数据与 Mock、规划方法、LLM 职责、Graph/多 Agent 是否真实、记忆、修改、执行、评测和开源可复现性。

## 代码核验结论

| 项目 | 真实实现 | 最值得借鉴 | 主要限制 |
| --- | --- | --- | --- |
| 周末喵 | 规则约束 + 自研固定 DAG + 高德增强；POI 主要按区域和角色模板生成 | 增删、交换、撤回和地图刷新形成连续修改体验 | localStorage 业务状态、模板数据和自动测试都偏薄 |
| TodayPersona | LangGraph 线性编排；ReAct Agent 搜索高德 POI，LLM 选择地点并生成叙事和调整行程 | 人格/情绪主题、情绪弧线、出行归档和完整品牌表达 | 路线、营业、预算和时间可行性主要依赖 Prompt；画像是较浅的摘要式记忆 |
| 小满 | 单主循环 + 确定性 Planner；固定方案做 200 次 Monte Carlo rollout；本地 JSON 记忆；模拟订单状态机和 LIFO 补偿 | 产品闭环、锁定/局部替换、反馈手账、家庭成员公平和执行状态展示 | 不是 MCTS；概率主要由人为模拟分布产生；所谓多分身是确定性评分；四任务“进化曲线”由预设启发等级驱动；POI DuckDB 未随仓库分发 |
| 饿了幺 | OpenClaw CLI/Skill + 服务端规则；30 家人工样例、112 家虚拟店、660 个合成菜品、70 个周末 POI 和 11 个模板 | `source/synthetic/confidence` 数据边界；LLM 选择后用服务端权威字段回填；多人硬约束与公平评分 | 周末路线仍是固定模板和估算通勤；记忆更接近 Profile shell；未知 LLM 候选尚未被严格拒绝 |

## 对 HappyFreeTime 的判断

HappyFreeTime 当前的真实优势不是功能数量，而是可复现的可信规划基础：

- 自包含、可重建且有许可元数据的 OSM Catalog 快照。
- Weather、Route、WebMap 的 live/record/replay/mock Adapter 与明确降级语义。
- LangGraph SQLite checkpoint、业务 Session View、Request ID 幂等和 Plan 实例身份。
- 2/3/4 站有界确定性组合、finalist 路线复核、时间线重建和 Verifier。
- 自动测试通过稳定接口覆盖真实模块，而不是只验证页面演示。

主要差距是：M2 尚未补齐餐时、返程、全程距离和集合级多样化；产品呈现仍像工程控制台；M3 局部修改、长期记忆和 M4 执行尚未实现。

## 已采纳决策

1. 产品定位固定为“可信、可修改、会记住一家人的周末管家”。
2. M2 优先级不变，竞品功能不抢占餐时、返程、全程距离、多样化与 eval。
3. 增加 M2.5 产品呈现桥接切片：管家式对话、渐进披露约束、POI 详情、Route Leg 展开和地图时间线联动。
4. M3 继续以锁定、局部替换、PlanDiff、Plan Version 和撤销作为旗舰能力。
5. 把轻量长期记忆从 M5 提前为 M3.5，采用结构化主体/scope/证据/置信度/有效期/撤销设计，并输出 `MemoryInfluence`。
6. LLM 可以参与 `PlanningIntent`、可行候选语义评分、`PlanCritic`、修改解析、记忆候选和 Presenter；不能裁决路线/时间/预算/营业/库存，也不能复活违规方案。
7. 不为了宣传引入多 Agent；成员公平优先使用可测试的约束和评分模型。
8. Mock 数据区分 `SourceFact`、`DerivedFeature` 和 `SimulatedState`，任何模拟概率都不得展示为真实预测准确率。

## 明确不照搬

- 不让 LLM 直接输出最终合法时间线或自行补写 POI 事实。
- 不把固定方案的随机预演描述为 MCTS，也不在没有真实分布校准时输出伪精确“真实成功率”。
- 不把一次行为直接写成永久偏好或家庭硬约束。
- 不用自由文本画像替代可审计记忆，不在首版先上向量数据库。
- 不把 Planner、Verifier 和成员评分器包装成多个 LLM Agent 来增加概念数量。
- 不复制来源和许可不清晰的竞品 Mock 数据进入正式 Catalog。

## 对应正式基线

- 产品、数据、LLM、记忆和前端设计：[`../canonical/architecture_v2.md`](../canonical/architecture_v2.md)
- 实施顺序和验收：[`../canonical/v2_roadmap.md`](../canonical/v2_roadmap.md)
- 当前 M2 未完成项：[`m2_trustworthy_planning_plan_2026-08-20.md`](m2_trustworthy_planning_plan_2026-08-20.md)

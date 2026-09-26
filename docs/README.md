# HappyFreeTime 文档中心

_当前实现、发布证据、学习记录、面试材料、未来路线图和历史设计的统一入口。_

---

## 📍 新会话从这里开始

如果你要学习或解释当前 Resume V2，按这个顺序阅读：

1. 根目录 README.md：项目是什么、现在能做什么、如何运行
2. [Resume V2 当前架构](current/resume_v2_architecture.md)：只看已实现的运行链路
3. [Resume V2 发布评测](releases/resume_v2_release_report.md)：确认数据、指标和限制
4. [CONTEXT.md](../CONTEXT.md)：统一领域术语
5. [面试材料索引](interview/README.md)：按主题学习和准备表达
6. 需要理解演进原因时，再读 learning/ 和 archive/
7. 需要讨论未来功能时，最后读 canonical/v2_roadmap.md 和对应 backlog；个人未决想法保存在本地 `docs/local/`

不要一开始直接阅读目标架构或历史交接文档。它们会把“当前已经实现”和“以后希望实现”混在一次长叙述里。

## ⚖️ 文档状态和权威层级

| 状态 | 含义 | 代表文档 |
| --- | --- | --- |
| Current | 当前代码和发布版本可以验证的能力 | 根 README、current/、发布报告 |
| Frozen | 某次发布或评测的不可变证据 | Resume V2 tag、releases/resume_v2_release_report.md |
| Canonical target | 已确认的目标设计，不等于已实现 | canonical/architecture_v2.md、canonical/v2_roadmap.md |
| Learning | 个人学习和复盘，不替代代码契约 | learning/ |
| Interview | 面试讲解和知识整理，必须回指项目证据 | interview/ |
| Historical | 某个时间点的设计、诊断或交接记录 | status/、archive/ |
| Proposal | 尚未批准的想法或未来切片 | canonical/路线图；个人草案在本地 `docs/local/` |

发生冲突时使用以下顺序：

1. 当前代码和自动测试
2. 根 README
3. current/ 和冻结发布报告
4. canonical/ 目标设计
5. learning/ 和 interview/
6. status/ 历史快照
7. archive/、本地草案和未批准 backlog

## 🧭 当前版本入口

### Current implementation

- [Resume V2 当前架构](current/resume_v2_architecture.md)：当前 Graph、Wire Proposal、Enrichment、Gate、PlanSpecCompiler、Beam、Provider、Verifier、持久化和降级边界
- [Resume V2 发布评测](releases/resume_v2_release_report.md)：36 条 reviewed Fixture、C0–C4、B0/B3、Retrieval、Advisor、Beam 和发布限制
- [CONTEXT.md](../CONTEXT.md)：Session、Planning Run、Plan Version、Constraint、Provider 和 Memory 等术语

当前版本的发布 tag 是 planner-v2-eval-baseline。评测报告中记录的代码提交是 db8603b，发布文档随后收口在 bef5fa3；两者处于同一祖先链，不能把评测报告中的提交误认为另一个产品版本。

### Canonical target and roadmap

- [architecture_v2.md](canonical/architecture_v2.md)：目标架构。它包含 ExecutionGraph、Saga、长期记忆、ToolBroker 和 MCP 演进等内容；阅读时只把明确标注或已由 current/和测试证明的部分当成当前能力
- [v2_roadmap.md](canonical/v2_roadmap.md)：M1–M5 长期路线图，包含执行闭环、记忆、产品化和评测规划
- [resume_release_v1_roadmap.md](canonical/resume_release_v1_roadmap.md)：Resume V1 的历史交付路线图，已经被 Resume V2 发布结果 supersede，不是当前施工计划

### Interview and learning

- [interview/README.md](interview/README.md)：面试材料入口
- [05_三阶段架构演进](interview/05_三阶段架构演进_从ReAct原型到受约束规划Agent.md)：V1 → V2 主叙事
- [06_语义规划与轻量 RAG](interview/06_从关键词匹配到可验证语义规划与轻量RAG.md)：语义中间层、Hybrid Retrieval、Grounding 和评测
- [07_Wire Proposal 与 CommandCompiler](interview/07_从万能Interpretation到受约束WireProposal与CommandCompiler.md)：结构化输出失败与 Harness 重构
- [learning/](learning/)：按里程碑记录“我是否理解和验证过”，不是产品文档

## 🗂️ 目录地图

```text
docs/
├── README.md                         # 本入口：状态、权威层级、阅读顺序
├── current/                          # 当前发布版本的实现说明
│   └── resume_v2_architecture.md
├── releases/                         # 冻结发布与可复现实验报告
├── canonical/                        # 已确认的目标架构和路线图
├── adr/                              # 关键架构取舍及原因
├── status/                           # 带日期的评测、诊断和阶段快照
├── interview/                        # 面试讲解、问答和项目知识
├── learning/                         # 个人学习进度和里程碑复盘
├── collaboration/                    # 会话协作规则
├── local/                            # 本地忽略：临时交接、个人草案和原始对话（不进远程）
└── archive/                          # V1、旧 Router 和原始讨论
```

## 🕰️ 哪些文档已经过时或只是历史记录

### 需要谨慎使用的旧文档

- interview/v2版本的方案生成的全流程.md：已更新为 Resume V2 的 Beam Search 流程；其中仍保留旧版组合搜索的对照说明
- status/v2_gap_analysis_2026-08-12.md：早期差距分析
- status/resume_release_case_review_2026-09-14.md：早期 Fixture 审核，最终口径以 36 条发布报告为准
- status/m2_catalog_c2_2026-08-23.md 和 status/m2_catalog_c3_2026-08-24.md：Catalog 中间版本
- learning/milestones/m1_entry_loop.md、m2_trustworthy_planning.md：有效的学习证据，但其中“下一步”是当时的时间点，不是当前待办

这些文档不应删除。它们解释了项目如何从 V1 演进到 V2；需要在开头保留或补充 Historical snapshot 说明。

### 只描述未来的设计

- canonical/architecture_v2.md 中的 Execution/Saga、长期 Memory、真实订单和 MCP Adapter
- canonical/v2_roadmap.md 中的 M4/M5

个人临时交接、未决草案和原始对话已移入被 `.gitignore` 忽略的 `docs/local/`，不属于公开版本。

它们可以用于设计讨论，但不能写成简历里的“已实现”。

### 明确属于归档的文档

archive/ 下的旧 Router 设计和 V1 Mock/迁移说明只用于追溯。原始对话不随公开版本发布。当前实现以 app/、自动测试、根 README、current/ 和 releases/ 为准。

## 📚 推荐学习方法

每次用新会话学习一个切片时，先给出这三个范围：

1. 当前目标：本次只学习哪个模块或一条链路
2. 证据边界：要看哪些代码、测试、评测报告
3. 暂不讨论：哪些路线图或历史文档先不展开

推荐按以下模块顺序学习：

```text
TurnInterpreter / DemoRouter
        ↓
Enrichment 与 Constraint Patch
        ↓
QuestionGate 与 interrupt/resume
        ↓
PlanningIntent 与 PlanSpecCompiler
        ↓
Catalog / Hybrid Retrieval / Beam Search
        ↓
Timeline / Route / Availability / Verifier
        ↓
Plan Version / PlanDiff / SQLite
        ↓
评测、Grounding 与降级
```

每学完一层，至少做一次小实验：读一个测试、修改一个输入、观察 Trace 或运行一条离线回归。学习文档中的回答只有在能回指代码、测试或报告时，才升级为面试结论。

## ✍️ 维护规则

- 功能或运行方式改变时，先更新根 README 和 current/
- 新增评测只创建带日期的 status 快照，不覆盖旧报告；冻结版本报告进入 releases/
- 目标设计必须写明“目标/未实现”，不能混入当前能力列表
- 面试材料中的指标必须链接发布报告或具体评测产物
- 历史文档不删除，先标记替代文档
- 已确认的目标设计进入 canonical/；个人未决想法和临时草案进入本地 `docs/local/`
- 每次发布用 tag 固定代码、数据、Fixture 和报告哈希

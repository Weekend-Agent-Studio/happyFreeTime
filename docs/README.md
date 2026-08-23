# HappyFreeTime 文档中心

_当前实现、架构基线、学习记录、面试材料和历史设计的统一入口 · 2026-08-24_

---

> **使用原则：** 先确认文档类别和状态，再决定是否据此实现。`archive/` 只用于追溯，`product/product_idea_inbox.md` 只记录想法，二者都不是实现契约。

## 🧭 新会话从这里开始

新开的 Codex 会话按以下顺序读取，不需要一次加载所有历史文档：

1. 阅读根目录 [`README.md`](../README.md)，确认当前已经实现的能力、运行方式和测试基线
2. 阅读本索引，确认文档权威层级和本次任务应使用的材料
3. 阅读 [`canonical/architecture_v2.md`](canonical/architecture_v2.md) 和 [`canonical/v2_roadmap.md`](canonical/v2_roadmap.md)，理解正式设计与当前里程碑
4. 若继续学习，阅读 [`learning/progress.md`](learning/progress.md) 和对应里程碑复盘
5. 若继续开发，检查 [`product/product_idea_inbox.md`](product/product_idea_inbox.md)，但不得自动实现未批准想法
6. 读取 [`collaboration/session_bootstrap.md`](collaboration/session_bootstrap.md)，按切片约定开始协作
7. 最后检查 `git status`、当前分支和最近提交，避免覆盖用户改动

继续 M2 时同时阅读 [`status/m2_trustworthy_planning_plan_2026-08-20.md`](status/m2_trustworthy_planning_plan_2026-08-20.md) 与 [`learning/milestones/m2_trustworthy_planning.md`](learning/milestones/m2_trustworthy_planning.md)。前者是切片计划快照，不高于 canonical 路线图；后者区分代码证据与个人学习状态。

Catalog C2 是已被 C3 替代的历史快照；当前采集、replay、Unknown 与 Fixture 隔离边界见 [`status/m2_catalog_c3_2026-08-24.md`](status/m2_catalog_c3_2026-08-24.md)。

可以把下面这段直接发给新会话：

```text
请先阅读 README.md、docs/README.md 和 docs/collaboration/session_bootstrap.md。
如果本次继续 M1 学习，再阅读 docs/learning/progress.md 与
docs/learning/milestones/m1_entry_loop.md。

先说明你理解的当前代码进展、我的学习进展和本次切片边界。
不要自动实现 product_idea_inbox 或 archive 中的内容。
每个行为切片完成后暂停，让我先预测、复述和做一个小实验。
```

## 📚 文档地图

| 目录 | 内容 | 权威性 | 更新时机 |
| --- | --- | --- | --- |
| 根 [`README.md`](../README.md) | 当前能力、启动、验收、测试 | 当前实现入口 | 功能或运行方式变化时 |
| [`canonical/`](canonical/) | V2 架构与路线图 | 正式设计基线 | 架构决策确认后 |
| [`status/`](status/) | 带日期的差距分析和状态快照 | 历史时点事实 | 生成新快照，不覆盖旧结论 |
| [`product/`](product/) | 临时想法和未决需求 | 非实现契约 | 讨论或晋升想法时 |
| [`collaboration/`](collaboration/) | 新会话协议和人机协作方式 | 协作规范 | 工作方式发生变化时 |
| [`learning/`](learning/) | 学习进度、里程碑复盘、复盘模板 | 个人掌握证据 | 每个切片或里程碑结束时 |
| [`interview/`](interview/) | 面试知识、项目讲解素材 | 学习与表达材料 | 形成可靠项目证据后 |
| [`archive/`](archive/) | V1、旧 Router 方案和原始讨论 | 不可作为当前依据 | 只追加归档说明 |

## ⚖️ 权威层级

当材料互相冲突时，按以下顺序判断：

1. 当前代码和自动测试决定系统实际行为
2. 根 `README.md` 描述当前可运行能力和验证方法
3. `canonical/` 决定已经确认的目标设计和里程碑方向
4. `status/` 只描述文件日期对应的历史状态
5. `learning/` 和 `interview/` 用于理解与表达，不替代实现契约
6. `product/` 中未晋升的想法不得自动进入开发
7. `archive/` 只解释设计如何演进，发生冲突时始终让位于当前材料

## 🗂️ 目录结构

```text
docs/
├── README.md
├── canonical/             # 当前架构与路线图
├── status/                # 带日期的状态快照
├── product/               # 未决产品想法
├── collaboration/         # 新会话与协作规范
├── learning/
│   ├── progress.md        # 总学习进度
│   ├── milestones/        # 每个里程碑的完整复盘
│   └── templates/         # 后续里程碑复盘模板
├── interview/             # 面试知识与表达材料
└── archive/               # 旧设计与原始讨论
```

## ✍️ 维护约定

- 功能完成后更新根 `README.md`，不要在这里记录个人是否掌握
- 学完一个行为切片后更新 `learning/progress.md` 和对应里程碑页面
- 学完完整里程碑后，按模板整理项目结构、完整链路、关键设计、测试、面试讲法和相关基础知识
- 新想法先进入想法收集箱；只有用户确认并进入路线图、Issue 或 ADR 后才能实现
- 历史文档不直接删除，先归档并注明替代它的当前文档
- 文档中的指标、结论和面试说法必须能指向代码、测试、提交或真实评测结果

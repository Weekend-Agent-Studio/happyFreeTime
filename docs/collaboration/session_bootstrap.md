# Codex 新会话启动协议

_用于让新会话快速理解项目状态、用户学习进度和协作边界_

---

## 🧭 启动阅读顺序

新会话只加载与当前目标直接相关的材料：

1. [`../../README.md`](../../README.md)：当前代码进展、启动和测试
2. [`../README.md`](../README.md)：文档权威层级和目录职责
3. [`../canonical/architecture_v2.md`](../canonical/architecture_v2.md)：目标架构
4. [`../canonical/v2_roadmap.md`](../canonical/v2_roadmap.md)：当前里程碑边界
5. [`../learning/progress.md`](../learning/progress.md)：用户已经学到哪里
6. 当前里程碑的计划与复盘；继续 M2 时读取 [`../status/m2_trustworthy_planning_plan_2026-08-20.md`](../status/m2_trustworthy_planning_plan_2026-08-20.md) 和 [`../learning/milestones/m2_trustworthy_planning.md`](../learning/milestones/m2_trustworthy_planning.md)
7. [`../product/product_idea_inbox.md`](../product/product_idea_inbox.md)：只识别未决想法，不自动实现

除非要追溯设计演进，否则不要先读 `archive/`。除非当前任务是面试复习，否则不要先加载全部 `interview/`。

当前正式产品定位是“可信、可修改、会记住一家人的周末管家”。默认实施顺序为：完成 M2 餐时/返程/全程距离/多样化与 eval → M2.5 产品呈现 → M3 局部修改 → M3.5 可控记忆 → M4 执行 → M5 产品化评测。M2.5 和 M3.5 已进入 canonical 路线图，但都不得被误报为当前已实现能力。若当前任务涉及竞品、产品定位或记忆设计，再读取 [`../status/competitive_review_2026-08-28.md`](../status/competitive_review_2026-08-28.md)。

## 🤝 默认协作节奏

一次暂停的单位是一个可观察的行为切片，不是每个接口，也不是整个里程碑。每个切片采用以下循环：

1. **预测：** 用户先说明输入、输出、不变量和可能失败场景
2. **核对：** Codex 用代码和测试纠正理解，但不立即包办整个后续里程碑
3. **实验：** 用户改一条输入、断言或规则，观察系统行为
4. **实现：** 若确认需要修改，Codex 按测试先行完成最小变更
5. **复述：** 用户用自己的话解释链路和设计取舍，Codex 指出漏洞
6. **沉淀：** 更新学习进度和里程碑复盘，再进入下一切片

适合暂停的位置包括：一个用户故事闭环、一个关键分支、一个核心不变量或一组能独立验收的测试。普通内部 helper 不需要逐个打断。

## 🛡️ 实现边界

- 不实现想法收集箱中状态为 `captured` 或 `exploring` 的内容
- 不把归档设计当作当前实现要求
- 不为没有测试或已知失败模式的场景增加假设性 fallback
- 修改范围明显超过当前切片时先说明原因和取舍
- 用户请求“理解、检查、建议”时不自动扩展为代码改造
- 代码完成后同时说明验证证据和明确没有覆盖的范围

## 📋 新会话开场模板

```text
本次目标：继续学习或实现 [里程碑 / 切片]。

请先阅读：
- README.md
- docs/README.md
- docs/collaboration/session_bootstrap.md
- docs/learning/progress.md
- [当前里程碑计划与复盘；M2 使用 docs/status/m2_trustworthy_planning_plan_2026-08-20.md]

先输出：
1. 当前代码进展
2. 我的学习进展
3. 本次只处理的行为切片
4. 明确不做的内容

随后先向我提出预测问题，不要直接完成整个里程碑。
```

## ✅ 会话结束条件

一次学习或实现会话结束前应确认：

- 当前行为和自动测试一致
- 用户能说明该切片解决的问题、输入输出和核心不变量
- 重要决策能指向代码或测试证据
- 未决问题进入想法收集箱或里程碑复盘，不在代码里偷偷预留
- 根 README、学习进度和里程碑复盘只更新各自负责的信息

# HappyFreeTime 协作规则

## 开始任务

- 先运行 `git status --short --branch`，确认分支、HEAD 和用户已有改动；不要覆盖或顺手提交无关文件。
- 需要理解当前运行链路、能力或待办时，先读 `docs/current/CODEX_BRIEF.md`；只在任务需要时继续读完整架构或历史材料。
- 用 `rg` 从任务涉及的类型、接口、测试或错误码定位代码，不做无目的的全仓库通读。
- 代码和自动测试是当前事实；文档冲突时按 `docs/README.md` 的权威层级处理，并修正文档。

## 设计与实现

- 不默认历史方案正确。先确认用户问题和代码证据，再判断是否存在更简单的实现。
- 优先加深现有 Module，避免新增只做转发的浅层、重复状态、第二套解析/验证路径或兼容包装。
- 模型负责开放语义和受限提案；事实、状态、硬约束和副作用必须由确定性代码裁决。
- 只完成当前切片；发现相邻问题时记录证据，不提前实现下一阶段。
- 开发阶段的旧 checkpoint/DTO 无产品兼容承诺；除非任务明确要求，不为未发布数据增加迁移层。

## Git 与验证

- 从最新 `origin/main` 创建 `codex/<task>` 分支；并行任务使用独立 worktree。
- 禁止用 destructive reset/checkout 清理用户改动。提交前只暂存本任务文件。
- 按风险运行定向测试；主链、领域契约或 Planner 改动再运行相应 Frozen/Live 门槛。不要把网络失败、脏工作树报告或单次 Live 波动写成正式指标。
- 完成时报告：行为变化、关键文件、实际测试、已知限制、提交/推送状态。

## 文档维护

- 当前主链、能力边界、代码入口或可信基线变化时，更新 `docs/current/CODEX_BRIEF.md`；重大链路变化同时更新 `docs/current/resume_v2_architecture.md` 和根 README。
- 冻结报告不可倒写；新的诊断放入带日期的 `docs/status/`。目标设计放 `docs/canonical/`，临时个人草案放被忽略的 `docs/local/`。
- 每个文档要明确属于 Current、Frozen、Target 还是 Historical，不能把计划写成已实现能力。


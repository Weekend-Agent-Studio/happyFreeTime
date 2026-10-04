# S-CORE2E：统一约束路径清理与发布验收

> 分支：`codex/s-core2-request-engine`；基于 `origin/main@26df327`；本报告记录 S-CORE2A–E 的分支验收，不代表已合并 `main`。日期：2026-10-04。

## 结果摘要

S-CORE2E 收敛并清除了迁移期生产路径：方案后约束由 `RequestPatchUpdateCompiler` 编译；字段反问通过字段级 Compiler 更新 `PlanRequest`；是否阻断由 `QuestionPolicy` 处理；旧开发 checkpoint 使用 `planner-core2e-v1` 命名空间，不读取或适配旧 checkpoint。`ClarificationResolver` 仍保留取消、新需求和修改目标引用等命令语义，不再承担规划约束解析。

统一主链为：

```text
自然语言 Proposal ─┐
顶部栏 typed DTO ──┼─> RequestPatch -> ConstraintEngine -> PlanRequest
字段反问回答 ─────┘                    ├─> QuestionPolicy / interrupt
                                       └─> Planner / replan
```

`RawConstraints` 与 `ConstraintPatch` 仍作为有限 Router Wire Proposal，不是 Planner 执行状态；不再保留 `NormalizedConstraints` 别名、旧 `ConstraintPatchCompiler` 或 `NeedQuestionGate`。没有建设完整 Temporal AST；`PlanningWindow` 保留字段来源与边界 kind，默认条件可见、可编辑。结构化请求修改采用 revision 检查，stale 请求明确拒绝。

S-SIR backlog 状态：S-SIR0 已完成；S-SIR1 与 S-SIR1B 的范围由 S-CORE2B–D 落地；S-SIR2–S-SIR5 仍按有复现的语义缺口单独评估，不属于 S-CORE2。主工作树中的 `docs/local/status/semantic_ir_backlog_2026-09-24.md` 是未跟踪/本地文件，不在本分支工作树内；为避免改动主工作树，本次在受版本控制的路线图和本报告更新了对应状态，没有修改该本地文件。

## 自动化验证

- 后端：`446 passed`、`57 subtests`。
- Clarification Eval：24 个确定性子案例全部通过，覆盖字段恢复、stale revision、无效回答上限、原子 Patch 与重复提问防护。
- 前端 Vitest：36/36 通过。
- 前端 TypeScript 检查与 Vite 构建：通过。
- `compileall` 与 `git diff --check`：通过。
- Playwright：桌面与移动项目中 13 个用例报告通过、1 个移动端固定布局用例按项目配置跳过；全部测试主体已执行。Windows 上 Playwright 在最后一个用例后未正常退出并卡在 teardown，需 Ctrl-C 收尾，因此命令退出码不是干净的 0；不将其记为完整 runner 通过。UI 覆盖了 When 保存后单独确认重规划、反问卡跳到 When 并通过同一 request state 解决、方案选择刷新恢复；API/Graph 全套回归覆盖 request revision、clarification resume、规划和会话恢复。

## 冻结与 Live 评测

同一 36 条 reviewed Frozen Fixture、每变体单次运行：

| Variant | Task success | Hard constraints | Conflict diagnosis | Modification execution | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| C0 Rule Intent + Rule Retrieval | 34/36 | 7/7 | 4/4 | 10/10 | 两个 grounded-semantic 断言未满足，与已知 Rule 语义召回缺口一致 |
| C1 LLM Intent + Rule Retrieval | 34/36 | 7/7 | 4/4 | 10/10 | 目标/查询召回 17/17；失败仍为两条 grounded-semantic 断言 |
| C2 Rule Intent + Hybrid | 36/36 | 7/7 | 4/4 | 10/10 | 必需断言 208/208 |
| C3 LLM Intent + Hybrid | 36/36 | 7/7 | 4/4 | 10/10 | 必需断言 208/208 |
| C4 LLM Intent + Hybrid + Advisor | 36/36 | 7/7 | 4/4 | 10/10 | Advisor schema 27/27；接受 23/27，其余安全回退 |

C0/C1 的 34/36 不构成 S-CORE2 退化：这是 Rule-only / 非 Hybrid 语义召回表现；C2/C3/C4 达到或高于冻结发布门槛。Hybrid BGE 冷启动约 78.1s（排除在稳态延迟外）；本轮稳态 Retrieval P50/P95 为 143/211ms。

Live Router 单次诊断：

| Variant | Task success | Hard constraints | Conflict diagnosis | Notes |
| --- | ---: | ---: | ---: | --- |
| B0 downstream Rule | 26/36 | 6/6 | 4/4 | 与前次观察 26/36 一致 |
| B3 Hybrid + grounded Advisor | 28/36 | 6/6 | 4/4 | Advisor 接受 16/20；较前次 30/36 低 2 条，属于单次实时模型波动，不能作为稳定退化或提升结论 |

Frozen 和 Live 指标不可混用为通用生产成功率。评测在每个 LLM 变体只运行一次、调用有上限；sandbox 首次 C1 尝试因网络限制发生安全 fallback，正式 C1 数字来自获准联网后的干净重跑。

## 已知边界与 PR 前检查

- Planner 唯一请求真值为 `PlanRequest`；ConstraintEngine 原子应用 Patch、检查 revision 与跨字段约束；QuestionPolicy 只消费结构化 Issue。
- 顶栏是当前请求的投影与 typed Patch 入口；保存条件与重规划分开，pending clarification 绑定 revision，成功顶部栏 Patch 会使旧 clarification 失效。
- 旧开发 checkpoint 不适配。已有开发测试会话需要新建；不会删除用户的 SQLite 业务会话/消息历史。
- 前端浏览器用例是 mock API 的组件级 E2E，和 API/Graph 后端集成测试共同覆盖端到端边界；本次没有宣称完成一次真实 Provider、浏览器、重启全链路的单手工演示。
- 完整 Temporal AST、S-SIR2–S-SIR5、统一 Trace、部署与长期记忆均不纳入本切片。

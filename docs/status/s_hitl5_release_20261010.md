# S-HITL5 收口与发布验收

日期：2026-10-10
分支：`codex/s-hitl-recovery`
评测代码基点：`358a556`，评测时工作树包含本切片改动（`dirty=true`）。

## 结果摘要

S-HITL5A/5B/5C 的代码、确定性回归和前端门槛已完成。RecoveryWorkflow 承担恢复 Action 的校验、Patch 应用、字段反问投影和失败投影；Graph 节点保留中断、Trace 和路由职责。字段恢复复用 QuestionPolicy 与既有尝试上限；冲突请求保持为未提交草稿，只有 ConstraintEngine 返回 resolved 才会成为 active request。

`needs_recovery` 保留原始冲突、Provider facts、Catalog 诊断、搜索 Trace 与 runtime decisions。恢复动作拥有独立的类型化请求合同，不重新经过 Router。失败码评测真值已与 Planner 的阶段性诊断对齐。

## 确定性验收

- 后端：`519 passed，48 subtests passed`
- 前端：`42 passed`
- TypeScript 检查与生产构建：通过
- Playwright E2E：`15 passed，1 skipped`（移动端跳过桌面专属布局测试）
- `compileall`、`git diff --check`：通过
- Policy-contract Fixture：9/9；这是确定性 Policy 合同测试，不是对话成功率。
- Reviewed HTTP 多轮 Fixture：1/1 完成“缺预算反问 → 严格预算恢复卡片 → stale Action 拒绝 → typed Action 重规划”。测试确认 stale 请求返回 409、有效恢复成功、Router 总调用数为 1（恢复 Action 本身为 0）。
- API 集成测试另覆盖修改失败后原 PlanVersion/选中方案不变。Playwright 的恢复卡片用例验证前端提交类型化 Action；其 API 响应由 E2E fixture 模拟，不应误称为浏览器到真实后端的全栈 E2E。

## Frozen C0–C4

| 变体 | 任务成功 | 硬约束安全 | 冲突归因 | 修改执行 |
|---|---:|---:|---:|---:|
| C0 Rule + Rule | 33/36 | 7/7 | 4/4 | 9/9（setup 9/10） |
| C1 LLM + Rule | 34/36 | 7/7 | 4/4 | 10/10 |
| C2 Rule + Hybrid | 36/36 | 7/7 | 4/4 | 10/10 |
| C3 LLM + Hybrid | 36/36 | 7/7 | 4/4 | 10/10 |
| C4 LLM + Hybrid + Advisor | 36/36 | 7/7 | 4/4 | 10/10 |

C0 与 PC 阶段冻结基线 33/36 持平；C2–C4 达到完整通过。C0 唯一修改 setup 差异是已知首轮方案 setup 差异，已进入报告，不能算作本次恢复代码造成的修改执行回归。

C4 Advisor 接受 `24/27`，其余 `3/27` 安全回退。检查了保存的 C4 transcript 后，发现评测器把派生约束 `constraint.planning_window` 当作必须同名存在的字段，导致旧报告误记 `0/24`。现已修复派生字段映射并增加正反例测试；基于同一份保存结果离线重算为 `24/24`，没有再次调用模型。C4 原始 run 的 `report.json` 保留了当时生成的聚合值；最终指标以本报告中的校正值为准。

Live latency 仅作为该次运行观察值，不与稳定态或其他变体混为因果结论：C0 P50/P95 `280/626ms`、C2 `585/1125ms`、C4 `3266/10152ms`。C4 包含 Advisor 与一次约 86 秒的 BGE 冷启动；不应用该冷启动代表稳态检索延迟。

## Live B0/B3

- B0（Downstream Rule）：任务成功 `30/36`，硬约束 `7/7`，冲突归因 `4/4`，P50/P95 `1520/2055ms`。本次结果是单次 Live 观测，不作为跨架构确定性结论。
- B3（Grounded Advice）：Runner 执行了 36 条，但 27 条遇到外部模型 Provider `status=402`；其中 26 次出现在 TurnInterpreter、1 次出现在 Advisor。该状态是 Provider 额度/计费错误，不是网络瞬断。B3 的 `9/36` 成功率、`0/4` 冲突指标受外部错误污染，标记为**不可用于产品质量比较**，不重试至通过。

因此本次发布验收的代码与离线门槛通过，但 Live B3 门槛尚未有效完成。待 Provider 额度恢复后只需重跑 B3；无需重跑 C0–C4 或 B0。额度恢复前不应把这轮 B3 报成成功或失败的产品质量指标。

## 失败归因修正

Planner 现在依据已有搜索 Trace 的实际 `rejected_by` 计数区分时间窗与距离淘汰；本地组合距离失败返回 `NO_PLAN_WITHIN_DISTANCE`，真实 Route Provider 验证失败仍使用 Route 阶段诊断。Frozen C0/C2 已在该修正后重跑并通过门槛。对应 smoke 与 release Fixture 的旧通用错误码已同步更新。

## Git 状态与后续

代码尚未推送或创建 PR。发布分支保留 B3 的明确外部阻塞，不将不完整 Live 指标伪装成完整绿灯。建议 Provider 额度恢复后补跑 B3，确认有效结果后再推送并创建 PR。

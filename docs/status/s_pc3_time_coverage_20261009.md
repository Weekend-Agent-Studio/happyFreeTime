# S-PC3：全天软覆盖目标

## 目标

把“一整天”从单纯的 `09:00–21:00` 可规划窗口，补充为可执行但不阻断规划的
`TimeCoverageObjective`。它只参与 PlanSpec 优先级、候选评分和结果提示；路线、
营业、预算、时间窗等硬约束仍由 Scheduler / Provider / Verifier 裁决。

## 实现

- `PlanningIntent.time_coverage` 保存目标时段和用户证据。
- Rule Intent 从 `time.trip.all_day.v1` 派生上午、下午；明确晚饭或晚间活动时再加入晚上。
- LLM Intent 不能丢弃该目标，仍由 Harness 从用户时间证据派生。
- PlanSpec 编译时优先给覆盖目标更匹配的结构，但不把结构变成硬门槛。
- 本地估算和路线复核后的 Plan 都加入 `planning.time_coverage.v1` 评分。
- 未完整覆盖时返回 `time_coverage_incomplete` warning 和 tradeoff；不会伪造等待或直接拒绝方案。
- 覆盖评分按站点角色过滤，晚饭不能单独冒充下午活动覆盖。

## 验证

- 后端：`483 passed`，`39` 个 subtests
- `compileall`：通过
- `git diff --check`：代码无内容错误
- Frozen C0：`33/36`，硬约束 `7/7`，冲突归因 `4/4`
- Frozen C2：`36/36`，必需断言 `208/208`，硬约束 `7/7`，冲突归因 `4/4`，修改链路 `10/10`
- C2 稳态检索 P50/P95：约 `148/280ms`；BGE 冷启动单独记录，未混入稳态指标

全天案例中，存在可行多站方案时会优先保留跨时段结构；无法完整覆盖时仍返回安全方案，
并明确标注未覆盖时段。精确短时间范围不会继承全天目标。

## 未完成项

按当前执行环境的外部 Provider 授权要求，6 条真实 PlanningIntent Pilot 尚未运行；
它不影响本切片的确定性回归结果，待明确允许将冻结用户请求发送到指定 LLM 后再单独执行。

本切片未实现 RecoveryPolicy、HITL UI 或自动恢复循环。

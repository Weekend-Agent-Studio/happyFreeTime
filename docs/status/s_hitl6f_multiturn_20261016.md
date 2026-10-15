# S-HITL6F：前端恢复闭环与多轮验收

## 状态

本切片已完成，基于 S-HITL6 A–E 的 `codex/s-hitl6-reliability` 分支。没有新增 Recovery Agent 或第二套规划路径。

## 完成内容

- Recovery 卡片现在记录一次操作的生命周期：提交中、等待下一步、成功、再次无解、取消/结束和可重试失败。
- `apply_request_patch` 操作在提交前显示安全的字段变化预览；不展示原始 Patch 对象。
- 已结束的卡片不可重复执行；同一轮返回新的 `needs_recovery` 时，旧卡片保留结果摘要，新卡片成为唯一可操作入口。
- 前端会将恢复结果作为 assistant response 写入会话消息；临时错误仍保留为可重试的错误状态，不伪装成已完成。
- 在已有 reviewed S-RX4 HTTP 集上增加 8 条聚焦清单，避免复制第二套多轮 runner：
  - 自动恢复重入与安全终止；
  - 地点消歧；
  - 显式距离约束恢复；
  - 开始新需求后的约束清理；
  - 单站替换失败后的 PlanVersion 保留；
  - 预算恢复与 stale Action；
  - 出发时间反问恢复；
  - 取消恢复且不产生副作用。
- S-RX runner 增加以下诊断指标：
  `silent_failure_count`、`unexplained_repeat_question_count`、
  `recovery_action_applied_rate`、`recovery_to_feasible_plan_rate`、
  `repeated_ineffective_recovery_count`、`new_request_constraint_leak_count`、
  `failure_diagnosis_coverage`、`modification_failure_preservation_rate`。

## 验证

- 前端 Vitest：34 passed。
- 前端生产构建：通过。
- HITL6/S-RX 数据集与指标契约：33 passed，30 subtests passed。
- `compileall app evals tests`：通过。
- `git diff --check`：通过。

本切片没有重新执行昂贵的 Live B0/B3，也没有把 8 条聚焦清单当作已经执行的 HTTP 结果。需要运行时指标时，应使用 `evals/run_s_rx_multiturn.py` 选择这 8 个 `source_case_id`，并在干净工作树上记录报告。

## 保留限制

1. Recovery 生命周期是前端对后端结构化 response 的安全投影；刷新后依赖已持久化的 assistant response，历史卡片不会伪造客户端临时状态。
2. 跨进程 SSE 重连和长期 Trace 存储仍不是本切片范围。
3. “恢复动作已应用”与“恢复后找到方案”分开计数；自动放宽后仍无解可以安全终止，不能被记为成功。

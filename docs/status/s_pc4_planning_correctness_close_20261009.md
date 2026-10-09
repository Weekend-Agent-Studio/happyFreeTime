# S-PC4：规划正确性最终补缝

## 目标

补齐第一阶段验收发现的两个边界：

- 没有精确站数时，Rule fallback 不能把未注册的必需角色序列压缩成旧静态骨架；
- 全天语义必须由类型化请求字段承载，不能从派生时间窗或 `rule_id` 反推。

## 实现

- `PlanSpecCompiler` 在静态 Rule 结构无法覆盖 `required_stop_roles` 时，生成有界的 1–4 站动态结构；保留有序重复角色，校验容量、餐食顺序和重复餐类，并将泛 `MEAL` 仅作为显式需求保留的角色。
- 模型 Proposal 被拒绝或不可用时，继续使用同一批确定性动态 completion；不增加第二次模型调用，也不恢复骨架注册表合法性门。
- `PlanRequest.trip_time_scope` 独立保存 `morning/afternoon/evening/all_day` 等整体粗粒度语义。CREATE、方案后 Patch、反问恢复和顶部栏的具体时间编辑均通过 `RequestPatch → ConstraintEngine` 更新或清除该字段。
- `PlanningIntent.time_coverage` 只读取类型化 `trip_time_scope`；显式时间范围不会继承全天目标。
- 增加 checkpoint、API constraint summary、动态 Rule completion、模型拒绝 fallback 和全天/显式范围回归测试；站数解析辅助函数改为语义中立命名。

## 验证

- 后端全量：`487 passed`，`39` 个 subtests。
- `compileall app evals tests`：通过。
- `git diff --check`：通过。
- Frozen C0（干净提交 `391eed7`）：任务 `33/36`；必需断言 `196/200`；硬约束 `7/7`；冲突诊断 `4/4`；修改 setup `9/10`、执行 `9/9`。
- Frozen C2（干净提交 `391eed7`）：任务 `36/36`；必需断言 `208/208`；硬约束 `7/7`；冲突诊断 `4/4`；修改链路 `10/10`。
- C2 稳态 BGE 检索约 P50/P95 `163/231 ms`；冷启动约 `33.1 s`，单独记录。

C0 的三条失败与 PC3 已知失败一致：两条 DemoWorld grounding 证据缺口，以及 `modify_last_stop_four_station` 的首轮 setup 真值差异；没有新增规划正确性或硬约束回归。C2 全部通过，说明动态 completion 和类型化时间字段没有破坏 Hybrid 主路径。

本切片没有运行实时 PlanningIntent、Advisor 或 Live B0/B3；这些属于后续业务发布回归，不是本次确定性补缝门槛。

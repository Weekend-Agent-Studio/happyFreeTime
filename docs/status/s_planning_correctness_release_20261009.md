# S-PC：规划正确性第一阶段收口

## 结论

S-PC1、S-PC2、S-PC3 已完成，第一阶段停止门达到：Planner 不再把部分结构、作用域时间或全天语义错误地硬编码成最小结构/错误时间窗；确定性冻结评测已在干净提交上完成，PlanningIntent 只做有界真实模型小样本验证。

本阶段**没有开始** Typed RecoveryAction、RecoveryPolicy、Graph interrupt/resume 或前端 HITL 改动。下一阶段应在本报告基础上另开切片，不把恢复机制用于掩盖 Planner 自己制造的假失败。

## 提交与范围

| 切片 | 提交 | 收口内容 |
|---|---|---|
| S-PC1 | `212a960` | 必需角色是结构下限；显式结构不再被提前收缩为 activity-only/meal-only；保留有序重复角色。 |
| S-PC2 | `efc1bca` | 结构更新统一编译为 RequestPatch；CREATE、方案后更新和反问恢复共享作用域时间语义；不把“下午去公园”误投影为出发时间。 |
| S-PC3 | `82a32f6` | “一整天”成为可观测软覆盖目标，影响结构优先级、候选评分和 warning，不成为硬失败或伪造等待。 |

当前验收基线为 `82a32f6e080498c3bc619fa4fe8cd36faba37886`，工作树干净，评测报告记录 `dirty=false`。

## 确定性验证

- 后端全量：`483 passed`，`39` 个 subtests。
- `compileall` 通过，`git diff --check` 无代码内容错误。
- **C0 Rule Intent + Rule Retrieval（干净重跑）**：任务 `33/36`；必需断言 `196/206`；硬约束安全 `7/7`；冲突归因 `4/4`；修改执行 `9/9`，修改 setup `9/10`。
- **C2 Rule Intent + Hybrid Retrieval（干净重跑）**：任务 `36/36`；必需断言 `208/208`；硬约束安全 `7/7`；冲突归因 `4/4`；修改链路 `10/10`；Beam 无 Legacy fallback。
- C2 稳态 BGE 检索约 P50/P95 `159/319 ms`；冷启动约 `39.6 s`，单独记录，不混入稳态延迟。

这组结果说明：结构、作用域时间和全天软目标没有破坏确定性主链路；Hybrid 仍补齐 Rule-only 的语义证据缺口。C0 的三条失败为两个 grounding 证据缺口（晚饭偏好、雨天室内）和一条修改 setup/fixture 行为差异，不能归因于 PC3 的硬约束回归。

报告：

- [C0 clean](../../artifacts/evals/S-PC3_C0_clean_20261009/report.md)
- [C2 clean](../../artifacts/evals/S-PC3_C2_clean_20261009/report.md)
- [PC3 implementation](s_pc3_time_coverage_20261009.md)

## PlanningIntent 真实小样本

在同一干净提交上运行 6 条 reviewed Fixture：

- 任务成功：`6/6`
- 必需断言：`37/37`
- 结构提案/目标：目标召回 `9/9`
- 修改 setup/execution：`2/2`、`2/2`
- 目标替换、锁定站点保持、PlanDiff：均 `2/2`
- PlanningIntent 模型决策：`5` 次，provider attempts `5` 次，fallback `0`
- 输入/输出 token：`8542 / 2058`，token coverage `5/5`
- 端到端 P50/P95：约 `2025/2930 ms`

这只是用于检查 PC1–PC3 与真实 PlanningIntent 的接口兼容性的小样本，不是完整 C1 指标，也不代表生产成功率。完整 C0–C4、B0/B3 已有历史报告，但不属于本阶段为 PC3 重新跑的门槛。

报告：[C1 pilot](../../artifacts/evals/S-PC3_C1_pilot_20261009/report.md)

## 行为收口

- “早上出去玩”仍表示整体上午窗口；“早上出发”保留为出发时段并在缺少精确时刻时进入字段级澄清；“早上九点出发”可直接得到 `departure_at`。
- `required_stop_roles` 在没有精确站数时只是必须包含的有序子序列，PlanningIntent/Rule 可以补全其他角色；显式“只安排一站/只吃晚饭”仍保持单站语义。
- “一整天”默认覆盖上午和下午；只有明确晚饭或晚间活动时才加入晚间目标。覆盖目标参与 PlanSpec 优先级和候选排序，无法完整覆盖时返回安全方案并给出未覆盖提示。
- 覆盖评分按站点角色计算，晚饭不能伪装成下午活动；精确短时间窗不会继承全天目标。

## 后续边界

仍需后续处理但不阻塞本阶段：

1. 将结构化恢复动作、澄清策略和前端恢复卡片接入第二阶段；
2. 为全天覆盖补充更多真实交互回归，并在需要时扩展 DemoWorld 数据；
3. 修正 C0 中已知的 grounding fixture/证据缺口及修改 setup 真值口径；
4. 若要发布新的 Resume V2 指标，应在后续业务改动后重新跑完整 C0–C4/B0/B3，而不是复用本阶段小样本。


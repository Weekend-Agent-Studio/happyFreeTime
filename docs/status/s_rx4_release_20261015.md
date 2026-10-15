# S-RX4 最终回归报告（2026-10-15）

## 结论

本轮在干净提交 `97474d08c43b1f1ca63b0a5ec2280702d2d743e0` 上完成了 Frozen C0–C4、Live B0/B3、24 条 reviewed HTTP 多轮集、检索 holdout、后端/前端回归和 E2E。代码与评测工作树均为 clean。

核心安全门通过：

- C2–C4、B3：36/36；必需断言 208/208。
- 所有变体硬约束安全率 7/7，冲突归因 4/4。
- 修改链路在 C0–C4、B0/B3 中均通过（10/10）。
- 多轮集无重复反问、无 HTTP 错误或应用异常；恢复动作均通过类型化 HTTP 接口。

严格意义上，多轮任务尚有 1 条“自动扩大距离后仍无候选”的数据覆盖缺口，因此本报告不把 RX4 宣称为所有 reviewed 任务 100% 成功。

## 评测口径

- Frozen 数据集：`evals/frozen_interpretations_resume_v1.json`
- Frozen 数据集 SHA256：`e8c81da9850f98f0ea0d5874746bce1062ff72fa562bc2b8299d852b1d9ae4f4`
- 多轮集：`evals/s_rx_multiturn_v1.json`，24 条，状态 `reviewed`
- 多轮集 SHA256：`d4dcc61d83f22fa5bb12c14dfe245d31a49559fb571a392d9541a95e938404f9`
- Seed 数据集 SHA256：`e8c81da9850f98f0ea0d5874746bce1062ff72fa562bc2b8299d852b1d9ae4f4`
- Git：`97474d08c43b1f1ca63b0a5ec2280702d2d743e0`，`git_dirty=false`
- BGE：`BAAI/bge-small-zh-v1.5`，512 维；Profile JSON SHA256：`62d0dab83f923ff90db52f47c7da8dd3b8b143349ba580181a77997e6b6833bb`

## Frozen / Live 结果

| 变体 | 任务成功 | 必需断言 | 硬约束 | 冲突归因 | 修改链路 | 主要延迟 |
|---|---:|---:|---:|---:|---:|---:|
| C0 Rule + Rule | 34/36 | 206/208 | 7/7 | 4/4 | 10/10 | P50/P95 461/778ms |
| C1 LLM Intent + Rule | 33/36 | 194/198 fixed | 7/7 | 4/4 | 10/10（9 条可评估） | 665/3309ms |
| C2 Rule + Hybrid | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | 564/1067ms |
| C3 LLM + Hybrid | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | 682/3310ms |
| C4 LLM + Hybrid + Advisor | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | 3917/12411ms |
| B0 Live Router + 下游 Rule | 34/36 | 206/208 | 7/7 | 4/4 | 10/10 | 1746/2429ms |
| B3 Live 全链路 | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | 5725/13660ms |

补充：C3 PlanningIntent objective recall 17/17；C4 16/17；B3 16/17。B3 Advisor 接受 22/27（81.5%），5 次安全回退；Advisor P50/P95 为 2865/3705ms，接受结果的事实 grounding 通过，未出现数字事实违规。

## reviewed 多轮 HTTP 结果

执行文件：`artifacts/evals/S-RX4_multiturn_B3_20261015_final/report.json`。

- 24/24 首轮交互策略符合预期。
- 22/23 eventual task success；另 1 条是预期终止/未完成路径，不计入任务成功分母。
- 5/5 字段反问恢复成功。
- 5/5 Recovery Action HTTP 成功。
- stale action：1/1 正确拒绝。
- 重复反问：0。
- 应用 HTTP 错误：0；应用异常：0。
- 未修改约束保持：6/6；失败修改后 PlanVersion 保持：1/1。
- 平均完成交互轮数：2.04。
- 调用量：Router 44、PlanningIntent 32、Advisor 22、Route Provider 请求 556；已观测输入/输出 token 为 528306/36220。

唯一未完成路径：`automatic_recovery_reenters_planner_trace`。恢复动作正确提交了 `max_distance_km` 的 RequestPatch（12km → 18km），但该 reviewed Fixture 的起点与 DemoWorld 候选仍无可行地点，系统安全停在下一次 `needs_recovery`，没有编造方案或无限重试。该问题属于恢复选项与 DemoWorld 覆盖的产品缺口，不是状态丢失或重复反问。

## 检索专项

16 条 holdout 中 15 条有 ground truth，Hybrid 未发生 fallback 或 provenance 违规：

| 指标 | Rule | Hybrid |
|---|---:|---:|
| Recall@5 | 0.240 | 0.498 |
| nDCG@5 | 0.196 | 0.495 |
| MRR | 0.298 | 0.708 |
| 稳态 P50/P95 | 6/8ms | 137/199ms |

Hybrid 冷启动约 43.7s；不与稳态延迟混报。共 6 条 holdout 为 Hybrid 相对 Rule 的新增 Recall@5 命中。

## 质量与工程回归

- RX3 DemoWorld 质量 Fixture：5 passed。
- 后端：575 passed，78 subtests。
- 前端 Vitest：44 passed。
- 前端生产构建：通过。
- Playwright E2E：15 passed、1 skipped。
- `compileall`、`git diff --check`：通过。

## 已知限制与后续

1. C0/B0 的两条失败是已知语义证据缺口；C1 另有一条 LLM 结构导致的可行性失败。它们没有造成硬约束或冲突安全回归。
2. 自动扩大距离的恢复动作目前只提供有限的 18km 选项；当 DemoWorld 仍无候选时应明确终止或提供更有针对性的下一步，而不是继续重复同一动作。
3. Live B0/B3 是单次观测；B3 的 36/36 不应解释为生产成功率。多轮 22/23 同样是固定 reviewed 集上的能力指标。

## 报告索引

- Frozen：`artifacts/evals/S-RX4_C0_20261015_final/` 至 `S-RX4_C4_20261015_final/`
- Live：`artifacts/evals/S-RX4_B0_20261015_final/`、`artifacts/evals/S-RX4_B3_20261015_final/`
- 多轮：`artifacts/evals/S-RX4_multiturn_B3_20261015_final/`
- 检索 holdout：本轮命令为 `python -m evals.run_retrieval_eval --cases evals/retrieval_holdout_cases.json --json`；输出指标见本报告。

# Resume V2 Release Evaluation Report

日期：2026-09-26
代码提交：`db8603b263b1b1348cc38d7591b2afb40a8b80ea`
评测集：36 条 reviewed Frozen Fixture
Fixture SHA256：`5a50a71c420ebd11a981494bffafc8826d082775c58859e90ff716e4ff87a4f3`
Dataset SHA256：`46901e0535958e8a1bf637d8b3817659c5e5b64524d724a036fe92a5cdd9b139`

模型为 `deepseek-flash`，本地向量模型为 `BAAI/bge-small-zh-v1.5`。默认搜索路径为 Beam；Legacy 仅作为显式模式或受控 fallback。所有规划均使用 Demo World 与可复现 Provider replay。

## Frozen 消融

Frozen 变体使用同一份 reviewed Interpretation，隔离 PlanningIntent、Hybrid Retrieval 和 Advisor 的增量作用。

| 变体 | 任务完成 | Required assertions | 硬约束安全 | 修改执行 | 说明 |
| --- | ---: | ---: | ---: | ---: | --- |
| C0 Rule + Rule | 34/36 | 206/208 (99.04%) | 7/7 (100%) | 10/10 | Rule 语义与词法召回基线 |
| C1 LLM Intent + Rule | 34/36 | 198/200 (99.00%) | 7/7 (100%) | 10/10 | PlanningIntent objective recall 17/17 |
| C2 Rule + Hybrid | 36/36 | 208/208 (100%) | 7/7 (100%) | 10/10 | Hybrid 补齐语义证据 |
| C3 LLM Intent + Hybrid | 36/36 | 208/208 (100%) | 7/7 (100%) | 10/10 | 语义理解与召回组合 |
| C4 LLM + Hybrid + Advisor | 36/36 | 208/208 (100%) | 7/7 (100%) | 10/10 | Advisor 不改变规划安全性 |

C0/C1 的两条失败是 Rule/Profile 语义证据缺口；C2/C3/C4 通过 Hybrid 召回补齐。C1/C3/C4 的 PlanningIntent objective recall 为 `17/17`；C0/C2 为 Rule baseline，不把模型目标召回计入 Rule 能力。

Frozen 端到端 P50/P95（毫秒）分别为：C0 `453/682`、C1 `829/2878`、C2 `517/943`、C3 `724/3109`、C4 `3131/11814`。C4 包含 Advisor 调用，不能与纯规划变体直接比较延迟。

## Live Agent

Live 结果验证真实 Router 的稳定性，不等同于 Frozen 结果，也不将 Frozen 36/36 表述为生产完成率。

| 变体 | 任务完成 | Required assertions | 硬约束安全 | 修改执行 | 其他 |
| --- | ---: | ---: | ---: | ---: | --- |
| B0 Live Router + Rule | 27/36 (75.00%) | 181/196 (92.35%) | 7/7 (100%) | 10/10 | 无模型失败；主要为真实 Router 的澄清/规划判定差异 |
| B3 Live Router + PlanningIntent + Hybrid + Advisor | 30/36 (83.33%) | 187/198 (94.44%) | 7/7 (100%) | 10/10 | Advisor 21/23 接受，2 次安全回退 |

B0/B3 的失败主要集中在实时 Router 对澄清与直接规划的判定，不是硬约束越界或网络失败。B3 总链路 P50/P95 为 `2495/14061ms`；实时模型 token 覆盖率不完整，已按报告单列，不能写成 100%。

## Retrieval 专项

当前 holdout 为 16 条，其中 15 条有可评估标注。

| 指标 | Rule | Hybrid |
| --- | ---: | ---: |
| Recall@5 | 0.240 | 0.537 |
| nDCG@5 | 0.196 | 0.568 |
| MRR | 0.298 | 0.802 |
| 稳态 P50/P95 | 5.3/7.1ms | 123.7/131.8ms |
| 冷启动 | 8ms | 31.8s |
| invalid provenance | 0 | 0 |

Hybrid 的主要收益是语义相关 POI 的召回与排序；冷启动和稳态延迟必须分开报告。

## Advisor 与 Beam

C4 共 27 次 Advisor 决策，接受 `24/27 = 88.89%`，安全回退 `3/27 = 11.11%`；接受结果的 Plan/Evidence/Facts grounding 为 100%。回退不影响规划任务成功率。

Beam 搜索在 Frozen C0–C4 中分别完成约 `4884/4624/4884/4638/4625` 次有界扩展，Legacy fallback 均为 `0/31`；B0/B3 分别约 `3925/3908` 次扩展。Beam 的当前证据是固定预算、骨架公平和 Route 请求受控，而不是在小型 Demo World 上必然提高成功率。

## 结论与发布边界

本轮已证明：

1. LLM PlanningIntent 能恢复并执行软目标，objective recall 在冻结输入上达到 `17/17`。
2. Hybrid Retrieval 相对 Rule baseline 提升了语义召回质量。
3. LLM + Hybrid 在 Frozen 36 条 reviewed 任务上达到 `36/36`，且硬约束安全保持 100%。
4. 实时 Router 仍有澄清判定波动，因此 Live B0/B3 应作为真实链路诊断指标，而非生产成功率承诺。
5. Advisor 采用证据校验与安全回退，解释失败不会污染规划结果。

评测报告原始运行时因主工作树存在用户并行文档修改而记录 `git_dirty=true`；本报告不覆盖这些文档，也不将该状态伪装成干净工作树。发布标签前应在不触碰并行文档的干净 checkout/worktree 中复核提交与报告元数据。

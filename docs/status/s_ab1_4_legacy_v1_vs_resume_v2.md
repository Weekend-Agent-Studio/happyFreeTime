# S-AB1.4：Legacy V1 Completed vs Resume V2

日期：2026-09-27  
用途：记录跨版本共同任务对比；本文件是评测证据快照，不是生产成功率承诺。

## 基线与可复现信息

主对比使用：

- `Legacy V1 Completed`：`legacy-v1-completed-baseline`，commit `5662a94`
- `Resume V2`：`planner-v2-eval-baseline`，commit `bef5fa3`
- `Legacy V1 Original`：`legacy-v1-original-baseline`，commit `90639f3`，仅作为历史附录

共同数据与 Oracle：

- 16 条共同任务
- cases SHA256：`4de1f7b0212f39c5b86903b175f60f117d29a2a1e6043ccfb7c66b91316e1fd6`
- Oracle SHA256：`e7914cc8141569bb38cc8e13cf7e6ff0fb9e75e9a518376481bbda0d14f75e6d`
- dataset SHA256：`4108030641c3b681d867b1add2cc1750cb675a216dac4d67063b9ec16e453970`
- V2 answer contract SHA256：`7d6666b9539b0397cfcfa4f4f0f87f4e9f5eaf929781b4fc55146056c5055c23`
- BGE index manifest SHA256：`ff523d3775c2d1e671b9312ca5a5d9d3da986fe6cd5ac015be0009da6351a7c6`

每个基线运行两次；环境失败只做一次有界重试。V2 首次因评测夹具缺少 geocoding 配置而被标记为无效审计运行，没有计入正式结果。

## 主要结果

| 指标 | Legacy V1 Completed | Resume V2 |
| --- | ---: | ---: |
| 每次任务完成 | 13/16（81.25%） | 16/16（100%） |
| 两次均成功的稳定任务 | 13/16（81.25%） | 16/16（100%） |
| 波动任务 | 0 | 0 |
| 端到端 P50 | 10.0–10.4s | 5.5–5.6s |
| 端到端 P95 | 23.9–25.0s | 7.1–7.3s |

Legacy 两次均失败的 3 条是冲突处理案例：

- `conflict_departure_after_return`
- `conflict_departure_outside_window`
- `conflict_strict_budget`

它们返回了方案，没有按 Oracle 返回预期冲突。该差异体现的是旧系统在确定性冲突诊断上的能力缺口，而不是模型调用失败。

V2 每次运行包含 38 次模型决策和 38 次 Provider attempt；两次输入 token 为 `137,642–138,465`，输出 token 为 `16,152–16,178`。Legacy Adapter 暴露 48 次阶段调用/运行，但没有与 V2 完全同口径的 token 字段，因此不把两者的调用数直接解释为同一种成本。

## 结果边界

- 这组主实验比较的是共同任务上的最终结果与成本，不证明 V2 的多轮恢复优势：5 条不完整输入在两套系统中都直接使用默认值，clarification recovery 为 `0/0`。
- Planner 核心对比只是完整输入的代理实验；不能把它描述成完全隔离 SlotAgent 后的纯 Planner 因果实验。
- 硬约束安全、冲突归因、Hybrid Retrieval、Advisor grounding 等指标应引用同一版本的 Frozen/Live 报告，不从本次 V1 Adapter 结果外推。
- `Legacy V1 Original` 的一次附录运行是 `4/16`，只用于说明历史演进，不作为公平主基线。

## 面试可用结论

在相同 16 条共同任务和两次重复运行下，保留原始 Multi-Agent 架构并补齐最低可运行能力的 Legacy V1 为 `13/16`，Resume V2 为 `16/16`；V2 端到端 P50 约降低 45%，P95 约降低 70%。更稳妥的解释是：V2 将状态恢复、硬约束和规划执行收回确定性 Harness，减少了旧系统在冲突处理和阶段交接上的失败；不是单凭这组实验证明所有收益都来自某一个模块。

完整原始结果见：

- [`S-AB1.4 report.md`](../../artifacts/evals/S-AB1.4/report.md)
- [`S-AB1.4 manifest.json`](../../artifacts/evals/S-AB1.4/manifest.json)
- [`Resume V2 release report`](../releases/resume_v2_release_report.md)

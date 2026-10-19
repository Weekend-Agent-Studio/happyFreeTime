# S-HITL6 Reliability 收口记录（2026-10-18）

## 结论

HITL 恢复链路的收口不应被一个已知的全天方案排序质量用例阻断；该用例不验证恢复流程，也不属于硬约束安全断言。本切片没有修改 Planner 的候选生成或排序逻辑，因此不在本次改动中顺手改变全天方案排序。

## 全天覆盖用例失败的含义

失败用例为：

```text
tests/test_s_rx3_quality.py::S_RX3QualityTest::test_all_day_top_plan_covers_multiple_periods
```

该用例检查“全天”请求的**排名第一方案**至少有 3 站、跨度不少于 600 分钟，并且站点起始时间覆盖上午、下午、晚上。失败意味着当前排名第一的方案没有达到这项体验质量断言；它不等同于违反用户硬约束。

本次已将该质量 fixture 固定注入 `RuleBasedCandidateRetriever`，消除 Rule/Hybrid 环境切换对确定性测试的影响。重跑确认：默认 Beam 模式仍失败，而显式 Legacy 模式通过，因此根因不是召回器环境，而是搜索结果排序差异。

默认 Beam 返回的第一名是 `activity-break-dinner-v1`：09:10 活动、11:20 休息、18:00 晚餐，总跨度 600 分钟；它缺少有效下午活动。另一个 `lunch-activity-dinner-v1` 候选覆盖测试使用的上午、下午和晚间分桶，但以 0.2 分差排在第三。与此同时，当前 PC3 领域语义把未明确晚餐的“全天”目标定义为上午+下午，而旧质量 fixture 仍要求上午+下午+晚间。这里同时存在**排名质量缺口**与**fixture 口径陈旧**，应在独立 Planner 质量切片中统一时间覆盖口径后处理，不应在 HITL 恢复 PR 中临时调权或放宽断言。

## 本切片验证记录

- 后端此前全量：600 passed、1 failed、77 subtests；唯一失败为上述全天覆盖质量用例。
- 最新 HITL/Graph/评测定向回归：98 passed、29 subtests（SQLite reopen 用例使用工作区临时目录执行通过）。
- 最后代码变更后 `compileall app evals tests` 与 `git diff --check` 通过。
- 全天覆盖定向重跑：默认 Beam `1 failed`；显式 Legacy `1 passed`。该差异已独立记录，尚未修复。
- Live B0 8 条诊断回放：报告 `artifacts/evals/S-HITL6K_live_B0_8_final2_20261018/report.json`。脚本路径 8/8，任务类 6/6，取消/新需求 2/2，应用恢复动作 2/2，诊断与动作匹配 2/2，无 HTTP 或应用异常。报告生成时工作树为 dirty，只用于诊断。
- 评测指标已收敛为唯一的 `repeated_ineffective_strategy_count`；删除了含义重叠且命名误导的旧 `repeated_ineffective_recovery_count`。
- Live B0 是单次诊断结果，不是稳定发布基线。

## 发布边界与后续

- HITL 改动已提交并推送至 `codex/s-hitl6-reliability@b541c13`；截至 2026-10-19 仍待 PR 审核和合并，因此本记录不代表 `main` 或新的冻结发布。
- 全天覆盖排序问题保留为独立 Planner 质量项；它不应与 HITL 收口混为一谈。
- “用户明确要求 Demo World 未覆盖的具体类别时，不能静默替代”仍是独立的语义 grounding / 目录覆盖问题，不在本切片内解决。

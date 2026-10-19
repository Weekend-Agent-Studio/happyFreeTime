# Codex Brief：HappyFreeTime 当前事实索引

_最后核对：2026-10-19。本文是低成本接手地图，不替代代码、测试或完整架构说明。开始工作前仍应检查 Git 状态。_

## 版本边界

- `origin/main@9be530e`：已合并 S-RX4、S-HITL6 及交接文档；2026-10-19 拉取核对。
- 当前 S-UI1 开发分支：`codex/s-ui1-product-frontend`，从上述 main 创建；B 切片重构前端布局与视觉系统，尚未代表 main 或新的冻结发布。
- 冻结评测基线：tag `planner-v2-eval-baseline`。冻结指标只属于对应代码、Fixture 和报告，不能被后续分支结果覆盖。
- 先用 `git status --short --branch` 和 `git log -1 --oneline` 验证以上快照是否仍有效。

## 产品与架构一句话

HappyFreeTime 是北京本地生活/周末出行的受约束规划 Agent：LLM 负责开放语义、有限结构提案和推荐说明；确定性 Harness 负责状态、候选生成、外部事实、搜索、硬约束、恢复动作和持久化。

当前主链：

```text
User message
  → Router / TurnProposal
  → TurnCompiler + DecisionContext
  → RequestPatch
  → ConstraintEngine → PlanRequest
  → Readiness / QuestionPolicy
  → PlanningIntent + PlanSpecCompiler
  → Retrieval + Beam + Scheduler
  → Route / Weather / Availability + Verifier
  → Advisor
  → PlanVersion / PlanDiff / SQLite
```

顶部栏编辑和字段反问回答不重新经过 Router；它们直接生成类型化 `RequestPatch`，并与自然语言更新汇入同一个 `ConstraintEngine`。

## HITL 与失败恢复

- 缺失或模糊字段：`QuestionPolicy → interrupt/resume → 字段级 Patch`。
- 硬冲突、无可行方案或修改失败：`RecoveryReasonAdapter → PrimaryBottleneck → RecoveryPolicy → typed RecoveryAction → interrupt/resume`。
- 用户确认动作后仍走 `RequestPatch → ConstraintEngine → 原 Planning/Modification 链路`；没有 Recovery Agent，也没有第二套 Planner。
- 动作由结构化失败证据和有界规则动态生成，不由 LLM 自由编造；具体/策略指纹和恢复历史会抑制重复无效动作。
- 恢复卡片持久化动作、字段变化、前后主因和最终状态；stale revision、错误方案版本和重复点击会被拒绝。

## 主要代码入口

| 工作领域 | 代码入口 |
| --- | --- |
| Graph 与应用入口 | `app/orchestration/entry_graph.py`、`app/orchestration/workflows.py`、`app/api/planning_turn.py` |
| 语义动作 | `app/domain/turn.py`、`app/domain/decision_context.py`、`app/services/router_extractor.py` |
| 请求与硬约束 | `app/domain/constraints.py`、`app/services/request_patch_compiler.py`、`app/services/request_patch_update.py`、`app/services/constraint_engine.py` |
| 反问 | `app/services/question_policy.py`、`app/services/clarification_patch.py`、`app/services/clarification.py` |
| 规划失败恢复 | `app/domain/recovery.py`、`app/services/recovery_reason_adapter.py`、`app/services/recovery_bottleneck.py`、`app/services/recovery_policy.py` |
| 规划与搜索 | `app/services/planning.py`、`app/services/planning_intent.py`、`app/services/plan_spec_compiler.py`、`app/services/candidate_retriever.py` |
| 最终校验与事实 | `app/services/plan_verifier.py`、`app/providers/` |
| 持久化 | `app/persistence/database.py`、`app/persistence/models.py`、`app/persistence/repositories.py` |
| HTTP 与前端 | `app/api/application.py`、`app/api/planning_context.py`、`frontend/src/App.tsx`、`frontend/src/PlanningContextBar.tsx` |
| 前端外壳与视觉 | `frontend/src/SessionNavigation.tsx`、`frontend/src/ConversationHeading.tsx`、`frontend/src/tokens.css`、`frontend/src/shell.css`；保留功能样式在 `styles.css` |
| Trace | `app/domain/run_trace.py`、`frontend/src/runProgress.ts` |
| 评测 | `evals/run_resume_release_eval.py`、`evals/run_s_rx_multiturn.py`、`evals/run_retrieval_eval.py` |

## 已验证能力与明确边界

已实现：类型化请求更新、字段级反问与 checkpoint 恢复、动态 PlanSpec、Hybrid Retrieval、Beam 搜索、时间排程、Provider 复核、硬约束 Verifier、最多三个方案、单站修改、PlanVersion/PlanDiff、Grounded Advisor、安全 Trace、类型化失败恢复。

未实现：真实预订/支付/叫车、长期用户记忆、MCP 主链、生产级实时 POI/库存/价格、任意城市、完整 Temporal/Constraint AST、开放式 Agent loop。

开发 checkpoint 使用 `planner-hitl1-v1` namespace；旧开发 checkpoint 不做迁移。Session/PlanVersion 等产品事实以 Repository 为准，checkpoint 只承担 Graph 暂停与本轮临时状态。

S-UI1B 前端采用本地 V4 原型的暖白/深灰方向：220/64px 折叠会话导航、桌面聊天与详情均分、五个紧凑条件 Chip、欢迎/44px 聊天标题、独立滚动和固定输入区。只重排既有真实能力；右侧现有行程/地图/订单空态/核验及卡片内操作保留至 C/D 切片迁移，尚未完成整个 V4 接入。条件草稿/stale 提示在工作区正常布局中显示，不覆盖顶部标题。

S-UI1C 当前开发分支已实现 V4 风格纵向方案比较：候选卡片只预览；右侧紧凑方案头显示时长、累计路线距离和全员地点费用（不含交通），并承载唯一正式选择入口。预览不会改正式选择；切换已有选择需 Base UI Dialog 确认，只有后端响应确认后才更新，忙碌/失败/过期版本不会假装成功。旧版本可预览但只读；历史消息、刷新恢复、反问时保留的活动方案继续沿用 Session/PlanVersion 锚点。手机通过候选卡的详情入口打开同一工作区抽屉。

C 不改 Planner 或选择 API 合同，也不实现 POI 子页面、地图改造/Tab 收敛（D）、条件编辑和恢复产品流（E），不提供预订或支付。卡片缺图明确占位；模拟 POI 评分不作为候选卡的真实口碑呈现。完整的当前分支证据见 `docs/status/s_ui1c_comparison_20261019.md`；该快照不代表 main、Frozen 或 S-UI1 全部完成。

距离合同注意：`max_distance_km` 当前同时限制候选检索范围与单段路线距离；`total_distance_km` 是另一个全程累计距离上限。顶部编辑尚未开放这两个字段，不能直接把原型“搜索范围”开关当作纯检索半径；后续 E 切片适配。宠物仅讨论为未核实普通偏好，B 未新增该字段。

## 当前可信证据

- 正式 Frozen 指标：见 `docs/releases/resume_v2_release_report.md`，不要用后续单次 Live 结果替换。
- S-HITL6 合并前定向回归记录：98 passed、29 subtests；`compileall` 与 `git diff --check` 通过。
- 最近一次全后端回归：600 passed、1 failed、77 subtests；唯一失败是独立的全天排序质量用例。
- Live B0 8 条 HITL 诊断回放：脚本路径 8/8，无 HTTP/应用异常；报告来自 dirty 工作树，只能证明链路诊断，不是发布成功率。
- HITL 收口证据：`docs/status/s_hitl6_reliability_closeout_20261018.md`。
- S-UI1B 前端验证与范围：`docs/status/s_ui1b_shell_20261019.md`；真实 API 浏览器测试命令为 `pnpm --dir frontend test:integration`（临时 SQLite、规则语义适配器和离线 Provider，不是 Live 质量指标）。
- S-UI1C 当前分支快照及验证：`docs/status/s_ui1c_comparison_20261019.md`；覆盖候选预览/正式选择确认、历史只读和手机详情抽屉，尚未进入 D。
- S-UI1 后续协作与 C 执行任务书：`docs/collaboration/s_ui1_luna_handoff.md`。B 提交 `c3507d9`；连续切片沿用开发分支，默认由侧边会话实现、主会话集中验收，不启动子 agent。
- C 实现提交为 `f4e1911`，尚待 C+D 集中验收。下一片 D 的 Target 任务书：`docs/collaboration/s_ui1d_inspector_task.md`；该任务书不代表 D 已实现。

## 已知问题

1. `tests/test_s_rx3_quality.py::S_RX3QualityTest::test_all_day_top_plan_covers_multiple_periods`：默认 Beam 首方案缺少有意义的下午覆盖，Legacy 可通过。这里同时存在排序质量缺口和旧 fixture 对“全天”要求晚间覆盖的口径差异；应在独立 Planner 质量切片统一语义后修复，不能在 HITL PR 中调权或弱化断言。
2. Demo World 的类别/候选覆盖有限。用户明确要求不存在的类别时，系统不应静默替代；这是独立的 semantic grounding / catalog coverage 问题。
3. Live Router/Advisor 会受模型和网络波动影响。Live 单次结果只作诊断，正式结论必须注明运行条件并与 Frozen 结果分开。

## 深入阅读顺序

1. 当前任务相关代码和测试；
2. `docs/current/resume_v2_architecture.md`：完整已实现链路；
3. `docs/releases/resume_v2_release_report.md`：冻结证据；
4. `docs/canonical/`：目标设计，仅在形成独立判断后阅读；
5. `docs/status/` 与 `docs/archive/`：历史诊断，不是当前契约。

文档权威层级和维护规则见 `docs/README.md`。

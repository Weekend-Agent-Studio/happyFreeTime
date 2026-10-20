# Codex Brief：HappyFreeTime 当前事实索引

_最后核对：2026-10-20。本文是低成本接手地图，不替代代码、测试或完整架构说明。开始工作前仍应检查 Git 状态。_

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

S-UI1B 前端采用本地 V4 原型的暖白/深灰方向：220/64px 折叠会话导航、桌面聊天与详情均分、五个紧凑条件 Chip、欢迎/44px 聊天标题、独立滚动和固定输入区。C/D 已进一步迁移方案比较与详情工作区，尚未完成整个 V4 接入。条件草稿/stale 提示在工作区正常布局中显示，不覆盖顶部标题。

S-UI1C 当前开发分支已实现 V4 风格纵向方案比较：候选卡片只预览；右侧紧凑方案头显示时长、累计路线距离和全员地点费用（不含交通），并承载唯一正式选择入口。预览不会改正式选择；切换已有选择需 Base UI Dialog 确认，只有后端响应确认后才更新，忙碌/失败/过期版本不会假装成功。旧版本可预览但只读；历史消息、刷新恢复、反问时保留的活动方案继续沿用 Session/PlanVersion 锚点。手机通过候选卡的详情入口打开同一工作区抽屉。

C 不改 Planner 或选择 API 合同；POI 子页面、地图改造/Tab 收敛已由 D 实现（见本文末尾），不提供预订或支付。卡片缺图明确占位；模拟 POI 评分不作为候选卡的真实口碑呈现。C 的分支证据见 `docs/status/s_ui1c_comparison_20261019.md`；该快照不代表 main、Frozen 或 S-UI1 全部完成。

S-UI1E1 已把五个条件 Chip 接入统一的 Base UI 编辑弹窗：日期/时间、出发区域、同行人数、人均预算和分类偏好均从服务端摘要初始化，仅发送相对打开时快照的变更；no-op 不请求，取消不保存。弹窗提供焦点约束/返回、ESC 与遮罩关闭、内部滚动、过期 revision/session 编辑失效；时间可精确输入、选预设/时长并用双滑块调整，保持同日与既有开始/出发、结束/最晚到家语义；预算精确值不受便捷滑块范围裁剪。普通条件保存与显式重新规划仍分离，stale 状态显示在工作区。实现入口：`frontend/src/PlanningContextBar.tsx`、`frontend/src/App.tsx`。

S-UI1E2 已在“更多条件”编辑器开放精确站数（1–4）、`max_distance_km` 与 `total_distance_km`。后端 Summary 投影字段值/来源，封闭类型化 `PlanningContextPatch.advanced` 原子编译成现有用户来源 RequestPatch；普通保存保留旧版本并标 stale，显式重新规划才建立新版本。`max_distance_km` 同时约束候选检索范围与单段路线距离，不能标成纯搜索半径；全程累计距离独立编辑。宠物只作为普通偏好标签“携带宠物”，同行入口与偏好入口同步，旁注明确地点携宠许可未经核实；不作为人数、费用或 Verifier 硬约束。入口仍为 `app/api/schemas.py`、`app/api/planning_context.py`、`app/domain/constraints.py` 与上述前端组件。

S-UI1E3 已把反问与恢复接入可追溯的产品交互：仅当前 clarification 可操作，旧问题只读；按后端 `allow_free_text` 限制输入，并对字段使用用户可理解的标签。`request_field` 只呈现服务端给出的选项。恢复条件变更先展示服务端 patch 的当前值/提议值，确认后才发送；取消零请求，并绑定 session、interaction/action、request revision 与 plan version。重复点击受锁保护，切换会话或交互过期会关闭旧确认且忽略旧异步结果。条件保存、重新规划、流式进度和会话恢复均在请求返回时校验 session epoch，避免旧会话结果改写当前状态。取消本轮、保留现有方案、开始新需求保持不同语义；失败可用同一 request_id 重试。实现入口为 `frontend/src/App.tsx`，以 `frontend/src/App.test.tsx` 和 `frontend/e2e/real-api.integration.spec.ts` 覆盖。实现会话单测和构建通过；主会话随后在允许本地服务访问的环境补验条件弹窗与 E3 真实恢复链路，桌面/手机共四项通过并生成截图，见下方当前可信证据。

距离合同注意：`max_distance_km` 同时限制候选检索范围与单段路线距离；`total_distance_km` 是另一个全程累计距离上限。UI 已分别呈现，不推导两者大小关系或将前者描述成几何半径。宠物仅以未核实的普通偏好保存，不代表目录或 Verifier 已确认地点允许携宠。

## 当前可信证据

- 正式 Frozen 指标：见 `docs/releases/resume_v2_release_report.md`，不要用后续单次 Live 结果替换。
- S-HITL6 合并前定向回归记录：98 passed、29 subtests；`compileall` 与 `git diff --check` 通过。
- 最近一次全后端回归：600 passed、1 failed、77 subtests；唯一失败是独立的全天排序质量用例。
- Live B0 8 条 HITL 诊断回放：脚本路径 8/8，无 HTTP/应用异常；报告来自 dirty 工作树，只能证明链路诊断，不是发布成功率。
- HITL 收口证据：`docs/status/s_hitl6_reliability_closeout_20261018.md`。
- S-UI1B 前端验证与范围：`docs/status/s_ui1b_shell_20261019.md`；真实 API 浏览器测试命令为 `pnpm --dir frontend test:integration`（临时 SQLite、规则语义适配器和离线 Provider，不是 Live 质量指标）。
- S-UI1C 当前分支快照及验证：`docs/status/s_ui1c_comparison_20261019.md`；覆盖候选预览/正式选择确认、历史只读和手机详情抽屉。
- S-UI1 后续协作与 C 执行任务书：`docs/collaboration/s_ui1_luna_handoff.md`。B 提交 `c3507d9`；连续切片沿用开发分支，默认由侧边会话实现、主会话集中验收，不启动子 agent。
- C 实现 `f4e1911`，D 实现 `b24548d`，地图→行程滚动定位修复 `718f896`。主会话已完成 C+D 关键 diff/既有断言/代表截图的轻量验收，修复后静态核对收口；主会话未重跑测试。实现会话报告定位 E2E 桌面/移动通过、单测 56/56、构建及 diff 检查通过。
- E1–E3 实现为 `ecffd3f`、`a36f48a`、`822df99`，验收修复为 `ee43f3e`；主会话已轻量验收收口。允许访问本地测试服务后，条件弹窗桌面短屏/375px 两项通过，E3 真实 API 恢复桌面/手机两项通过；修正测试计数器以同时覆盖普通与流式消息端点。此前网络受限记录保留为历史运行证据，当前详见 `docs/status/s_ui1e_conditions_hitl_20261020.md` 末尾补验。此快照未合并到 main，未重跑全套。
- 下一片 F 的 Target 任务书：`docs/collaboration/s_ui1f_modification_closeout_task.md`；范围为单站修改、真实 PlanDiff 与最终产品回归，尚未执行，不推送或合并。

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

## S-UI1D 方案工作区与 POI 详情（2026-10-20）
分类：Current。此分支已把方案检查器收敛为“行程 / 地图”两种主视图；订单不属于当前实现。POI 详情沿用当前响应中的 Stop resource ID 与同响应 PoiPresentation 快照，在工作区内打开并支持回到原视图、滚动位置和焦点。路线图按响应原始路段索引呈现；无坐标或未配置高德浏览器 API 时显示可见文字回退，不生成虚构坐标。核验与来源保留为次级说明。

此实现仍在 S-UI1 连续切片分支，C+D 已完成上述轻量验收。`718f896` 补上从地图切至行程后，将目标路段滚动到实际详情容器的固定头部下方，并在上下文变化时取消待定位目标。真实高德 JS 地图成功路径尚未在本环境验证；临时真实 API 集成覆盖了只读浏览和状态快照不变。

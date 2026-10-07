# S-TRACE6：实时步骤投影与核验页收口

日期：2026-10-07  
分支：`codex/s-trace-run-observability`  
基线：`9969009`（S-TRACE5）

## 结论

S-TRACE6 已完成本地收口。它没有改变 Router、PlanningIntent、Retrieval、Planner、Provider 或 Verifier 的业务决策；本切片把已有 Trace/SSE 能力投影成用户可理解的实时阶段，并收紧核验页的信息边界。

## 完成内容

### 6A：公开阶段生命周期

- 创建流程使用七个公开阶段：`understand`、`compile_request`、`structure`、`retrieve`、`construct`、`verify`、`advise`。
- 每个阶段最多一次 `started` 和一次 terminal 状态：`completed`、`fallback`、`failed` 或 `waiting_input`。
- Observer 连续分配 `sequence`，根据阶段开始/结束时间计算 `duration_ms`，限制事件数量，并校验公开详情的安全长度和格式。
- `waiting_input` 是正常交互终态；公开事件不包含 Prompt、模型原始输出、隐藏推理、密钥、堆栈或原始异常。
- `RuntimeDecision`、`SearchTrace` 和 Evidence 不迁移到公开 Trace。

### 6B：主对话实时步骤

- 前端维护本轮 `progressEvents`，使用纯函数 `projectRunProgress()` 合并同一阶段的 started/terminal 事件。
- 执行中显示真实事件驱动的阶段状态；完成后折叠为“规划完成 · 查看本轮处理过程”。
- 创建、反问恢复、顶部 Patch 和单站修改只显示其真实发生的阶段，不强制套用七步流程。
- 刷新会话后从助手响应中的 `run_trace` 恢复完成态时间线；没有定时器伪造进度，并遵守 reduced-motion。

### 6C：核验页分层

- “依据”改为“核验”。
- 核验页分为：核验概览、需要确认、数据与证据；开发者详情默认可折叠。
- Evidence 按当前选中方案及 `supporting_evidence_ids` 过滤；路线详情不在核验页重复铺开。
- Warning 按 `code + resource_id + source` 去重，只展示当前方案相关风险。
- `VITE_SHOW_DEVELOPER_DETAILS=false` 可隐藏模型、Token、检索、Beam 等诊断字段；只影响展示，不删除响应数据。

## 验证

### 确定性回归

- 后端：`466 passed，39 subtests`
- `compileall app tests`：通过
- 前端单元测试：`41 passed`
- 前端生产构建：通过
- Playwright E2E：`13 passed，1 skipped`（移动端跳过桌面专属布局用例；使用单 worker 以避免并发钩子抖动）
- `git diff --check`：通过

### Frozen 业务评测

评测未调用真实 LLM。产物位于本地忽略目录 `artifacts/evals/`：

| 变体 | 任务成功 | 必需断言 | 硬约束安全 | 冲突归因 | 修改链路 |
| --- | ---: | ---: | ---: | ---: | ---: |
| C0 Rule + Rule | 33/36 | 196/206（条件 196/200） | 7/7 | 4/4 | 准备 9/10，执行 9/9 |
| C2 Rule + Hybrid | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 |

C0 的 33/36 与 S-CORE3H4 的当前基线一致，不是 Trace 改动回归：两条为既有 Rule 语义证据缺口，一条为既有四站修改准备缺口。C2 保持完整通过。

## 数据职责边界

- `PlanningRunTrace`：用户安全的阶段执行过程。
- `RuntimeDecision` / `SearchTrace`：开发和评测诊断。
- `Evidence` / Provider facts：支撑当前方案的事实。
- `Warning` / degradation：降级与待确认风险。

Trace 随 `AgentResponse.run_trace` 写入既有 `planning_runs.response_json`，没有新增 Trace/Evidence 数据表。

## 提交

- `31fd5a8 fix: close public planning trace lifecycles`
- `19ee17e feat: render live planning stage progress`（包含 6B 与共享前端核验页收口）
- 当前收口提交将包含：SSE E2E 夹具更新、架构/README 和本报告。

## 未纳入范围

- 跨进程 SSE 重连和历史事件续传；
- OpenTelemetry、长期 Trace 数据仓库；
- 逐次 Beam expansion 或单条 Provider 请求事件；
- 模型思维过程展示；
- Planner、Router、Advisor 或 Provider 业务优化；
- 新模型调用和定时伪进度。

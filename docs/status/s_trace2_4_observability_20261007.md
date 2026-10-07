# S-TRACE2–4：执行 Trace、前端信息架构与实时进度

状态：完成（S-TRACE2、S-TRACE3、S-TRACE4）

## 完成内容

- PlanningTurnApplication 为每一轮创建 request-scoped `InMemoryRunObserver`；observer 通过显式 Graph config 传递，不进入可持久化 State。
- Graph、Request Compiler、PlanningIntent/PlanSpec、Retrieval、Beam/Scheduler、Provider/Verifier、Modification 和 Advisor 产生统一的安全阶段事件。
- `AgentResponse.run_trace` 与既有响应一同保存，幂等重放返回同一份 Trace；RuntimeDecision/SearchTrace 仍作为开发诊断，不混入公开时间线。
- 前端将“本轮处理过程”“方案依据”“降级与风险”“开发者详情”分区展示，完成后的处理过程只消费 `run_trace`。
- 新增 `POST /api/sessions/{session_id}/messages/stream`；使用 `fetch + ReadableStream` 接收 SSE，旧的非流式 POST 保留并复用同一应用服务。
- SSE 只公开 `PlanningRunEvent`、最终 `AgentResponse` 和安全错误，不暴露 Prompt、模型原始输出、隐藏推理、密钥、堆栈或原始异常。

## 验证

- 后端：`463 passed，39 subtests`
- 前端：`37 passed`
- 前端生产构建：通过
- SSE API 回归：progress/result 事件和普通响应字段合同通过
- Trace 事件最多 64 条，sequence 从 1 连续递增；高频 Beam expansion 和单条 Provider 请求不进入公开时间线。

## 尚未纳入

- 跨进程 SSE 重连与历史事件续传。
- OpenTelemetry、长期 Trace 数据仓库和逐次搜索事件。
- 正式 C0–C4/B0/B3；业务行为未改变，发布前按 S-TRACE5 再执行。

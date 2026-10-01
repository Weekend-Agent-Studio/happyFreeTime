# S-CLEAN0：历史兼容与旧原型清理

日期：2026-10-01
分支：`codex/s-clean0-compat-purge`

## 基线确认

- `origin/main`：`50c7ff8`，已包含 V2 集成提交 `9a22e41`。
- V1 Original：标签 `legacy-v1-original-baseline`，提交 `90639f3`。
- V1 Completed：标签 `legacy-v1-completed-baseline`，提交 `5662a94`；远程分支 `origin/codex/legacy-v1-completed` 保留。
- 后来的 Resume V1 评测基线 `planner-v1-eval-baseline` 未删除、未混淆。

## 已清理

删除了当前主线不再使用的历史源码：

- `Agents/`：V1 Multi-Agent 原型及其旧测试；
- `MCP/`：独立天气 MCP 教学 Demo；
- 根目录 `services/`、`Tools.py`、`main.py`、`main_v2.py`；
- `evals/run_s_ab1_legacy_pilot.py`：旧原型专用评测入口。

删除根目录 `services/` 前，将仍被 V2 使用的本地路线估算迁移到
`app/providers/route.py`，保持原有 Demo 速度和 Haversine 计算行为。

## 保留与延期

MCP 只作为历史教学 Demo 保留在 V1 标签中；当前主链没有 MCP 依赖或 MCP 工具调用。
旧 V1 通过标签/独立分支访问，不再作为主线启动入口。

以下兼容项仍被当前正式路径、测试或 checkpoint 恢复使用，因此本切片没有强删：

- `PlanningIntent.coverage` 及旧 `PlanningIntentProposal`；
- `PlanningSlotProposal` 兼容别名；
- `Command(resume="text")` 和旧 checkpoint 反序列化兼容；
- 其他仍被正式路径读取的旧 checkpoint 转换逻辑。

这些项目延期到 S-CORE1/S-CORE2，不新增替代 Adapter。原先未使用的
`TraceEventRecord` 预留表已在本次删除；当前数据库使用 `create_all`，且没有该表的生产写入路径。

## 正式入口

```text
Frontend → FastAPI app.api.server:app → Entry Graph
         → Router / Enrichment / Question Gate / Planning
```

统一启动方式：

```powershell
.\scripts\start_backend_demo.ps1
```

README 已移除旧源码树说明，并将历史 V1 代码链接固定到冻结标签；MCP 已明确为未来 Adapter/历史 Demo，不属于当前 V2 主链。

## 验证

- `.venv\Scripts\python.exe -m pytest tests -q`：449 passed，36 subtests。
- `.venv\Scripts\python.exe -m compileall -q app evals tests scripts`：通过。
- `git diff --check`：无内容错误；仅有 Windows 换行转换提示。
- FastAPI `/api/health`：`{"status":"ok"}`。
- 新 Session、离线正常规划：成功返回 3 个方案。
- Frozen C2（Rule Intent + Hybrid Retrieval + Beam）：36/36 任务成功，208/208 必需断言，硬约束 7/7，冲突诊断 4/4，修改链路 10/10。

C2 结果目录：`artifacts/evals/S-CLEAN0_C2_20261001/`。该报告的 dirty 标记仅包含清理期间未提交的变更及用户原有未跟踪文件，不代表运行失败。

## 提交

- `dea56c0 refactor: move local route estimate into provider layer`
- `c0c5763 chore: remove archived v1 and mcp prototype`
- `a94abb0 docs: point legacy references to frozen tags`
- `d7735a0 docs: finalize clean0 report metadata`

本分支只完成历史清理，不改变 PlanSpec、ConstraintEngine、Beam、Verifier、Advisor 或持久化架构。

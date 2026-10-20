# S-UI1E 条件编辑与 HITL 状态

分类：Current branch snapshot。日期：2026-10-20。当前分支 `codex/s-ui1-product-frontend`；只记录本分支 E1→E3 的逐片完成证据，不代表已合并到 `main`。未进入 F。

## E1：五项条件编辑器

实现：五个条件入口使用 Base UI Dialog，编辑时保存 session、request revision/view 和服务端摘要快照；仅生成相对快照的类型化 patch。弹窗取消和 no-op 不请求；session/revision/view 改变时旧编辑关闭失效。日期时间支持日历日期、时间精确输入、同日时段预设、时长快捷操作和 06:00–23:00 双手柄快捷滑块；快捷滑块不裁剪外部合法值。出发区域明确为出发点/检索依据；同行数保留 0 人合同和儿童信息；预算精确输入超出便捷范围时不被截断；体验、饮食、场景和避免标签保留分类。

影响文件：`frontend/src/PlanningContextBar.tsx`、`frontend/src/PlanningContextBar.test.tsx`、`frontend/src/App.tsx`、`frontend/src/styles.css`、`frontend/e2e/planning-flow.spec.ts`、`docs/current/CODEX_BRIEF.md`。

验证：

- `pnpm --dir frontend exec vitest run src/PlanningContextBar.test.tsx src/App.test.tsx --maxWorkers=1 --no-file-parallelism`：50 tests passed。
- `pnpm --dir frontend build`：通过（TypeScript 检查及 Vite production build）。
- `pnpm --dir frontend exec playwright test e2e/planning-flow.spec.ts --grep "planning condition dialog|When edits save without replanning" --workers=1`：4 个用例均在 `page.goto(http://127.0.0.1:4173/)` 被环境以 `net::ERR_NETWORK_ACCESS_DENIED` 拒绝，页面未打开；不计为通过，也不能据此判断浏览器交互行为。
- 未取得浏览器截图；保留浏览器抽查为未验证项。

提交：`ecffd3f feat(ui): productize planning condition editors`。

## E2：高级条件和宠物偏好

实现：顶部“更多条件”复用同一编辑弹窗，允许设置/清除精确站数、检索及单段距离上限、全程累计距离上限；仅发送变更字段。DTO、领域模型和 UI 分别拒绝非整数/1–4 以外站数，以及非有限、非正距离。Summary 提供规范值与来源；高级 patch 在一个 base revision 下原子编译到 ConstraintEngine。降低精确站数仍保留必需角色并返回既有结构冲突。普通保存不建 PlanVersion，只标旧方案 stale；显式 replan 和刷新恢复由真实 API 场景验证。宠物以唯一普通偏好标签“携带宠物”持久化，同行与偏好编辑器同步，且明确提示地点许可尚未核实。

关键文件：`app/api/schemas.py`、`app/api/planning_context.py`、`app/domain/constraints.py`、`tests/test_api.py`、`tests/test_constraint_engine.py`、`frontend/src/PlanningContextBar.tsx`、`frontend/src/PlanningContextBar.test.tsx`、`frontend/e2e/real-api.integration.spec.ts`。

验证：

- `python -m pytest tests/test_api.py::ApiTest::test_advanced_planning_conditions_are_typed_persisted_and_replanned_explicitly tests/test_api.py::ApiTest::test_advanced_planning_conditions_reject_invalid_types_and_ranges tests/test_constraint_engine.py -q`：14 passed，7 subtests passed。
- `pnpm --dir frontend exec vitest run src/PlanningContextBar.test.tsx src/App.test.tsx --pool=forks --maxWorkers=1`：53 passed。
- `pnpm --dir frontend build`：通过 TypeScript 检查和 Vite production build。
- `pnpm --dir frontend exec playwright test --config playwright.integration.config.ts e2e/real-api.integration.spec.ts --grep "real API preserves selection" --workers=1`：2 passed（desktop-api、mobile-api，47.5 秒），覆盖高级条件 set → stale → reload restore → explicit replan，以及宠物偏好在同行/偏好入口间同步并经 API 保存。
- 第一轮浏览器运行发现条件弹窗底部按钮被遮罩拦截；弹窗层级已修正并重跑通过。第一轮集成测试的 replan 断言也改为等待 stale 提示消失，避免把异步规划中的过渡状态当作完成状态。
- 截图：`.impeccable/review/s-ui1e-advanced-desktop-api.png`、`.impeccable/review/s-ui1e-advanced-mobile-api.png`、`.impeccable/review/s-ui1e-pet-desktop-api.png`、`.impeccable/review/s-ui1e-pet-mobile-api.png`。

提交：E2 作为独立提交交付；准确 commit ID 列于最终交付记录。未推送。

## E3：Clarification / Recovery 产品交互

实现：只有当前反问卡片可操作；过期问题保留为只读历史。按 `allow_free_text` 限制自由输入，字段名映射为产品用语；`request_field` 只提交服务端提供的选项。恢复 patch 先以服务端提案展示当前值和建议值，用户确认后才提交，取消不会发请求。确认绑定会话、交互/动作、请求 revision 和方案版本；会话切换、上下文变化或交互过期会关闭确认，旧会话异步响应不能覆盖当前会话。恢复动作有重复提交锁；取消本轮、保留当前方案、开始新需求分别消费服务端响应，开始新需求会清除前端旧方案视图但不自行修改后端选择。失败保留可重试操作，沿用原 request_id。

关键文件：`frontend/src/App.tsx`、`frontend/src/App.test.tsx`、`frontend/src/styles.css`、`frontend/e2e/real-api.integration.spec.ts`。

验证：

- `pnpm --dir frontend exec vitest run --pool=forks --maxWorkers=1 --testTimeout=15000`：5 files passed，69 tests passed。显式增大 Vitest 单测超时用于规避本机低速导致的 5 秒超时；没有跳过断言。
- `pnpm --dir frontend build`：通过 TypeScript 检查及 Vite production build。
- `git diff --check`：通过。
- `pnpm --dir frontend test:e2e --workers=1`：未通过；28 项在打开 `http://127.0.0.1:4173/` 时收到 `net::ERR_NETWORK_ACCESS_DENIED`，2 项跳过。失败发生在页面加载之前，不能证明浏览器交互通过或应用断言失败。
- `pnpm --dir frontend test:integration`：未能启动本地 FastAPI/Vite 浏览器链路。单独检查 `127.0.0.1:8007` 时 PowerShell 报告套接字访问权限不允许；未得到集成用例结果。
- 已增加真实 API/临时 SQLite 场景，覆盖反问、预算恢复提案取消零写入、确认后服务端条件变化及结果；由于 localhost 限制，本轮未能执行该场景。
- E3 新截图未生成。E1/E2 截图仍在 `.impeccable/review/`，不能代替 E3 反问/提案/结果截图。

提交：与 E3 实现和当前分支快照更新一并作为本片独立提交。未推送、未合并；准确 commit ID 见交付记录。

## E 验收补丁（2026-10-20）

本次修补三处验收问题：

- `openSession` 为恢复结果增加 session epoch 校验；条件保存与显式重新规划在成功、异常和 `finally` 路径均忽略已切换会话的结果，流式进度回调也校验请求 epoch 和 session id。发送请求以 `sessionIdRef` 作为当前会话的优先来源。
- Base UI 独立 Popup 明确使用视口居中定位；新增 1440×540 短屏检查，断言弹窗中心和“保存条件”按钮处于视口内，并准备输出 `.impeccable/review/s-ui1e-context-dialog-desktop.png`。
- E3 集成场景将预算回复改为“人均1元，严格控制，不超预算”，确认恢复后等待 lifecycle 成为 `succeeded`，再轮询真实 session 直到严格预算字段持久化为 `false`。

验证结果：

- `pnpm --dir frontend exec vitest run src/App.test.tsx --pool=forks --maxWorkers=1 --testTimeout=15000 --testNamePattern="in-flight answer|in-flight condition save|in-flight replan" --reporter=dot`：3 passed。
- `pnpm --dir frontend exec vitest run src/App.test.tsx --pool=forks --maxWorkers=1 --testTimeout=15000`：51 passed。
- `pnpm --dir frontend build`：TypeScript 检查和 Vite production build 通过。
- 短屏弹窗 Playwright 定向运行：桌面和手机均在 `page.goto(http://127.0.0.1:4173/)` 收到 `net::ERR_NETWORK_ACCESS_DENIED`，未进入应用断言；截图未生成。
- E3 桌面/手机真实 API 定向运行未得到测试结果：本地链路启动无输出并在等待后中断；单独 API 用例也在约一分钟内无输出，手动中断。未将这些尝试记为通过。
- `CODEX_BRIEF.md` 已更新当前 session 异步保护覆盖范围与验证状态。

未决：条件弹窗实际视觉位置/短屏可见性，以及 E3 恢复动作真实 API 桌面与手机场景，仍需在允许浏览器访问本地服务的环境验证；未取得本补丁截图。

## 未决限制

- E1 在其独立提交时未能访问浏览器；E2 的真实桌面/手机 API 浏览器链路已覆盖条件取消、普通保存、stale、刷新和重新规划，但 E1 所有 ESC/键盘/焦点路径仍需更全面抽查。E3 单测与构建通过，但当前 sandbox 禁止 localhost socket，浏览器 E2E、真实 API 集成和 E3 截图仍待环境允许后补验。
- 高德 JS SDK 在线成功路径仍是既有未验证项，不属于 E。

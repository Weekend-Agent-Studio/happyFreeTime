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

待实施。完成后在此记录真实交互链路验证、前端最终门槛、截图路径和 commit。

## 未决限制

- E1 在其独立提交时未能访问浏览器；E2 的真实桌面/手机 API 浏览器链路已覆盖条件取消、普通保存、stale、刷新和重新规划，但 E1 所有 ESC/键盘/焦点路径仍需更全面抽查。
- 高德 JS SDK 在线成功路径仍是既有未验证项，不属于 E。

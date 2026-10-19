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

提交：待 E1 提交后由后续 E2 记录准确 commit。

## E2：高级条件和宠物偏好

待实施。完成后在此记录实际字段 seam、UI 行为、定向 API/浏览器结果和 commit。

## E3：Clarification / Recovery 产品交互

待实施。完成后在此记录真实交互链路验证、前端最终门槛、截图路径和 commit。

## 未决限制

- E1 浏览器交互与截图尚未验证；自动化浏览器无法访问本机 Vite 服务。
- 高德 JS SDK 在线成功路径仍是既有未验证项，不属于 E1。

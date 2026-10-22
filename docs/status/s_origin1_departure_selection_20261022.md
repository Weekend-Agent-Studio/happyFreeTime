# S-ORIGIN1：出发地明确化与地点选择

分类：Current branch snapshot。日期：2026-10-22。分支：`codex/s-ui2-planning-polish`，基于 `origin/main` 的连续 S-UI2 开发分支；未推送、未合并。

## O1：名称与默认地点说明

- 顶部条件项统一称“出发地”，编辑器标题为“从哪出发”；说明它用于计算前往首站的交通，未指定其他活动范围时也会影响附近候选。
- 当前出发地展示地点名称和完整地址，并用文字说明演示默认、用户已选、待确认与保存失败状态。演示默认点显示为“演示默认出发点”，附注“固定位置，并非你的实时位置”；没有复制默认坐标或冒称真实街道/地标。
- 旧地点解析/保存失败时保留当前值，并在编辑器明确标出“修改失败 · 当前出发地未变”。O1 没有更改 `location` 与 `planning_area` 的领域语义或 API。
- 验证：`pnpm --dir frontend exec vitest run src/PlanningContextBar.test.tsx src/App.test.tsx`，82/82 通过；`pnpm --dir frontend build` 通过。浏览器截图尚待 O2 完成后统一验证。

## O2 状态

类型化地点搜索、服务端地点身份复核、显式恢复演示默认点及真实 API/SQLite 集成流程尚未开始。O2 完成前，本界面仍使用既有自由文本地理解析入口。

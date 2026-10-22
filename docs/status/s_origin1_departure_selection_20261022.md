# S-ORIGIN1：出发地明确化与地点选择

分类：Current branch snapshot。日期：2026-10-22。分支：`codex/s-ui2-planning-polish`，基于 `origin/main` 的连续 S-UI2 开发分支；未推送、未合并。

## O1：名称与默认地点说明

- 顶部条件项统一称“出发地”，编辑器标题为“从哪出发”；说明它用于计算前往首站的交通，未指定其他活动范围时也会影响附近候选。
- 当前出发地展示地点名称和完整地址，并用文字说明演示默认、用户已选、待确认与保存失败状态。演示默认点显示为“演示默认出发点”，附注“固定位置，并非你的实时位置”；没有复制默认坐标或冒称真实街道/地标。
- 旧地点解析/保存失败时保留当前值，并在编辑器明确标出“修改失败 · 当前出发地未变”。O1 没有更改 `location` 与 `planning_area` 的领域语义或 API。
- 验证：`pnpm --dir frontend exec vitest run src/PlanningContextBar.test.tsx src/App.test.tsx`，82/82 通过；`pnpm --dir frontend build` 通过。浏览器截图尚待 O2 完成后统一验证。

## O2 状态

已完成 O2。顶部编辑器用 300ms 防抖搜索北京市地点，候选列出名称、区和地址；输入文字本身不可保存，用户必须选中结果并在确认区保存。候选有无结果、请求失败、演示模式/在线失败降级的显式状态；支持上下键、Enter、Escape、触屏选择，并在编辑查询、关闭弹窗、切换会话或 revision 变化时清除/取消旧请求。

后端复用 `GeocodingProvider` 能力，提供封闭的 `GET /api/departure-locations/search?q=`。搜索只返回地点身份与展示字段，不返回客户端可提交坐标；高德由服务端调用 POI Text Search，选择后按 provider ID 再查 POI Detail。条件 DTO 现在只接受两种 Where set：`{kind: "default"}` 或 `{kind: "provider_place", provider_id}`。服务端解析最终地点，按已核实的名称、地址和坐标编译到现有 `RequestPatch.location`；客户端坐标/名称会被 schema 拒绝。默认选择直接恢复该 Session 的 `environment.default_location`。自由文本出发语义链仍保持原样，clear 合同保留。

搜索不修改会话、revision、方案版本或正式选择。保存仍沿用 `base_revision`/`request_id` 更新路径：条件改变后保留旧版本并置 stale，不触发 Router 或重新规划；用户需要明确按新条件重规划。没有高德服务配置时，Mock 只提供已配置的北京“国贸”和“海淀黄庄”，清楚标为有限演示地点；在线搜索异常会标记并退回演示列表，在线地点 ID 解析异常不会改用演示坐标。

坐标复核：现有内部 `GeoLocation`/路线坐标合同采用 GCJ-02；高德 POI 搜索/详情坐标沿用此坐标系，未添加新转换，也未改变规划目录或 WGS84 目录适配。

验证：

- `pnpm --dir frontend test`：5 个文件、98 项通过。
- `pnpm --dir frontend build`：通过。
- `.venv/Scripts/python.exe -m pytest tests/test_geocoding_provider.py tests/test_planning_context_places.py -q`：12 项通过。
- `pnpm exec playwright test --config=playwright.integration.config.ts --list`：桌面/手机共 8 项可收集；这只是 spec 解析，不代表浏览器断言通过。
- `git diff --check`：通过。
- 尝试运行 `test_planning_context_patch_updates_all_five_typed_sections` 时，Windows 沙箱在 `TestClient.__enter__` 创建 asyncio 本地 self-pipe 的 `socket.accept` 处等待；未进入测试断言，之后中止。Playwright 集成也停在本机 FastAPI/Vite 健康检查启动阶段，未进入页面断言；没有生成桌面/手机截图。
- 没有配置或调用真实高德 Web Service Key，线上搜索和实时数据质量尚未验证。Mock/fixture 测试不代表 Live 搜索质量。

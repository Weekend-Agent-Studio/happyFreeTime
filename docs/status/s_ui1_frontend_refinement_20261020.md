# S-UI1 前端体验修整

分类：Current branch validation snapshot。2026-10-20。记录 `codex/s-ui1-product-frontend` 上的前端体验修整；分支已推送，尚未合并到 main。

## 本轮范围

主要参考相邻目录 `happyfreetime-react-prototype-v4` 的日期时间编辑和详情信息层级，保留同一分支上的卡片、导航、token 与时间线布局改动。未改后端 API、Planner 或选择/恢复的领域语义；未创建 PR 或合并。

- 新增 `frontend/src/DateCalendar.tsx`：本地日期月历、月份切换、选中态、方向键/Home/End/PageUp/PageDown 导航。换月本身不改条件，选择日期才更新本地值，保存继续生成同一类型化 Patch。
- 日期弹窗改为桌面月历/时间双栏，手机单栏内部滚动；保留精确日期/时间输入与清空语义。预设对齐 V4 的上午 09–12、下午 12–18、晚上 18–22，增加 3 小时时长，明确选中态。
- 时间滑块拖动保持起止有序；手动输入反序仍有原有校验。06–23 以外的精确输入不截断；修复滑块轨道与说明文字重叠。
- POI 页不再重复整块方案选择头，使用固定、紧凑的返回栏；返回仍恢复原 Tab、滚动与焦点。移动详情通用标题和说明收紧，修复 Base UI Title/Description 的样式选择器不匹配。
- 返程同时显示交通方式/时长；路线行使用可理解的估算提示，完整来源仍保留于 title 和核验区。地点标签去重。

## 验证

- 全量前端单测：79/79 passed（5 个测试文件）。
- 定向桌面/375px 浏览器测试：2/2 passed，覆盖日历/时间编辑、方案预览、紧凑 POI 头及返回行程。
- 生产构建通过 TypeScript 检查与 Vite 构建。
- `git diff --check` 通过。
- Impeccable detector 已运行一次；报告主要为既有颜色/圆角与旧 DESIGN sidecar 的 advisory，不等于业务测试失败；本轮不扩大到设计配置迁移。
- 真实 API 集成：`pnpm test:integration` 6/6 passed（1.6 分钟），覆盖桌面/手机真实 FastAPI/临时 SQLite 的选择、条件、刷新、反问/恢复，以及单站替换/PlanDiff/失败恢复。外部 Provider 为离线数据，不是 Live 质量指标。

截图：`.impeccable/review/ui-refinement-{time,trip,poi}-{desktop,mobile}.png`。来自本分支本次完整实现；桌面/手机定向浏览器用例已更新并验证时间、行程、地点详情视图。

## 保留边界

这是一轮重点界面修整，不是全前端无缺陷保证。真实高德 JS SDK 成功路径、实时 POI 商业事实、Planner 排序质量未纳入；原型的婴儿人数、假订单和纯检索半径没有复制。`.tmp-probe/`、`.tmp-pytest/`、`.tmp-verify/` 保持原状。

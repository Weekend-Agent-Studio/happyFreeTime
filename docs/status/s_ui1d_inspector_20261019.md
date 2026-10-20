# S-UI1D 方案工作区与 POI 页面状态

分类：Current implementation snapshot，记录于 2026-10-20。此状态位于 codex/s-ui1-product-frontend，基于 C 提交 f4e1911 和 D 任务书提交 8a2b8b7；等待主会话对 C+D 集中验收。

## 本片行为

- 方案检查器主标签为“行程”和“地图”；核验与来源作为次级折叠内容。保留 C 的版本安全选择流程。
- 行程按原始计划顺序呈现起程、站间路线、到站、离开、停留和响应实际提供的返程；路线按钮会转到地图聚焦对应原始路段，地图列表可返回行程定位。
- POI 在当前工作区打开，返回时恢复原标签、滚动位置和焦点。Stop 与 PoiPresentation 由当前响应资源 ID 关联，切换计划/会话/版本后丢弃旧详情。
- 地图点位和路线操作分离；缺少路线 geometry 或浏览器地图配置时显示可见文字回退，不构造坐标。POI 不把模拟评分/评论当作事实，费用与展示参考人均分列，未知数据提示待确认，外链使用编码后的地点搜索查询。
- 仅当前活动版本中正式选中的方案、且当前条件未过期或待确认时，站点替换操作可用；查看详情本身为只读。

## 验证结果

- pnpm --dir frontend test：5 个测试文件通过，56 个测试通过。
- pnpm --dir frontend build：通过，TypeScript 检查与 Vite 生产构建完成。
- pnpm --dir frontend test:e2e --workers=1：26 项通过，2 项在移动项目中按桌面专属契约跳过。覆盖行程/地图切换、POI 返回滚动和焦点、375px sheet、ARIA 键盘行为及既有产品流程。
- pnpm --dir frontend test:integration：桌面与移动真实 API 各 1 项通过；POI/地图浏览期间无新增 POST/PUT/PATCH/DELETE，请求快照中的 selected plan、active version 与 request revision 未变。
- git diff --check：通过。

## 截图

截图位于忽略目录 .impeccable/review/：

- s-ui1d-desktop-trip.png
- s-ui1d-desktop-poi.png
- s-ui1d-desktop-map.png
- s-ui1d-mobile-poi.png
- s-ui1d-mobile-return.png
- s-ui1d-mobile-trip.png

## 未决问题

当前真实 API 集成环境未配置高德 JS API，并且真实响应不保证包含可绘制 geometry，因此本片验证了可见文字回退和只读状态，未验证高德在线地图成功加载及真实地图标记交互。订单、下单及 E/F 后续能力未纳入本片。
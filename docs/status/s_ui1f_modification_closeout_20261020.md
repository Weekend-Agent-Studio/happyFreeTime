# S-UI1F 单站修改与产品化收口

分类：Current branch snapshot。日期：2026-10-20。分支：`codex/s-ui1-product-frontend`。基于 `ea0f484` 开始 F；本记录描述未合并开发分支，不是发布或 Frozen 证据。

## 行为与范围

- 只允许从当前活动版本的正式已选方案发起站点替换。草稿捕获会话、版本、方案、站序、资源/角色和条件 revision；切换会话、预览方案、版本或条件失效时不能把旧目标提交到新状态。
- 草稿明示目标站点、快捷条件、自定义偏好和取消方式。手机从详情抽屉发起时会在主工作区操作；ESC/取消关闭草稿、恢复抽屉与焦点，不请求或变更正式选择。
- 替换使用既有类型化消息合同。快捷“全程更近”表示 required 的全程距离目标；多击不会重复提交，网络失败重试沿用同一逻辑 request ID。
- 成功时只采纳服务端返回的新版本和候选，新候选仍是预览，需用户再次正式选择。旧版本的正式选择不会带入新版本。失败时原正式方案继续可见，并可进入 E3 恢复流程。
- 变更明细按响应版本及 `new_plan_id` 绑定到具体候选，显示所有返回的替换站、锁定站及全程距离、时长、地点费用 delta。缺少字段/明细明确降级，不伪造锁定事实或差值。
- 清除了旧隐藏 PlanDiff 文案，新增候选级断言、桌面/手机浏览器场景及真实 API/临时 SQLite 场景。

## B–F 链路状态

| 切片 | 状态与边界 |
| --- | --- |
| B | `c3507d9`：前端外壳与基础视觉系统。 |
| C | `f4e1911`：候选比较与预览/正式选择边界；主会话轻量验收接受。 |
| D | `b24548d`，定位补丁 `718f896`：行程/地图工作区与 POI 详情；主会话轻量验收接受。 |
| E1–E3 | `ecffd3f`、`a36f48a`、`822df99`；修复 `ee43f3e`：条件、反问、恢复和跨会话异步保护。 |
| F | 本次实现与回归收口；提交后仍待主会话集中验收。 |

这些提交沿用同一开发分支，尚未推送或合并。F 未增加后端 API 或 Planner 能力，不代表全产品发布就绪。

## 实际验证

- `pnpm --dir frontend test`：5 个文件、77 项通过。
- `pnpm --dir frontend build`：TypeScript 检查和 Vite production build 通过。
- `pnpm --dir frontend test:e2e --workers=1`：30 项通过，2 项按设备条件跳过；覆盖桌面与 375px 布局。
- `pnpm --dir frontend test:integration`：6 项通过；使用本地 FastAPI、临时 SQLite、离线规则/Provider fixture。确定性替换验证候选、PlanDiff、锁定站点、正式选择/刷新和无解恢复；请求路径同时检查 `/messages` 与 `/messages/stream`。
- `git diff --check` 与 `git diff --cached --check`：均通过。

代表截图位于 `.impeccable/review/`：

- Mock 回归：`s-ui1f-desktop-replacement-draft.png`、`s-ui1f-desktop-replacement-diff.png`、`s-ui1f-desktop-replacement-recovery.png`；对应 `s-ui1f-mobile-replacement-{draft,diff,recovery}.png`。
- 真实 API/SQLite：`s-ui1f-desktop-real-{draft,diff,recovery}.png`；对应 `s-ui1f-mobile-real-{draft,diff,recovery}.png`。

## 未验证与后续边界

- 在线高德地图 JS 成功加载/真实标记交互未验证；此处地图场景沿用离线或回退环境。
- POI 商业、营业、价格等信息仍受演示目录/Provider 数据限制；不能视为实时地点数据质量评估。
- Planner 排序质量缺口继续按 brief 中独立问题跟踪；F 未调整排序或搜索策略。
- 预订、支付、叫车和任何交易动作不在实现范围。

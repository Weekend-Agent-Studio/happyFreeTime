# S-UI1C：纵向方案比较与正式选择（2026-10-19）

状态：Current branch snapshot，基于 `codex/s-ui1-product-frontend` 的开发分支证据；不是 main、Frozen 发布或 S-UI1 总体验收。

## 行为变化

- 方案按后端返回顺序纵向比较，卡片只切换预览，不调用选择 API。候选数量不补齐；卡片呈现标题、图片或明确占位、总时长、累计 RouteLeg 距离、全员地点费用（不含交通）及后端风险/推荐提示。
- 右侧紧凑方案头显示当前预览/正式选择状态与同一组指标，提供正式选择入口及返回已选方案。已存在正式选择时，切换候选先经 Base UI Dialog 确认；取消或 ESC 不请求后端。只在后端返回同一活动 PlanVersion 的 `selected_plan_id` 后更新 UI。
- 选择目标绑定会话、活动 PlanVersion 和 plan ID。会话/版本切换使弹窗或响应失效；忙碌时阻止重复请求，失败保留原选择并显示错误。草稿、stale 条件和待处理恢复会阻止新的正式选择。
- 历史版本保留预览，但缺少当前版本锚点或与活动版本不符时只读，不以 plan ID 猜归属。已有会话刷新恢复、反问时保留活动方案和多轮富回复继续使用原 Session/PlanVersion 数据。
- 手机候选卡的详情入口打开同一方案工作区抽屉；正式选择、指标及详情在抽屉中呈现。图片来源/示意声明沿用事实数据，缺图使用占位，模拟评分不出现在比较卡片上。

## 关键文件

- `frontend/src/App.tsx`、`frontend/src/styles.css`：候选预览、正式选择确认、右侧/手机工作区头部与样式。
- `frontend/src/App.test.tsx`：费用/距离/缺图、单候选、卡片预览、忙碌、失败、选择切换、历史版本只读、会话变化和恢复场景。
- `frontend/e2e/planning-flow.spec.ts`、`frontend/e2e/real-api.integration.spec.ts`、`frontend/e2e/visual-capture.spec.ts`：桌面/手机交互、刷新与真实 API 流程。

本片未修改后端 Planner、API/领域合同、计划版本语义、订单能力或条件编辑；不包含 D 的 POI 子页面、地图改造、Tab 收敛与位置恢复，也不包含 E/F。

## 验证

- `pnpm --dir frontend test`：52 passed。
- `pnpm --dir frontend build`：TypeScript 检查与 Vite 生产构建通过。
- `pnpm --dir frontend test:e2e --workers=1`：24 passed、2 skipped（两个桌面专用布局用例在手机项目跳过）。覆盖离线规划流、切换确认/取消/ESC 与焦点返回、刷新、恢复和桌面/手机详情入口。
- `pnpm --dir frontend test:integration`：2 passed（desktop-api、mobile-api）；真实 FastAPI + 临时 SQLite 验证选择快照、PlanVersion 不变、刷新恢复及保存条件后的 stale 状态。外部 Provider 为离线确定性数据，不代表 Live 质量。
- 浏览器测试首次在受限沙箱中因 `ERR_NETWORK_ACCESS_DENIED` 无法连接本机测试服务；在允许访问本地测试服务器的受审运行中，完整 E2E 与 API 集成均通过。该环境阻断不计为产品失败。
- 代表性正式选择截图已目视检查：桌面 `.impeccable/review/s-ui1c-selected-desktop-api.png`，手机 `.impeccable/review/s-ui1c-selected-mobile-api.png`。目录受 `.gitignore` 忽略，不随提交发布。

## 限制与后续

- 这是开发分支快照，尚未合并到 main，也不代表整个 S-UI1 完成。
- 选择只保存当前活动版本的 `selected_plan_id`；不创建 PlanVersion，也不预订、支付或执行行程。
- POI 商业信息与路线 Provider 的真实度仍受现有数据源限制；必须沿用既有模拟数据、路线降级和来源提示。
- 下一片 D 才处理右侧 POI 子页面、地图/Tab 交互和返回位置；本记录不把这些计划写作已实现能力。

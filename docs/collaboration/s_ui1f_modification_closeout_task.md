# S-UI1F：单站修改与产品化收口

分类：Target。2026-10-20。只授权 F，不包含发布、推送、合并或新业务扩展。

## 接手方式

- 继续原实现会话；先读根 AGENTS.md、本任务书，按需查 CODEX_BRIEF。无需重读完整 C/D/E handoff 或历史报告，不启动子 agent。
- 先 `git status --short --branch`、`git log -6 --oneline`。沿用 `codex/s-ui1-product-frontend`，保留 E1 `ecffd3f`、E2 `a36f48a`、E3 `822df99`、竞态/弹窗修复 `ee43f3e` 及后续主会话验收提交，不回 main 重建。
- `.tmp-probe/`、`.tmp-pytest/`、`.tmp-verify/` 是已有未跟踪内容，不纳入提交；不要强杀进程、递归删除或顺手清理。若测试确需处理被占用目录，先定位持有者和本次测试目录归属。
- 参考相邻 `happyfreetime-react-prototype-v4/src/App.tsx`、`src/styles.css` 的修改/对比视觉，正式接口和代码决定业务事实，不连接 Figma，不复制原型假结果。

## 目标与已存在能力

让用户在合法正式已选方案中发起单站替换，清楚知道修改目标、条件和结果；能比较真实 PlanDiff，失败时保留原方案，刷新后仍能理解版本变化。完成一轮 S-UI1 的端到端回归并清理有证据的遗留代码。

已有可复用能力，不重造：

- `App.tsx` 的 ReplacementDraft、replacementCommandFromDraft、startReplacement、chooseReplacementMode、submit、canReplaceInspectorPlan。
- 当前快捷条件是立即提交动作，界面已有提示；自由输入通过发送提交。保留此约定并确保标签明确，不额外增加统一确认步骤。
- ConversationCommand 已有 base_plan_version_id/base_plan_id、stop_index/resource_id/role、replacement_criteria；“全程更近”是 required 的 route_objective，其他语义偏好是 preferred，不能降级或混淆。
- `PlanDiff` 已含 from/to version、base/new plan ID、locked_stops、replacements 与距离/时长/费用 delta；当前 RichPlanningReply 已有初步展示。
- E 已建立条件、反问、恢复和会话异步保护。F 应沿用，不复制另一套交互状态或修改 API。

## 定向阅读入口

1. `frontend/src/App.tsx` 上述函数、RichPlanningReply、Inspector/TripPanel、POI 页面与选择门禁；`types.ts` 的 ConversationCommand/PlanDiff；`api.ts` 的消息流与幂等请求。
2. `frontend/src/App.test.tsx` 的 replacement、locked stop、版本/stale/恢复/竞态断言；`frontend/e2e/planning-flow.spec.ts` 与 `real-api.integration.spec.ts`。
3. 按需只读 `app/api/planning_turn.py` 中修改入口的版本/正式选择验证，`tests/test_plan_modification.py`、`tests/test_api.py` 中修改与 PlanDiff 场景，定位实际领域行为。
4. `frontend/DESIGN.md`、tokens/shell/styles 中任务涉及的规则。不通读整个后端或技能库。

## 必须完成的行为

### 1. 合法入口和草稿生命周期

- 只有当前 active version、正式已选方案、committed 且非 stale、无待处理反问/恢复/请求/选择确认时可发起替换。历史版本、纯预览、缺版本证据一律只读；UI 与提交时均检查，后端仍最终裁决。
- 在行程的站点入口清楚显示“换这站”；POI 页面如已有替换入口可复用同一流程，若没有则不强制新增第二入口。
- 替换草稿明确展示第几站、当前地点、可选条件、立即提交快捷动作、自由输入和取消。手机从详情 sheet 发起后必须能发现并操作草稿，不能被仍打开的模态抽屉遮挡；关闭/返回和焦点转移合理。
- 草稿捕获 session、原版本、plan、站序和 resource ID。切会话、新版本、正式选择变化、条件更新或新的待交互使目标失效时，清理或禁用旧草稿并提示；不能把旧 resource/站序套到新方案。提交前重新核对捕获目标，防止 UI 可点但目标已失效。
- 取消本地草稿不发请求、不改方案/版本/正式选择；自定义输入不意外作为普通需求发送。忙碌期间防双击。

### 2. 类型化提交、成功与失败

- 继续调用既有消息 API，使用完整 ConversationCommand，不将替换请求退回纯自然语言。快捷条件和自由文本按已有合同合并去重；不让“全程更近”变成仅目标站更近或普通推荐偏好。
- 成功、needs_input、needs_recovery、失败分别消费实际响应；有修改命令不等于修改成功。只根据服务端返回更新 active version、候选与正式选择，不自动选择首候选，不用本地替换数组伪造新 Plan。
- 修改成功后的候选仍遵守 C 的“预览→明确正式选择”。按 API 实际合同处理原选择的失效/保留；不得把旧 selected_plan_id 带到新版本。需要说明实际前后语义并用断言固定。
- 修改失败/仍无解时保留原方案供浏览，接入 E3 的真实恢复流程。网络失败重试沿用逻辑请求 request_id，修正输入或改变条件是新请求。过期成功、失败、流式事件和 finally 不污染当前会话。

### 3. 真实 PlanDiff 展示

- 每条 diff 绑定相同响应内的 new_plan_id，展示到对应候选或可明确识别的结果区；多个候选不能都借用第一条 diff。
- 显示替换前后地点、替换站序/角色、实际锁定站点，以及全程距离、总时长、全员地点费用的增减（费用不含交通）。delta 为 0 正常显示，正负号、单位和比较基准准确；时长减少不要一概写“提前到达”。
- 展示 replacements 实际内容，不能无条件取第一个元素后解引用；缺 diff/缺字段安全降级为“未返回变更明细”等明确状态，不伪造未改站点或 delta。当前合同单站修改不表示渲染器可以假设数组永不为空。
- 使用明确版本/plan 归属；切换新旧预览、历史消息及刷新恢复时，对比内容不能跟随最新 response 串页。
- 清除 `legacy-plan-diff` 中“餐厅保持不变 / 通勤缩短”的硬编码隐藏文案；如测试依赖它，改为断言真实锁定角色、资源和距离，不靠隐藏兼容文字通过。

### 4. 有限清理与最终一致性

- 删除只在旧实现使用且已被替代的订单空态、重复入口/不可达分支、孤立样式或测试兼容文案，仅限有搜索和回归证据的项。订单功能本就未实现，不能借清理新增订单。
- 不为了整理把整个 App 拆成新架构；仅在修改/展示逻辑有明确重复或独立职责时提取。沿用 tokens 与样式所有权，不在文件末尾叠加覆盖规则。
- 核对当前用户可见主流程中是否有原型 Mock 承诺、错误费用/距离口径或未实现功能的可执行按钮；仅修本次迁移造成的问题。既有 POI 数据源局限、地图在线成功路径、Planner 排序缺口继续如实记录，不扩展修复。

## 验证与证据

先补关键定向断言，最后在最终代码状态统一运行一次；已在相同代码状态运行且之后未改相关内容的结果可引用，不重复堆次数。

- 单测：未选/历史/stale/草稿/反问/恢复的门禁；原目标捕获与过期；取消零请求；准确命令及 required 更近条件；防重复；成功的新版本/选择语义；失败保留原方案；多个 diff 与空 replacements；会话切换的过期结果。
- 浏览器桌面与 375px：选择合法方案→目标站替换→结果比较→明确选择新候选→刷新恢复；手机替换草稿可见可操作，键盘/ESC/焦点返回。可在少量长名称、长行程用例中验证横向不溢出和滚动，不做无限视觉微调。
- 至少一条真实 FastAPI/临时 SQLite 的确定性修改场景，验证锁定站点资源和顺序未变、目标确实变化、PlanDiff 与服务端持久化相符、新版本选择/刷新。使用现有离线规则/Provider 环境，不把集成换成 page.route Mock；输入须由实际 fixture 确认可触发成功，不凭文案猜结果。
- 网络请求断言同时覆盖 `/messages` 与 `/messages/stream`。动作完成必须等明确的服务端结果/状态，不能只等 submitted 文案或按钮点击完毕。
- 最终运行：`pnpm --dir frontend test`、`pnpm --dir frontend build`、`pnpm --dir frontend test:e2e --workers=1`、`pnpm --dir frontend test:integration`、`git diff --check`。只读后端合同不重复全后端/Frozen/Live；如必须改后端则先给证据说明范围，再做对应定向验证。
- 本机 localhost 在沙箱内可能报 ERR_NETWORK_ACCESS_DENIED；这是需要允许本地测试服务访问的环境问题。必要时走现有权限机制，仅开放本地服务，不反复把页面未加载的失败当业务验证。授权后仍有应用断言失败须诊断，不归咎网络。
- 截图集中一轮：桌面/手机修改草稿、成功真实 diff、失败/恢复代表状态。重用 E 已通过的条件弹窗和恢复证据，不重复全量截图。

## 交付与停止条件

1. 更新 CODEX_BRIEF 的实现入口、F 能力与剩余边界；新增 `docs/status/s_ui1f_modification_closeout_YYYYMMDD.md`，标明 Current branch snapshot。重大主链变化才同步架构文档，Frozen 报告不改。
2. 对 B–F 的用户链路给出简短完成/未完成清单，保留地图真实在线加载、真实 POI 数据质量等未验证项；不能宣称已生产就绪或已合并。
3. F 可用一个功能提交收口；如清理较多可另作清理提交，均只暂存本片相关文件。不要夹带已有临时目录。
4. 最终返回 commit、关键行为、实际测试/失败、截图与未决项，等待主会话最终验收。不推送、不合并、不自行开始下一项目。

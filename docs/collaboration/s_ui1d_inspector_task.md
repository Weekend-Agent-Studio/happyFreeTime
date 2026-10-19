# S-UI1D：右侧行程、地图与 POI 子页面执行任务书

状态：Target / 待实现。2026-10-19。C 的自测交付已收到，尚未进行主会话验收；C+D 完成后集中验收。

## 一、基线与执行方式

- 仓库：`E:\04_Develop\Projects\PycharmProjects\happyFreeTime`。
- 分支：`codex/s-ui1-product-frontend`；必须包含 B `c3507d9` 和 C `f4e1911`。
- 开始先运行 `git status --short --branch`、`git log -3 --oneline`，读取根 AGENTS 与 CODEX_BRIEF。不得覆盖用户改动。
- 沿用当前分支顺序开发，不重新从 main 创建缺少 B/C 的分支，不 reset。
- 本会话只实现 D，默认不开子 agent、评审代理或文档代理。完成自测与本片提交后停止，不进入 E/F，不推送、不合并。
- 主参考为本地 `happyfreetime-react-prototype-v4/src/App.tsx` 与 `src/styles.css`；Figma 可能过时，不必连接。不重开视觉方向讨论，不通读全仓库或历史材料。
- C 的自报结果：Vitest 52 passed、构建通过、E2E 24 passed / 2 skipped、真实 API 2 passed。仅作为接手基线，不得把未运行的检查记为本片结果。

## 二、目标

将右侧统一为：**复用 C 的紧凑方案头部 + 行程/地图两个主 Tab + 同区域 POI 子页面**。
桌面右侧和手机方案抽屉使用同一内容组件与交互规则。所有数据继续来自当前预览对应的真实响应；详情浏览不提交业务写请求。

## 三、只读定位入口

用 `rg` 定向定位，不要求逐行读完整 App：

- `frontend/src/App.tsx`：InspectorTab、PlanInspectorHeader、InspectorViewPicker、Inspector、TripPanel、RecommendationSummary、TimelineRoute、PoiDisclosure、StopImage、MapPanel、EvidencePanel、DeveloperDetails、MobileSheet。
- 同文件的 `viewPlan`、`openPlanDetails`、`openRouteOnMap`、`showRouteInTimeline`、`startReplacement`，以及 C 的版本/选择可用性判断。
- `frontend/src/AmapPlanMap.tsx`：原始 route leg 索引、geometry 过滤、Polyline/Marker 回调、加载与清理。
- `frontend/src/types.ts`：Plan、Stop、RouteLeg、PoiPresentation、PoiGalleryItem、RecommendationAdvice、PlanWarning、ProviderFact。
- `frontend/src/styles.css`、`shell.css`、`tokens.css`、`frontend/DESIGN.md`：既有样式所有权和 B 的滚动容器。
- `frontend/src/App.test.tsx`、`AmapPlanMap.test.tsx`；`frontend/e2e/planning-flow.spec.ts`、`visual-capture.spec.ts`、`real-api.integration.spec.ts`。

已有事实：C 的头部与选择流程仍在 App 中；当前详情为四视图，POI 使用折叠详情；高德 Marker 点击目前用于路线选择。D 要演进这些入口，不能再建一套平行工作区。

## 四、必须实现

### 1. 头部与视图结构

- 保留 C 的 PlanInspectorHeader，包括预览/正式选择、指标、返回已选方案、选择确认、错误及版本保护。可以提取文件，不复制选择状态或 API 调用。
- 主 Tab 仅保留“行程”“地图”，具备可访问名称、选中状态和键盘操作；若使用 ARIA tablist，补齐对应键盘与 tabpanel 行为，优先复用现有 Base UI 能力。
- 移除“订单”主 Tab 和无交易能力的空页面入口。不要新增模拟执行、预约或支付；本片无需新增“准备执行”按钮。
- 核验与来源不能随 Tab 删除：将现有 EvidencePanel 放入按需展开的次级区域或明确的“核验与来源”入口。继续保留方案级证据筛选、风险去重、安全 Trace 与已有开发诊断开关，不能混成第三个主 Tab。
- 取消行程正文中与 C 头部重复的标题/整组指标。推荐理由、取舍与必要动态风险仍可找到。

### 2. 行程页

- 简洁推荐理由及可展开取舍，来自 RecommendationAdvice / Plan 原有内容，不用 V4 固定文案补空。
- 时间轴依照原始 Stop 顺序，显示到达、离开、停留时间。空、单站、多站和长名称均合理呈现。
- 站点入口在本工作区打开 POI 子页面。站点修改按钮与详情入口是独立操作，不互相冒泡；避免嵌套 button。
- 保留出发段、站间段及实际存在的返程段，使用 RouteLeg 原始数据：交通方式、耗时、距离、起终点和必要来源/降级提示。
- 单站仍可有出发和返程；没有返程数据时不造返程节点。不得把最后一个站间段误认为返程。
- 不在行程页重复完整地图。可保留紧凑交通概览和“在地图查看此段/全程”操作。
- 时间轴路线操作切到地图并聚焦对应原始 leg 索引；地图的路线操作可显式返回对应时间轴位置。

### 3. 地图页与高德复用

- 必须复用并适度扩展 AmapPlanMap，不使用原型示意地图代替，不新建地图 Loader、另一套路由服务或坐标状态。
- 分离“打开 POI”和“聚焦路线”回调：POI Marker 打开对应 Stop 详情；Polyline 点击聚焦原始路段，并保持地图 Tab。需要返回时间轴时给明确操作，不能所有点击都自动切页。
- 出发地/返程标记不是 POI，不得误开第一站或最后一站详情。
- **索引不因 geometry 过滤改变**：中间一段无坐标时，后续线段、标记标签、详情目标与 route leg 原始索引仍准确。不要按过滤后数组下标关联 Stop。
- Stop 本身不包含坐标，Marker 继续由可信 RouteLeg geometry 端点等现有数据派生。缺坐标时不编造点位、直线或路线；提供文字路线和可打开 POI 的站点列表作为降级入口。
- 沿用真实 source / provider_mode / degraded / verified_at 等含义；不把 mock、replay、local_estimate 显示为实时高德事实。
- 没有 JS Key、配置错误、SDK 加载失败、没有可绘制几何等情况，仍可看路线文字和地点详情。SDK 错误必须可见，不能黑屏或一直 loading。
- 保留地图实例清理，不能因切 Tab/详情产生重复实例、残留监听或销毁后回调污染新方案。不要求借本片全面重写地图生命周期或做性能架构升级。

### 4. POI 详情子页面

- 在右侧原区域/手机原方案抽屉内展示，替换内容区，不另开全屏模态框或嵌套手机抽屉。
- 通过当前预览的 Stop 与当前响应中的 PoiPresentation 按 resource_id 关联；禁止使用另一轮最新响应的同名地点信息。
- 显示有数据的项目：名称、可信图片/明确示意图、介绍、地址、类别、标签、到访时段/停留时长、参考费用、营业展示、预约要求、到访提示、风险、数据声明与外部地图链接。
- 推荐原因仅在后端对该站确有相应信息时展示；不能把方案级推荐理由直接宣称为某站专属结论。
- 不展示 demo_rating / demo_review_count 为真实口碑；不把 Demo World 商业字段、示意图、历史照片或 unknown 状态升级为实时事实。
- Stop.price、price_kind、reference_avg_price 和全员 Plan.total_price 的口径分别保留，不混为同一个人均实时价。不为 unknown 费用造出“免费”。
- 缺 PoiPresentation 仍展示 Stop 可提供的基本信息，并对缺失字段省略或标“待确认”。图片失败有占位与来源降级，不静默换成另一地点的照片。
- 外部地图链接使用现有可信名称/地址生成和安全的编码、target/rel；有链接不等于位置已实时核实。
- POI 子页面不需要重复完整方案选择头部；返回后恢复原头部、Tab 和浏览位置。不能让外部链接、图片或返回按钮触发正式选择。

### 5. 返回、滚动与上下文生命周期

- 从行程打开某站 → 返回原行程 Tab、接近原来的精确滚动位置；从地图打开 → 返回原地图 Tab 与原列表/内容滚动位置。保留当前路段聚焦。
- 键盘打开与返回时，焦点回到原触发控件或合理的同站入口；地图 Marker 无 DOM 触发器时，回到可键盘操作的同站列表入口。手机抽屉自身 ESC/关闭仍正常，不因子页面产生多层焦点锁。
- 同一预览上下文内只需保存实际需要的 Tab/返回位置/焦点。可用 ref 与一个小型本地视图状态，不需要全局 store、浏览器路由、localStorage 或后端 checkpoint。
- 上下文至少区分 session、方案版本/响应归属与 plan ID；resource ID 或 stopIndex 单独不能防止串页。缺版本锚点的历史预览不为滚动功能制造领域版本号，安全清理本地状态即可。
- 切换另一个方案、会话或进入新版本时，关闭旧 POI、清除旧目标和旧路段，默认显示行程页；不能显示新标题加旧 POI。重复预览同一上下文不必无故重置位置。
- latest response 成为反问/恢复/失败但当前预览仍是保留的原方案时，不应仅因 latest response 变化而清空正确的详情上下文。沿用 C 区分当前预览响应与最新交互响应的方式。
- 窄屏/桌面切换时，同一上下文不串状态、不留下隐藏但可聚焦的重复详情；桌面与手机使用同一逻辑，不维护两套业务数据。
- 不要求刷新后恢复 POI 子页面和像素位置；刷新仍通过已有 Session API 恢复业务事实。

### 6. 修改入口与 C/HITL 保护

- 保留现有单站修改入口，调用原 startReplacement / ConversationCommand 链路；本片不重写修改实现或 PlanDiff。
- 仅当前活动版本的正式已选方案可提供有效修改；历史只读、预览未选、stale、未提交草稿、忙碌、待处理恢复等状态不能绕过已有安全边界。尽量复用现有判断，并在提交边界保留原校验，不把“是否能选择”和“是否能修改”盲目视为同一状态。
- 打开/关闭 POI、切 Tab、聚焦路线、展开核验都不调用选择、修改、重新规划或条件保存 API。
- C 的切换确认与旧响应保护继续有效，失败/反问不能覆盖正式方案。不删除已有 Clarification/Recovery 卡片或恢复历史。

## 五、推荐文件边界与范围

- 可从 App 提取 `PlanInspector.tsx`，在同模块或少量直接子组件中组织行程、地图、POI、核验。按职责拆分，不机械创建每一层文件或纯转发包装。
- C 的头部可留在原文件或一起提取，选择事实与提交逻辑仍由现有 App 会话层持有。
- 扩展 `AmapPlanMap.tsx` 的 Marker/Polyline 回调和对应测试；API Client、后端 DTO 及核心服务原则上不修改。
- 在既有 styles 的所属规则处修改，使用 Tokens。确认替代后清理本片旧视图/重复 CSS；保留仍被其他功能使用的组件。
- D 是前端业务展示与地图联动切片，不修改 Planner、Router、Verifier、Provider 合同，不修目录/全天排序，不接入新的 POI 服务。
- 双手柄条件编辑、站数/距离 API、宠物偏好、婴儿模型、HITL 产品流重做和最终 PlanDiff 排版属于 E/F，不能顺手实现。

## 六、必要测试

保留 C 的预览/选择/确认/版本保护断言。允许更新 UI 定位，但不能通过删断言弱化覆盖。

| 层次 | 必须覆盖 |
| --- | --- |
| 组件/状态 | 行程与地图均可打开同站；详情缺字段/缺图；关闭返回 Tab；切方案/会话/版本不串 POI；最新反问保留正确旧方案；历史/未选/stale 修改不可用 |
| 路线/地图 | 出发/站间/返程、单站与无返程；中间 geometry 缺失仍保持原始索引；Marker 打开 POI，Polyline 只聚焦路线；起/返标记非 POI；禁用/错误/无几何的文字降级 |
| 浏览器 | 桌面与 375px，真实滚动后打开 POI/返回的 Tab 与 scrollTop 恢复，键盘与焦点返回，Tab 切换、路段联动、长名称/长行程、手机抽屉关闭/ESC；无额外业务写请求 |
| 真实 API | 延续独立 FastAPI/SQLite 集成，不拦截业务 API。至少浏览真实响应的行程/POI、从地图降级列表打开/返回 POI；查看详情前后 selected_plan_id、active_plan_version_id、request revision 不变；C 的选择/刷新/stale 用例继续通过 |

地图成功路径可在单测注入轻量 AMap SDK 替身，验证创建的 Marker/Polyline 回调、原索引和实例清理；这类测试不能写成“真实高德验收通过”。若本机已有可用真实 JS 地图配置，可做一次人工联调，但不输出密钥、不改凭证；无配置时明确成功路径未 Live 验证，不为此另接服务或修改安全配置。

滚动恢复使用真实滚动容器和可滚动内容验证，可容忍少量像素差，不能只断言某个 ref 值或组件是否存在。
已有 B 的滚动容器分别是桌面 detail-panel、手机 sheet-content；实现时确认实际滚动所有者，避免记录了错误层的 scrollTop。

执行必要验证，每片一次完整前端检查即可；失败修复后先定向重跑，不反复做无变化的全套检查：

```powershell
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend test:e2e --workers=1
pnpm --dir frontend test:integration
git diff --check
```

本机服务端口/浏览器权限问题与业务失败分开记录；不把用例 skip、Mock 地图测试或离线 Provider 称为 Live 结果。无需跑全后端 Frozen/Live 评测。
集中截图：桌面行程/POI/地图或降级，手机 POI 与返回状态。先等过渡结束；一轮检查、集中修正，不开额外设计代理或无限微调。

## 七、交付与停止

- 更新 CODEX_BRIEF 的已实现前端入口/边界、frontend/DESIGN，以及 `docs/status/s_ui1d_inspector_YYYYMMDD.md`，明确 Current branch snapshot / 未最终验收；不倒写 C 的原始报告。
- 报告真实变化、关键文件、测试实际结果与跳过原因、代表截图、地图数据限制、未决问题，注明“C+D 等待主会话集中验收”。
- 只暂存本片文件并独立提交，记录 commit；不推送、不合并、不进入 E/F。
- 回传最小材料：分支、C `f4e1911`、D commit、状态记录、测试结果、截图路径及需要判断的问题。没有阻塞也明确说明。

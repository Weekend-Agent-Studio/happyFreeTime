# S-UI1 后续切片与 Luna 执行任务书

状态：协作方式为 Current；C+D 已实现并完成轻量验收；E–F 为 Target / 尚未完成。更新于 2026-10-20。

进度更新：C `f4e1911`、D `b24548d`、定位修复 `718f896` 已完成主会话轻量验收，主会话未重跑测试。下一阶段执行 `docs/collaboration/s_ui1e_conditions_hitl_task.md` 的 E1→E2→E3，分别提交后集中验收。本文 C 原任务书与单独 D 任务书保留作对照，不表示 C/D 尚未实现。

## 协作方式

- 主会话负责明确范围、设计关键业务边界、汇总验收与取舍。默认直接验收，不使用子 agent。
- 用户指定的侧边会话使用 GPT 6 Luna、Max 推理执行任务书。不要为执行任务再开子 agent。
- C 与 D 顺序执行，可在都完成后回主会话集中验收；E 单独验收；F 做最终汇总。每个切片仍须自测并独立提交。
- 出现需要新业务状态、改变领域语义、后端核心重构或不能确定的事实时，记录具体证据与最小问题，不自行扩大范围。
- 交付记录足够短且可核查，不要求长篇逐步叙述。未改动的 Frozen/Live 基线不重复运行。

## 已完成基线与 Git

- S-UI1B 已在 `codex/s-ui1-product-frontend` 提交为 `c3507d9`；基于 `origin/main@9be530e`。开始时仍用 `git log` 核对。
- 入口：`docs/current/CODEX_BRIEF.md`、`frontend/DESIGN.md`、`docs/status/s_ui1b_shell_20261019.md`。
- B 的验证：Vitest 45 passed；规划流浏览器 22 passed / 2 个桌面专用用例跳过；真实 FastAPI/临时 SQLite 浏览器 2 passed；构建通过。外部 Provider 为离线数据，不是 Live 质量结论。
- 实现会话首先运行 `git status --short --branch`、`git log -3 --oneline`。确认位于上述分支并包含 B 后继续，不再次切回 main 建无依赖的新分支。
- 这是用户明确同意的连续切片任务。与 AGENTS 的“新任务从最新 main 建分支”不冲突：该动作已在 B 开始前完成。
- 不 reset、不覆盖用户改动、不提交无关文件。多个实现会话不要同时写同一工作树；本次 C、D 为顺序任务。
- 每片完成后提交本片，记录 commit；未要求推送、创建 PR 或合并。若未来改用独立 worktree，必须从包含依赖切片的明确 ref 创建。

## 后续分工

| 切片 | 执行职责 | 边界与交付 | 验收批次 |
| --- | --- | --- | --- |
| C | 真实聊天/方案比较与正式选择 | 纵向卡片、纯预览、右侧选择、切换确认；保留会话/历史/错误语义 | 可与 D 一次验收 |
| D | 右侧信息工作区 | 在 C 的头部上扩展行程/地图双 Tab、POI 子页面、返回滚动位置、真实 RouteLeg/地图联动 | C+D 集中验收 |
| E1 | 五项条件编辑器 | V4 日期时间/人数/预算/偏好控件；本地 Draft 与后端 Draft/Committed 分开；原有类型化合同与校验 | 与 E2/E3 集中验收 |
| E2 | 有限的已有能力接入 | 站数/距离类型化编辑合同；先核实字段语义和测试，必要时扩展 API DTO，不改 Planner；宠物仅未核实普通偏好，不加婴儿计价模型 | 同上 |
| E3 | HITL 产品交互 | Clarification/Recovery、ID/revision、取消、新需求、旧方案保留、安全失败反馈；不另建恢复机制 | 同上 |
| F | 修改与最终验收 | 合法已选方案单站替换、真实 PlanDiff、完整刷新/窄屏/键盘场景、确认对齐后清理遗留样式组件 | 最终验收 |

E1–E3 是任务清单中的子步骤，可在同一 E 会话分次提交；不需要三个并行会话。C/D 不提前开发 E 的领域功能。

## C 原实现任务（已完成，保留对照）

### 目标

在已完成 B 的布局中，把真实会话与方案列表改为 V4 的纵向比较体验。点击卡片只预览；只有右侧明确确认才改变正式选择。完成后停在 C，不自动进入 D。

主要视觉参考：`E:\04_Develop\Projects\PycharmProjects\happyfreetime-react-prototype-v4\src\App.tsx` 与 `src\styles.css`。Figma 可能过时，不必连接。原型是视觉/交互参考，正式仓库是业务事实；禁止整份覆盖 App 或复制 Mock。

### 开始时定向阅读

先读 AGENTS、CODEX_BRIEF 和本任务书，再用 `rg` 定位：

- `frontend/src/App.tsx`：`responsesFromSession`、`activePlanResponse`、`attachResponsesToMessages`、`submit`、`viewPlan`、`choosePlan`、`openInspectorForResponse`、`ChatBubble`、`RichPlanningReply`、`PlanCard`、`Inspector`。
- `frontend/src/types.ts`：AgentResponse、SessionView、Plan、RecommendationAdvice、ImageRef、PoiPresentation。
- `frontend/src/api.ts`：流式消息、恢复会话、`selectPlan`；不另建 API Client。
- `app/api/application.py` 和 `app/persistence/repositories.py` 的方案选择端点/校验（只读）。
- `frontend/src/App.test.tsx` 中预览、选择、异步过期响应、历史富消息、选择失败、反问时旧方案保留的测试。
- `frontend/e2e/planning-flow.spec.ts`、`real-api.integration.spec.ts` 现有测试与运行配置。

不要通读所有后端、历史文档或技能库。只在具体需求需要时调用相关工具；用户已经确定 V4 方向，不重开视觉方案评审。

### 必须实现的行为

1. **保留真实会话链路**：新建/切换/刷新、完整消息、各轮原始结构化响应、流式进度、失败重试和 request_id 语义继续有效。不要只用最新 response 覆盖全部历史富回复。
2. **纵向比较**：候选按后端顺序排列，数量取真实返回（最多三个，不能硬凑三个）。布局参考 V4，内容至少包含标题、真实图片/占位、总时长、累计 RouteLeg 距离、费用口径、必要推荐标记和风险。
3. **卡片只预览**：点击卡片更新预览及右侧对应响应；不调用选择 API，不改 selected，不创建版本、不触发修改或重规划。卡内链接/展开等独立操作不要冒泡产生意外动作。保留键盘可访问的明确预览控件，避免嵌套 button。
4. **右侧选择**：在右侧建立后续 D 可继续使用的紧凑方案头部，显示当前预览标题、指标、预览/已选状态和“选择此方案”。移除左侧卡片的正式选择按钮，避免双入口。手机通过同一右侧详情抽屉提供选择，并在卡片保留容易发现的详情入口。
5. **预览不覆盖正式方案**：已有选择时预览其他候选，正式选择保持不变；能返回已选方案。当用户要切换正式选择时，使用现有 Base UI Dialog 弹窗确认；取消不发请求。
6. **后端确认后再选中**：请求中显示忙碌并防止重复提交；只使用 API 返回的 selected_plan_id 更新正式选择。失败保留原选择并给出可见错误，不假装成功。
7. **状态与并发**：确认目标必须绑定当时 session、active PlanVersion 和 plan ID。切换会话、生成新版本或目标失效时，不得把旧确认作用到新状态；继续保留现有选择响应的 session/version 防护。生成新版本后按服务端语义清除旧选择，不能自动选择首方案。
8. **历史只读**：历史卡片允许预览，但不把非当前 active version 的方案当成可正式选择/修改。优先使用服务端版本锚点；缺失归属证据时只读降级，不为老开发 DTO 新增迁移层，也不凭 plan_id 猜版本归属。
9. **待验证与待交互**：旧方案仍可看；方案 stale/未提交草稿或存在待处理恢复时，不将其显示成可执行的有效新选择。统一控制右侧操作可用性，后端仍最终裁决。
10. **解释与事实**：保留后端推荐理由、主要取舍和必要风险；可以精简/展开，不得换成原型固定文案。`total_price` 是全员地点费用、不含交通；本片优先保留此口径，不用当前已变更人数反算历史人均。
11. **图片**：优先 Stop.image 或有可信来源的展示图；来源与示意属性保留。缺失/加载失败使用明确占位，不把原型固定地点照片套给另一 POI，不展示 demo 评分为真实口碑。

注意：正式选择只更新当前版本的 selected_plan_id，不会创建 PlanVersion；生成/修改方案的版本语义保持不变。

### 组件与修改范围

- 主要改动可落在 App、现有功能样式和少量新组件中，如 `PlanComparison.tsx` / `PlanCard.tsx` / `PlanInspectorHeader.tsx`。命名可调整，以职责为准。
- 优先提取现有成熟展示与业务函数，不复制第二套会话状态。没有明确收益不要一次提取整个 App 或引入新状态管理库。
- C 建立的右侧头部供 D 直接扩展；此时可以继续复用旧 Inspector 的行程/地图/订单空态/核验内容，不为了 C 临时新建另一套详情面板。
- 共享颜色字号来自 tokens；样式在所属规则处修改，不追加一大段覆盖规则。
- 原 API/领域类型原则上保持；若确有合同缺口，先给出代码证据和最小建议，不擅自改后端核心。

### 明确不做

- 不实现 POI 子页面、返回位置缓存、地图改造或双 Tab 收敛（D）。
- 不做双手柄时间、婴儿模型、宠物硬约束、站数/距离编辑（E）。
- 不扩展真实订单、预订、支付或执行状态。
- 不调整 Planner/Router/Verifier、不修全天排序或目录覆盖问题。
- 不删除完整旧前端，不替换整个 App，不重做 B 的导航、标题和滚动系统。

### 测试与验收证据

保留行为断言，因选择入口移动而改测试定位可以，但不能删除失败、版本或恢复断言来让测试通过。

必须覆盖：

- 三候选/单候选渲染；缺图、长标题、未知价格与正确费用口径。
- 多次预览不调用 select；右侧与卡片来源一致。
- 初次选择成功、请求失败、忙碌时重复点击。
- 已选 A → 预览 B 仍选 A → 取消切换无请求 → 确认切换后才选 B。
- 旧历史候选只读、会话切换/新版本期间的过期确认与过期响应不污染当前选择。
- 刷新恢复正式选择；最新一轮是问题/失败时旧方案仍可查看；每轮富回复保留。
- 手机打开详情后正式选择；确认弹窗焦点、Tab、ESC、关闭后焦点返回。

按顺序执行一次必要验证；失败修复后只重跑受影响部分，再做最后全前端检查：

```powershell
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend test:e2e --workers=1
pnpm --dir frontend test:integration
git diff --check
```

真实 API 浏览器测试不得改成固定响应 Mock；更新其选择入口，继续验证 SQLite 恢复与版本。普通 UI 测试可以使用夹具。
浏览器启动失败/端口占用/网络问题要与业务失败区分，保留准确结果，不伪称通过。无需为本片运行全后端 Frozen/Live 评测。
截图只做一轮桌面和手机的代表状态，发现问题集中修正；避免重复设计评审或无止境截图微调。

### 交付与停止条件

1. 更新 CODEX_BRIEF 的前端能力边界/入口；新增 `docs/status/s_ui1c_comparison_YYYYMMDD.md`，明确 Current branch snapshot，记录实际行为、测试、限制。必要时同步 frontend/DESIGN，但不写入 D/E 未实现能力。
2. 只提交本片文件，提交说明围绕 C 的用户行为；不推送、不合并、不启动下一片。
3. 最终简短返回：commit、关键文件、测试实际结果、截图路径、未决问题、下一片依赖。没有问题也明确写“无阻塞问题”。
4. C 完成后用户可以另发 D 任务；主会话可在 C+D 完成后一次验收。自测通过不等于已经通过主会话最终验收。

## 交回主会话的最小材料

```text
请集中验收 S-UI1C（或 C+D）。
分支：codex/s-ui1-product-frontend
B 基线 commit：...
C commit：...
D commit（如有）：...
状态报告：...
实测结果与未通过项：...
代表截图：...
我遇到的取舍/需要判断的问题：...
默认不使用子 agent；先检查 diff 与合同，再针对风险复验。
```

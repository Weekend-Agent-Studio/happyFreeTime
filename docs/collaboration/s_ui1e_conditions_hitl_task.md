# S-UI1E：条件编辑与 HITL 产品交互任务书

分类：Target。2026-10-20。本文只授权 E1 → E2 → E3 顺序实现，不代表已完成，不进入 F。

## 执行方式与基线

- 继续原 C/D 实现会话即可。必读本任务书、根 AGENTS.md；CODEX_BRIEF 只看当前边界和相关入口。不要求重读完整 handoff、历史报告或整个 App。
- 先运行 `git status --short --branch`、`git log -5 --oneline`。沿用 `codex/s-ui1-product-frontend`，应包含 C `f4e1911`、D `b24548d`、定位修复 `718f896`。不要回 main 重建而遗漏依赖。
- 主会话已完成 C+D 轻量验收：定位缺口修复后可收口；未重跑交付测试。真实高德 JS SDK 成功路径仍未验证，不属于 E 的补做范围。
- 不启动子 agent。E1、E2、E3 可连续执行，分别验证、分别提交；全部完成后一次交回主会话。出现真正改变领域语义的阻塞时停在该子片，给出具体代码证据；不要把未完成部分写成已实现。
- 不推送、不合并、不创建 PR。只暂存本任务文件，不覆盖用户改动。
- 主要视觉参考为相邻目录 `happyfreetime-react-prototype-v4/src/App.tsx`、`src/styles.css` 的条件编辑器和恢复交互。Figma 可能过时，不需连接；原型中的 Mock 状态、人数模型、硬编码恢复动作和距离文案不是业务合同。

## 共同边界

1. 复用既有 API Client、PlanningContextApplication、RequestPatch、ConstraintEngine、Clarification/Recovery 主链；UI 编辑不经过 Router，不把类型化条件改成自然语言消息发送。
2. 三种状态必须区分：弹窗内未提交的本地编辑；后端 `request_view=draft` 的可恢复草稿；后端 committed 条件。只有服务端响应能确认后两者，不能用本地保存成功自行推断。
3. 普通保存条件与显式重新规划是两个动作；已有方案保留并按真实 `plan_stale` 展示。保存可能按既有合同解决待回答问题并恢复规划，必须消费实际响应，不能一概当成 `context_saved`。
4. C/D 的会话、历史响应归属、正式选择唯一入口、版本保护、POI 返回和路段定位继续成立。不能为完成 E 弱化测试或让旧版本方案重新可选/可改。
5. 复用现有 tokens、Base UI Dialog 等依赖；不整份复制原型、不引入新状态库、不重写完整 App。可按明确职责提取编辑器/交互组件，避免只转发 props 的包装层。
6. 历史开发 DTO/checkpoint 无迁移承诺；不新增兼容解析层。用户可见文案表达业务含义，不暴露内部字段名、revision、checkpoint 等实现细节；必要诊断信息保留在次级说明。

## E1：五项条件编辑器

### 定向入口

- `frontend/src/PlanningContextBar.tsx`：SECTIONS、whenSummary、open/save、clarificationSection、PlanningContextNotice。
- `frontend/src/App.tsx`：updateContext、replanContext、openContextForClarification、PlanningContextBar 调用与会话切换。
- `frontend/src/types.ts`、`api.ts` 的 PlanningContextSummary/Patch 与 update/replan API。
- `frontend/src/PlanningContextBar.test.tsx`、App.test.tsx 和现有浏览器条件编辑断言。
- 必要时只读 `app/api/schemas.py`、`planning_context.py` 的五项合同；E1 原则上不改后端。

### 必须实现

1. **统一弹窗**：五个 Chip 保留 B 的布局，打开 V4 风格编辑器。桌面、375px 可操作；标题/内容/底部按钮布局稳定，长内容在弹窗内滚动；取消、ESC、遮罩关闭不提交，焦点约束和关闭后焦点返回有效。保存中防重复并保持错误可见。
2. **日期时间**：日历选日、时间预设、可输入的起止时间、双手柄时段与快速时长共用同一份本地值。使用现有 Base UI Slider 能力或可访问原生控件，不添加拖动库；两个手柄可分别键盘操作并有名称/当前值。日历可用键盘选择日期，月份切换不意外改日期。
3. 保留 `start_kind`/`end_kind` 与 pending_field 的实际含义：行程开始/结束、出发、最晚到家不能互相偷换。预设只改时钟值；不清除已有边界语义。时间不完整可按现有 clear 合同提交；两个时间均有效时阻止明显反序/相等，不静默跨日或截断。滑块展示范围不能夹掉服务端已有的合法时间；超出便捷范围仍可精确输入保存。日期按本地日期字符串处理，避免 UTC 转换偏一天。
4. **出发区域**：明确是出发点/检索依据，保留文本输入、现有值和明确清除。地理解析由现有后端负责；不制作伪定位或假建议列表，不增加目的地/城市切换合同。
5. **同行人数**：成人/儿童使用加减控件，遵守现有 WhoEdit/PartyProfile 校验，不照搬原型“成人至少 1”的假设；合法范围以代码为准。保留儿童年龄、同行关系的既有能力，不因视觉简化丢值。不新增独立婴儿人数或计价模型，也不对原型年龄分组作产品承诺。宠物由 E2 接入。
6. **预算**：人均预算/不限、精确输入、便捷滑块与预设、严格上限开关。滑块范围只表示便捷范围，已有或手输金额超出该范围不被夹回；空值、非有限数、非法金额不发送。不限时提交合同要求的无 amount/strict 形式。非严格预算是偏好，严格预算是后端硬约束；不承诺实时价格、交通费用或一定可行。
7. **偏好**：已选可移除、推荐标签可切换、自定义可添加；继续保留 preferences/diet_tags/scene_tags/avoid 分类，不能保存一个分类时丢掉其他分类。按 category+value 去重，Enter 添加不触发表单保存。标签不暗示已验证的 POI 事实；避免一条“全部软偏好”的文案错误概括饮食/避免等已有约束。
8. **草稿与并发**：打开时捕获原始值与 base_revision；仅提交真正变化的字段。无改动保存不请求、不涨 revision、不置 stale。取消后重开从当前后端值初始化。会话切换、条件 revision/待交互上下文失效时，关闭或明确使旧编辑失效；不能把旧本地值贴到新 revision 上继续保存。异步返回不得写入另一会话或覆盖较新状态。保存失败保留本地输入并允许修正；不可把失败当作成功关闭。

### E1 验证与提交

- 定向单测：无改动/取消零请求，准确差异 patch，分类保留，预算范围外输入，日期/时间输入与滑块一致，原有时间边界语义，非法值，revision 变化与会话切换、过期响应。
- 浏览器抽查桌面/手机弹窗、键盘/ESC/焦点、滚动、保存后 stale 与原方案保留。优先扩展现有用例，不复制整个场景。
- 前端构建；仅跑受影响的单测和浏览器用例，不在每个子片重复全套。
- 提交建议：`feat(ui): productize planning condition editors`。记录 E1 已完成范围和实际结果，再进入 E2。

## E2：接入已有站数、距离与宠物偏好能力

### 已核实事实与实现范围

- `PlanRequest` 已有 `exact_stop_count`、`max_distance_km`、`total_distance_km`；现有公开 PlanningContextPatch/Summary 尚未投影这些字段，需要最小类型化接入。
- `max_distance_km` 当前兼用于候选检索范围与单段路线距离上限；`total_distance_km` 是独立的全程累计上限。不得把前者标成“纯搜索半径”。方案卡的累计距离保持 C 的口径。
- 站数现有语义为精确站数，提案合同 1–4；不是原型的 1–5，也不是最大站数。`required_stop_roles` 数量超出站数时，ConstraintEngine 已有 `STOP_COUNT_BELOW_REQUIRED_ROLES` 冲突。

### 定向入口

- `app/api/schemas.py`：FieldEdit、PlanningContextPatch/Summary。
- `app/api/planning_context.py`：project、compile_edit；`app/api/application.py` 现有 planning-context 更新/重新规划端点。
- `app/domain/constraints.py`：PlanRequest、RequestPatch、字段范围与来源；`app/services/constraint_engine.py` 的字段应用和结构冲突。
- 按需定位 `candidate_retriever.py`、路由/Verifier 中两个距离字段的消费点，确认语义，不改这些算法。
- `tests/test_api.py` 的 planning_context 用例、`tests/test_constraint_engine.py`；前端沿用 E1 组件/类型/测试。

### 必须实现

1. 在顶部提供“更多条件”入口，复用同一弹窗体系。高级条件包含：精确站数、检索及单段距离上限、全程累计距离上限；每项显示当前值并允许明确取消限制。未设置不自动填成原型默认值。
2. 通过现有 Summary 投影 canonical 值及来源，通过现有 PlanningContextPatch 增加封闭、类型化的高级编辑段。字段名可随现有风格确定，但必须是一条请求、一个 base_revision、原子应用；不开放任意内部 RequestPatch 字段给浏览器。
3. 复用 FieldEdit set/clear；站数严格整数 1–4，距离有限且大于零，UI/DTO/领域校验一致，拒绝非法数字、越界和错误类型。领域字段本身若缺少必要范围校验，可补对应的最小校验并定向测试，不重构 Planner。
4. 编译为带用户来源的既有 RequestPatch 后进入同一 ConstraintEngine。普通保存只更新条件并标记旧方案；显式 replan 后才形成新版本。刷新后从服务端恢复；no-op 不发送，clear 只清对应字段。
5. 文案建议“检索及单段距离上限（km）”，补一句“同时影响候选检索范围和每段路线；全程总路程另设上限”。不把检索说成未经证实的精确几何圆；不强行推导两种距离上限的大小关系。
6. 降低站数与已有必需角色冲突时，展示现有冲突/恢复机制；不能偷偷清除角色来凑可行，也不新增第二套站点分配逻辑。清除精确站数不顺手清必需角色。
7. **宠物偏好**：可放在同行编辑器中，唯一持久化表示为普通 preferences 标签“携带宠物”，不新增人数或独立事实字段。在偏好编辑器移除该标签后开关同步关闭，反向亦然；不复制两份状态。开关旁明确“作为出行偏好，地点是否允许携宠尚未核实”。不作为 Verifier 硬约束、不保证候选均允许宠物、不自动开启儿童或饮食标签。
8. 同步 clarificationSection/“编辑条件”路由，使新字段需要输入时能进入高级编辑器（按现有后端确实支持的 pending 字段处理）；未知字段继续使用已有安全输入/反馈，不吞掉问题。

### E2 验证与提交

- DTO/应用 seam/API：三字段 set/clear、非法数值与站数、字段来源、旧 revision 拒绝且无写入、普通保存无新 PlanVersion、显式 replan/刷新恢复；验证与必需角色冲突不丢角色。
- UI：三个字段不串值，准确单位/语义，未设置与 clear，宠物跨两个入口同步且不计入人数/费用、保存失败不假成功。
- 至少一条真实 FastAPI/临时 SQLite 的浏览器场景覆盖高级条件保存→stale→刷新恢复→显式重新规划。复用离线规则环境，不改成 page.route Mock，不宣称 Live Provider 验证。
- 运行受影响的 API/领域测试、前端定向测试和构建。未改搜索/路由/Verifier 算法不跑 Frozen/Live；若必须改变核心语义则先报告证据，不擅自升级范围。
- 提交建议：`feat: expose typed advanced planning conditions`。

## E3：Clarification / Recovery 产品交互

### 定向入口

- `frontend/src/App.tsx`：submit、onClarificationOption、onRecoveryAction、recoveryLifecycleFromResponse、RecoveryCard、ChatBubble、newRequestMode、failedRequest。
- `frontend/src/types.ts`、`api.ts`：ClarificationReply、RecoveryActionRequest/Choice/Outcome、ConversationCommand、request_id。
- 按需只读 `app/domain/recovery.py`、`app/domain/constraints.py` 的交互合同，及 API 的处理端点；不重写 RecoveryPolicy/Graph。

### 必须实现

1. 反问卡、恢复卡和条件提示统一 V4 视觉，但所有问题、可选动作、字段变化、结果来自实际响应。不要硬编码“4 站改 3 站”或前端推导新恢复动作。
2. 反问只处理当前有效 clarification_id/request_revision；问题字段映射到对应 E 编辑器，已有选项和自由输入继续可用。历史问题保留可读但不可重复操作；不能因新一轮只返回问题而丢掉旧方案。
3. 恢复入口明确区分建议、待确认、执行中、成功、仍无解/新问题、取消/保留现有方案。`apply_request_patch` 涉及条件变化时，用现有 Dialog 展示服务端提案的字段和值供确认；只有确认才发送动作，取消弹窗零请求。非修改类动作不一律增加多余确认；描述足够明确即可。
4. 前端可以用已返回的当前条件与 action.patch 生成展示差异，但这仅是提案，不写入正式条件；成功后的 before/after 与 outcome 以服务端为准。未知值安全显示，不把原始对象变成 `[object Object]`，不擅自推导可行结论。
5. 提交绑定当时 session、interaction_id/action_id、request_revision、plan_version_id。确认时再核对仍是同一有效交互；会话切换、新问题/版本或交互结束时使旧确认失效。防双击；异步过期结果不污染当前会话。后端仍最终裁决 stale/重复动作。
6. `request_field` 继续按 input_type/choices 收集输入，复用已有验证/类型化提交。编辑条件、取消本轮、保留旧方案、开始新需求分别遵守既有 action/command；取消本轮不能清空整段会话，“新需求”不能被误当旧问题答案。不要用按钮文案猜动作种类。
7. 历史恢复卡显示真实生命周期及已确认的字段变化，刷新按持久化响应恢复；失败/取消不写成成功。未知动作安全降级并给出可见反馈，不构造新 API 动作。
8. 普通条件保存后的 stale、未提交草稿、正在恢复和可重新规划状态有清晰提示；交互阻塞期选中/替换门禁保留。旧方案可浏览但不冒充已按新条件验证；不从前端清除后端 selected 来模拟恢复。
9. 网络/应用失败可修正或重试；保留原方案、原正式选择与已确认条件。重试沿用同一次逻辑请求的 request_id，修正输入后的新动作使用新 request_id，遵循现有 API 幂等合同。未知错误给用户可理解反馈，诊断代码可留在次级信息。

### E3 验证与提交

- 定向单测/浏览器：问题→编辑字段/选项继续；旧问题只读；恢复提案取消零请求、确认精确 payload、忙碌防重；取消本轮/新需求/保留旧方案各自语义；会话切换与过期确认/响应；失败/重试；恢复后仍无解和新问题；刷新恢复状态。
- 至少一条真实 API/SQLite 场景覆盖实际反问或恢复动作及前后状态，不能全部以静态 Mock 代替。若既有集成已覆盖完全相同行为，扩展断言即可。
- 提交建议：`feat(ui): productize clarification and recovery flows`。

## 最终验证、文档与停止条件

- 每片只跑影响范围；E3 收尾统一执行一轮 `pnpm --dir frontend test`、`pnpm --dir frontend build`、`pnpm --dir frontend test:e2e --workers=1`、`pnpm --dir frontend test:integration`。如果相同 HEAD 已执行过该项且之后无相关改动，引用准确结果即可，不重复跑。
- E2 运行并记录准确的后端定向命令/用例。E3 若只是 UI 不重复全后端；若改了应用合同则补相应定向回归。失败修复后重跑受影响项；每次提交前 `git diff --check`。
- 截图集中在 E 完成后做一轮：桌面/手机条件编辑代表状态、高级条件及宠物提示、真实反问/恢复提案和处理结果。无需每次提交全量补图。
- 每片更新 CODEX_BRIEF 的实际能力/入口；可共用一份 `docs/status/s_ui1e_conditions_hitl_YYYYMMDD.md`，分 E1/E2/E3 记录 commit、验证与限制。标为 Current branch snapshot，不改 Frozen 报告，不将 Target 任务书写成完成证明。
- E1–E3 全部完成后停下，未进入 F。最终交回：三个 commit、关键文件、真实运行结果、截图路径、未决问题，以及是否改变后端 seam/领域合同。没有阻塞则明确说明。
- 本任务不包含单站替换/PlanDiff 产品重构（F）、预订支付、真实价格或宠物许可数据源、婴儿计价、Planner 排序/目录覆盖修复、地图成功路径专项。

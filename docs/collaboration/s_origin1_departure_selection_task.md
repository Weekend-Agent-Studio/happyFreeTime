# S-ORIGIN1：出发地明确化与地点选择

分类：Target。2026-10-22。用户已确认只做“出发地”，游玩区域/“想去哪附近玩”暂缓。本文件是可直接交给原侧边实现会话的完整任务书，不要求重读旧 handoff。

## 一、范围与执行方式

- 目标：用户能理解当前路线从哪里开始，并通过真实可确认的地点选择修改起点。不是只改标题，也不是增加自由文本后假装定位成功。
- 默认不启动子 agent。O1 → O2 顺序实现、分别提交，全部完成后集中验收；不推送、不合并、不自行进入游玩区域任务。
- 先读根 AGENTS.md、本任务书和 CODEX_BRIEF 的当前边界；运行 `git status --short --branch`、`git log -5 --oneline`。
- 布置任务时分支为 `codex/s-ui2-planning-polish@02893b5`，存在用户侧边会话的 tracked UI/数据调整及未跟踪补丁、zip、CSS、临时目录。这些属于既有工作，禁止覆盖或顺手纳入本任务提交。先由原实现会话核对并提交其已完成调整，再开始本片；不能对同一工作树并发写入。
- 本片接在现有前端调整之后，默认作为连续任务沿用包含前序成果的专用分支；若执行时已合并 main，则拉取核对最新 main 后创建 `codex/s-origin1-departure-selection`。不得从旧 main 起步遗漏前序改动，也不得在 main 直接编辑业务代码。分支选择与基线写入交付记录。

## 二、已核实代码事实

- 顶部 Where 编辑目前仅写 `PlanRequest.location`，不写 `planning_area`。`location` 是路线起点；没有 planning_area 时还承担候选检索中心。保留此既有行为，不修改检索语义。
- 默认 EnvironmentContext 在 `app/orchestration/entry_graph.py` 中使用 `39.9219, 116.4436`，地址显示“北京市朝阳区”。这是演示固定点，不是浏览器定位，也不代表整个朝阳区。不能擅自给它命名为某个地铁站/商场入口。
- `PlanningContextPatch.where` 目前是 `FieldEdit[str]`；`PlanningContextApplication._compile_where` 将文本交给 GeocodingProvider，唯一解析成功后编译为 ConstraintValue/RequestPatch。它没有给前端提供搜索建议列表。
- `app/api/server.py` 通过 `HFT_GEOCODING_PROVIDER_MODE`、`AMAP_WEB_SERVICE_KEY` 构造地理服务；离线 Mock 只明确配置国贸、海淀黄庄。Web 服务 Key 与地图 JS Key 不同，不可拿后者代替服务端地理服务。
- 基础 POI 目录是有限的本地快照，不是高德全量 POI 搜索。选择一个出发位置不需要也不允许把该搜索结果加入规划目录。

## 三、定向阅读入口

1. 前端 `PlanningContextBar.tsx`：SECTIONS、getValues、where 的 open/save、baseRef、弹窗与 pending field；`App.tsx` 的 updateContext、session epoch 防护；`types.ts`、`api.ts` 的 Summary/Patch/请求处理。
2. 后端 `app/api/schemas.py`、`planning_context.py`、`application.py` 的已有 seam；`app/api/server.py` 的 Provider 组装。
3. `app/providers/geocoding.py`、`app/domain/providers.py`、`app/domain/constraints.py` 的位置、解析结果与来源合同；`entry_graph.py` 的默认 EnvironmentContext。
4. 现有 PlanningContextBar/App 单测、`tests/test_api.py` 的 where/地理解析/旧 revision 用例，以及 `real-api.integration.spec.ts`。
5. 用户已认可的当前视觉是基准；本次不重新设计整个顶栏或接入 Figma。

## 四、O1：命名、当前地点与默认值

1. 顶部项目统一叫“出发地”，编辑弹窗标题“从哪出发”。标签可用“搜索出发地点”，帮助文字使用“用于计算前往首站的交通”；不再混用“地区”“出发区域”和“游玩区域”。必要时补一句“未指定其他活动范围时，也会影响附近候选”，避免谎称它完全不影响召回。
2. 弹窗首先显示“当前出发地”：地点名称、地址和来源状态。系统默认、用户已选、待确认/解析失败有文字区分，不能只靠颜色；Chip 长名截断，弹窗中能看完整地址。
3. 默认点名称使用“演示默认出发点”，注明“固定演示位置，并非你的实时位置”；可在次级信息展示坐标。不要仅展示“朝阳区”却让用户误以为从其所在位置起算。
4. 本片保留默认坐标，避免无理由改变全套演示路线或评测 fixture。默认定义保持单一事实来源；不要在前端再复制一份坐标。若需要 UI 名称，通过现有位置摘要或最小展示字段投影，不覆盖用户真实地点名称。
5. 提供“使用演示默认出发点”或“恢复默认出发地”操作。它应恢复明确的服务端默认点；不能把 clear 操作冒充 reset。利用 O2 的服务端默认地点选择合同实现，取消和 no-op 不写入。
6. 不新增浏览器实时定位授权、常用地址、家庭住址保存、长期记忆或地图拖拽选点。

## 五、O2：真实搜索与确认选择

### 用户交互

1. 使用一个搜索输入与候选列表。用户输入地铁站、商场或地标，结果展示名称、区/地址，明确区分同名地点；点击结果产生弹窗内的“待保存出发地”，只有保存才更新 Session。
2. 起步限制北京，界面明确当前城市。出发地可以不在本地规划 POI 目录中；搜索位置可用与附近有规划候选是不同概念。不能假装支持任意城市。
3. 输入文字后未选择结果时，不把原地点当作新选择保存；不得仅按字符串相同就视为同一地点。编辑搜索词使已有候选选择失效时，明确要求重新选择；关闭/取消恢复原值。
4. 建议约 300ms 防抖、合理最小字符数、有限候选数量。处理正在搜索、无结果、网络失败、未配置和地点信息不足；旧查询响应不能覆盖新查询，关闭或切会话后不回写旧候选。
5. 列表支持键盘上/下、Enter 选择、ESC 关闭/返回和清晰焦点；正确关联 combobox/listbox/option，触屏目标可操作。优先使用已有 Base UI 能力，不引入新 UI 库。
6. 结果须有可用地点身份和坐标。仅行政区名称、无坐标的输入提示或无法唯一确认的结果不能当作精确出发点保存；引导选择更具体的地标。不自行给行政区中心编造“精准位置”。

### API 与可信边界

1. 搜索走后端封闭接口，服务端调用高德地点搜索（可结合输入提示）；Web 服务 Key 不发送到前端，不写入响应、日志、截图或测试夹具。复用现有 HTTP/Provider 组装方式，避免另起通用地图平台或代理任意 URL。
2. 复用当前 GeocodingProvider 所属位置能力，按清晰职责加深搜索/地点身份解析接口；不要把搜索和保存各写一套互相矛盾的地址归一化逻辑。服务端响应只返回 UI 必需的 ID、名称、地址、城市/区、可选状态及来源。
3. 采用类型化地点选择合同：明确区分服务端默认点和 Provider 搜索结果 ID。保存时服务端按地点 ID 复核唯一位置，再编译到现有 `RequestPatch.location`；不得直接相信客户端任意传入的坐标/城市/来源，也不得把所选名称重新当自由文本地理编码，导致选 A 保存成同名 B。
4. UI 编辑 Where 的公开 DTO 可调整为封闭的选择结构；同步 TS/Pydantic/API 用例。自然语言 origin_text 仍沿用现有 GeocodingProvider 解析链，不为旧开发 DTO 新增兼容/迁移包装。保留必要的 clear 合同语义，但前端重置默认使用明确的默认选择操作。
5. 搜索与候选选择是只读：不更新 revision，不触发 Router/规划，不创建 PlanVersion，不清除正式选择。保存通过唯一已有条件更新 seam，携带 base_revision 和 request_id；普通保存后旧方案 stale，显式 replan 才建立新版本。若正在补 location 反问，继续按现有响应合同恢复，不强行当成普通 context_saved。
6. 不改 `planning_area`、max_distance_km 或 total_distance_km 的含义；不新增游玩区域编辑和商圈圆心筛选。
7. 坐标系必须核实：高德返回坐标与现有 GeoLocation/路线 Provider 的约定一致才可保存；不能把 GCJ-02 与基础 OSM 的 WGS84 无标识混用。沿用仓库已存在的转换/约定；若发现跨 Provider 的既有缺口需要改领域合同，先报告确切证据和最小方案，不在本片暗中改全链坐标。

### 无配置与离线模式

- 无在线服务时，提供明确标识的本地演示地点选项和默认点；只列出服务端确实配置、名称与坐标能对应的地点，不伪造“搜索成功”。可按关键词过滤这份有限列表，并提示“演示地点，仅支持列表内选择”。
- 在线服务失败不静默把其他地点坐标顶上；当前已保存出发地继续可见，允许重试或明确选择演示默认点。
- 离线 fixture 使用可确认的固定地点身份/解析数据，不依赖真实 Key。不能把 Mock 验证写成高德线上验证。

## 六、必要验证

- 前端定向测试：命名和默认状态；同名结果区分；只读搜索零写入；候选选择与取消；无选择禁止误存；旧查询响应/会话切换；键盘选择；准确的类型化 selection patch；失败保留旧值；no-op；默认恢复；revision 失效。
- Provider/API 测试：搜索归一化、空结果/无坐标/歧义、未配置/网络失败；伪造或不在服务范围的地点身份拒绝且无写入；服务端地点 ID 解析与最终保存坐标一致；默认点来源和坐标；旧 revision 拒绝；保存不建版本；自然语言出发地原链路不退化。
- 真实 FastAPI/临时 SQLite 浏览器场景（Provider 可使用离线 fixture）：搜索→选择→取消零写入；保存→stale→刷新恢复名称/位置→显式 replan。核对首段 RouteLeg 使用保存后的出发坐标，同时检索仍遵守既有 location fallback，不仅断言 Chip 文案变化。
- 桌面/手机代表截图各一轮：默认出发点、候选列表、已选待保存状态、保存后。检查长名、矮屏、焦点和按钮可见性。
- 按影响运行定向 Python 测试、前端相关单测、构建和上述浏览器；最终 `git diff --check`。只改位置 seam 不跑整套 Frozen/Live；若核心语义必须变化先报告，不自行升级范围。localhost 沙箱阻断与应用失败分开记录，可按现有权限机制运行本地测试服务。

## 七、交付与停止条件

- O1/O2 分别提交，只暂存本片文件。更新 CODEX_BRIEF 和一份 `docs/status/s_origin1_departure_selection_YYYYMMDD.md`，注明 Current branch snapshot、合同变化、实际测试及在线/离线边界。
- 最终交付：基线/分支、两个 commit、关键文件、测试结果、截图、是否实测高德线上服务、未决问题。没有线上 Key 时交付明确可用的离线模式，不宣称在线成功。
- 本片结束后停止：不做游玩区域、商圈偏好、POI 扩库、实时定位、地图选点、排序或其他 UI 精修。

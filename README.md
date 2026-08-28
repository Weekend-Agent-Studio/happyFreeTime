# HappyFreeTime

HappyFreeTime 是一个面向北京周末活动的本地生活规划 Agent。V2 将自然语言入口、确定性约束补全、必要反问、候选方案生成和会话持久化组织在一条可检查的 LangGraph 工作流中，并提供 React 规划工作台。

## 项目状态与文档入口

本 README 维护当前代码已经实现的能力、运行方法、验收场景和测试命令。它回答的是“项目现在能做什么”，不是个人已经掌握到什么程度。

| 入口 | 用途 |
| --- | --- |
| [`docs/README.md`](docs/README.md) | 文档总索引、权威层级和新会话阅读顺序 |
| [`docs/canonical/`](docs/canonical/) | 已确认的 V2 架构基线和开发路线图 |
| [`docs/learning/progress.md`](docs/learning/progress.md) | 学习进度；与代码完成度分开维护 |
| [`docs/learning/milestones/m1_entry_loop.md`](docs/learning/milestones/m1_entry_loop.md) | M1 项目链路、关键设计、测试证据与面试表达 |
| [`docs/learning/milestones/m2_trustworthy_planning.md`](docs/learning/milestones/m2_trustworthy_planning.md) | M2 天气、路线、Catalog C1-C3 与原生多站规划的领域契约、证据和限制 |
| [`docs/status/m2_trustworthy_planning_plan_2026-08-20.md`](docs/status/m2_trustworthy_planning_plan_2026-08-20.md) | M2 当前切片、已确认的多站骨架/搜索设计与下一实施顺序 |
| [`docs/product/product_idea_inbox.md`](docs/product/product_idea_inbox.md) | 尚未批准实现的临时想法 |
| [`docs/collaboration/session_bootstrap.md`](docs/collaboration/session_bootstrap.md) | 新开 Codex 会话时的协作启动说明 |

> 学习进度有独立价值：代码里程碑完成只说明功能存在，学习里程碑完成还要求能够解释设计、验证行为、回答追问并完成小实验。

## 前端现在可以做什么

| 操作                      | 当前状态           | 说明                                                     |
| ------------------------- | ------------------ | -------------------------------------------------------- |
| 输入自然语言规划目标      | 已完成             | 可以自由输入，也可以点击三条预置建议                     |
| 自动补全非关键条件        | 已完成             | 日期、时间、预算、距离、同行人等默认值会显示为“本次假设” |
| 缺少关键条件时反问        | 已完成             | 前端继续输入答案，Graph 从 SQLite checkpoint 恢复        |
| 生成并比较候选方案        | M2 多站切片        | 短时请求保留双站；覆盖午晚餐的长时间窗可生成显式 3/4 站方案，最多显示 3 个 |
| 选择候选方案              | 已完成             | 点击方案卡后，右侧详情随选择更新                         |
| 查看行程时间线            | 已完成             | 显示开始/结束时间、价格、站点和站间耗时                  |
| 查看地图页签              | M2 真实地图首版    | 配置高德 JS API 后绘制复核后的路线几何和 N 站 marker；未配置或加载失败时保留路线文字摘要 |
| 查看约束冲突              | 已完成             | 无可行方案时显示原因与可放宽方向，不伪造推荐结果         |
| 新建规划                  | 已完成             | 清空当前前端状态，下一次发送时创建新会话                 |
| 移动端使用                | 已完成             | 375 px 起可用，方案卡可横向滑动，详情下沉展示            |
| 最近会话列表              | 已完成             | 左栏最多展示 5 条非空会话，可点击恢复完整消息与最近规划   |
| 刷新与导航恢复            | 已完成             | URL 保存 `session`；刷新、浏览器前进/后退均恢复对应会话   |
| 修改假设值                | 未完成             | 当前只能查看假设，还没有点击编辑控件                     |
| 天气事实与雨天可行性      | M2 切片 A 已完成   | 支持 mock/replay、live/record Adapter、缓存降级与来源展示；已用本机 Key 验证真实高德响应 |
| 路线复核与完整多站时间线  | M2 切片 B/D 已完成部分 | finalist 才逐段复核路线并重建时间线；支持缓存、replay、本地估算降级；已完成真实路线与浏览器地图联调 |
| Catalog 来源与单资源剪枝  | M2 C1-C3 已完成   | 默认离线读取 200 条可重建 OSM 北京 POI；来源、许可、未知事实和核验状态可见，单资源硬约束在组合前剪枝 |
| 原生规划与可解释评分      | S0 已完成          | OSM ID 直接组合；时长、偏好、饮食、场景、避开项和预算参与规则评分，路线复核后刷新分项证据 |
| 整单可行性复核与有限回填  | M2 D0 已完成       | 按实际到达/停留验证基础营业时段；支持同日多营业区间；外部复核按最多 24 个 Route Leg 计费并在骨架间公平取样 |
| 通用多站骨架与搜索        | D1-D3 已完成首版，D4 部分完成 | 外部接口不变；显式骨架覆盖 `ACTIVITY→MEAL`、`LUNCH→ACTIVITY→DINNER`、`ACTIVITY→BREAK→DINNER`、`ACTIVITY→LUNCH→ACTIVITY→DINNER`，多站角色池最多 8 个候选后完整枚举 |
| 动态 POI 与打车执行       | 未完成             | 当前 POI 是版本化静态快照且未现场核验；动态库存、可靠价格、实时 POI 检索和真实叫车仍未实现 |
| 订单/预订执行             | 占位               | “订单”页签是 M4 产品闭环的入口，目前不能下单             |

## 推荐的前端验收场景

1. **可行方案：** 点击“周六和朋友聚一下，人均150”，应看到候选卡、假设、行程时间线和地图估算。
2. **默认值：** 输入“安排一个轻松的约会，想吃甜品”，观察系统补充的日期、时间、预算和距离假设。
3. **反问恢复：** 输入“今天下午出去玩，别超预算”，收到预算反问后输入“人均300”。两条消息属于同一会话，Graph 会继续执行。
4. **约束冲突：** 上述反问后输入“人均200”，可能返回严格预算下无可行行程方案，并展示放宽建议。
5. **闲聊旁路：** 在离线模式输入“你好”，应直接回复，不进入规划节点。
6. **方案切换：** 生成多个候选后点击第二张方案卡，检查右侧行程和地图信息随之更新。
7. **新建会话：** 点击左侧“新建规划”，当前界面清空；数据库中的旧会话不会被删除。
8. **历史切换：** 连续完成两次规划，左侧应显示两条最近会话；点击任一项会恢复该会话的完整消息和最近方案。
9. **刷新恢复：** 生成方案后刷新页面，URL 中的 `session` 不变，消息、约束和候选方案应重新出现。

静态 Catalog 中的营业时间、距离和价格核验状态会影响结果，因此某些“今天”或严格预算请求返回不可行冲突是预期业务结果，不代表接口失败。

### 会话、请求与方案标识

- `session_id` 标识一段连续规划对话，也是 LangGraph 的恢复边界；它不是数据库联合主键的一部分，而是不可猜测的独立标识，所有读写仍必须同时校验 `user_id` 归属。
- 前端为一次逻辑提交生成 `request_id`，网络重试复用它；后端以 `(user_id, session_id, request_id)` 唯一确定一个 `Planning Run`，并原子保存用户消息、响应快照和候选方案，避免重复提交再次写消息或重新规划。
- `plan_id` 标识一次 Planning Run 中产生的候选实例；`composition_fingerprint` 只描述“骨架 + 有序资源”的稳定组合，用于去重，同一组合可以合法出现在不同会话中。

## 是否需要 API Key

不配置也能完整运行。

- `HFT_DEMO_MODE=1`：强制离线 Demo Router，不需要 API Key。Graph、Enrichment、Gate、Planning、FastAPI 和 SQLite 都是真实运行的，只有语义抽取由确定性规则代替 LLM。
- `HFT_DEMO_MODE=0`：强制真实 Router，必须配置 `LLM_API`，否则启动时会给出明确错误。
- 不配置 `HFT_DEMO_MODE`：存在 `LLM_API` 时使用真实 Router；不存在时自动使用离线 Demo Router。

真实模式配置：

```dotenv
HFT_DEMO_MODE=0
MODEL_NAME=deepseek-chat
LLM_API=your-api-key
BASE_URL=https://api.deepseek.com
```

`LLM_API` 只用于 RouterExtractor 的结构化语义抽取。活动与餐厅默认来自版本化的 `data/catalog/pois.json`，不因配置 LLM Key 自动更新；路线仅在显式启用 route `live/record` 且提供后端高德 Key 时请求高德。

天气、路线和浏览器地图分别使用高德控制台中的两类 Key。编辑项目根目录 `.env`；不要把真实值写进 `.env.example` 或提交到 Git：

```dotenv
# mock（默认）/ replay 可完全离线运行
HFT_PROVIDER_MODE=mock
HFT_MOCK_WEATHER=中雨
HFT_ROUTE_PROVIDER_MODE=mock

# replay 从固定规范化事实读取
# HFT_PROVIDER_MODE=replay
# HFT_WEATHER_REPLAY_PATH=data/replays/weather.json
# HFT_ROUTE_PROVIDER_MODE=replay
# HFT_ROUTE_REPLAY_PATH=data/replays/routes.json

# 后端天气与路线共用“Web 服务”Key；live / record 才会联网
HFT_PROVIDER_MODE=live
HFT_ROUTE_PROVIDER_MODE=live
AMAP_WEB_SERVICE_KEY=填写高德_Web服务_Key

# 前端地图使用“Web端（JS API）”Key 及其配套安全密钥
AMAP_JS_API_KEY=填写高德_Web端_JS_API_Key
AMAP_JS_SECURITY_CODE=填写该_JS_Key_对应的_securityJsCode
```

`HFT_DEMO_MODE=1` 可以继续保留：它只让自然语言 Router 使用离线规则，不妨碍天气和路线 Provider 设为 `live`。前端不需要单独的 `.env`：后端 `/api/config/map` 只下发本来就会公开的 JS Key；`securityJsCode` 由同源 `/_AMapService` 代理追加，不进入 Vite 构建产物。`_AMapService` 是高德 JS API 规定的一级固定前缀，不能嵌套在 `/api` 等路径下。JS Key 与安全码必须成对配置，缺少任意一项会在后端启动时明确报错。

地图直接绘制 Planner 已经消费并公开的 `RouteLeg.geometry`，不会在浏览器再次请求驾车路线；因此地图与时间线使用同一份路线事实，也不会为一次展示重复消耗路线规划配额。天气和路线的 `record` 都只保存规范化事实，不保存请求 Key。`live/record` 失败时先使用可用缓存，再尝试 replay；无匹配路线回放时明确降级为本地估算。当前缓存尚未持久化。

2026-08-27 已使用本机个人开发者 Key 完成最小真实联调：Web 服务路线返回 `amap_live`、17.751 km、28 分钟和 285 个几何点，天气返回 `amap_live`；完整 UI 请求显示高德实时天气、两段未降级高德路线，并在 JS API 2.0 底图上绘制起点、两个停靠点和路线折线。该样本证明接线与凭据可用，不代表所有地点、时段和配额边界均已验证。

填好 Web 服务 Key 后，可以先绕过完整规划链路做一次不会回显 Key 的最小联调：

```powershell
& 'D:\NWPU_career\anaconda3\envs\PyTorch\python.exe' scripts/check_amap_live.py
```

成功输出只包含路线距离、耗时、几何点数和天气事实；失败会显示高德的安全错误码（例如 Key 类型不匹配或配额限制），不会打印请求 URL 或 Key。当前 POI 来自版本化 OSM 快照，不调用高德 POI 搜索。

## POI Catalog、价格与图片边界

- `data/catalog/pois.json` 当前包含 200 条北京 POI：100 个活动、100 个餐厅。它由版本化 Overpass 查询、原始响应 replay 和生成脚本构建，基础来源为 OpenStreetMap contributors（ODbL 1.0）。
- 快照保存 WGS84，`SnapshotCatalog` 在 Adapter 内转换为当前路线 Provider 使用的 GCJ-02。旧 M1 Mock 数据只存在于 `data/fixtures/v1/`，不会混入运行快照。
- 当前覆盖率：地址 104/200、基础营业时间 110/200、许可可追溯远程图片 72/200、可靠价格 0/200；10 条 POI 保留同日多营业区间。完整报告见 `data/catalog/completeness.md`。
- 缺营业时间或亲子适配信息时保留候选并输出 Warning；已知不满足硬约束时才输出 Violation 并淘汰。未知价格不会随机补齐或当作免费，严格预算只接受 `known/free`。
- 图片通过 OSM 的 Wikidata/Commons 引用解析，只保存 URL、作者与许可元数据；缺图或加载失败显示类别占位图，不影响规划，也不会下载图片文件。

完全离线重建相同快照：

```powershell
& 'D:\NWPU_career\anaconda3\envs\PyTorch\python.exe' scripts/collect_osm_catalog.py --offline --max-records 200
```

在线刷新会访问公开 Overpass 与 Wikimedia API，不需要 API Key；应遵守服务使用策略并控制频率。当前脚本不是通用爬虫，也不抓取大众点评。

## 启动前后端

项目已在 `D:\NWPU_career\anaconda3\envs\PyTorch`（Python 3.11）验证。首次运行先安装后端依赖：

```powershell
Set-Location E:\04_Develop\Projects\PycharmProjects\happyFreeTime
& 'D:\NWPU_career\anaconda3\envs\PyTorch\python.exe' -m pip install --no-cache-dir -r requirements.txt
```

打开第一个 PowerShell，启动离线后端：

```powershell
Set-Location E:\04_Develop\Projects\PycharmProjects\happyFreeTime
.\scripts\start_backend_demo.ps1
```

打开第二个 PowerShell，启动前端：

```powershell
Set-Location E:\04_Develop\Projects\PycharmProjects\happyFreeTime
.\scripts\start_frontend.ps1
```

访问 `http://127.0.0.1:5173/`。后端健康检查是 `http://127.0.0.1:8000/api/health`。

若 PowerShell 的执行策略阻止脚本，可直接运行：

```powershell
$env:HFT_DEMO_MODE='1'
$env:LANGGRAPH_STRICT_MSGPACK='true'
& 'D:\NWPU_career\anaconda3\envs\PyTorch\python.exe' -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000

# 另一个终端
Set-Location E:\04_Develop\Projects\PycharmProjects\happyFreeTime\frontend
pnpm install --store-dir ..\.pnpm-store
pnpm run dev
```

## 按切片分别测试

所有后端切片都能脱离浏览器单独测试：

```powershell
$PYTHON='D:\NWPU_career\anaconda3\envs\PyTorch\python.exe'
$env:LANGGRAPH_STRICT_MSGPACK='true'

# RouterExtractor：结构化输出、重试和澄清降级
& $PYTHON -m unittest tests.test_router_extractor -v

# Enrichment：日期/时间/距离规则和默认假设
& $PYTHON -m unittest tests.test_enrichment -v

# NeedQuestion Gate：哪些字段阻断、哪些字段允许默认
& $PYTHON -m unittest tests.test_question_gate -v

# Entry Graph：旁路、规划、interrupt/resume
& $PYTHON -m unittest tests.test_entry_graph -v

# Planning：2/3/4 站方案、天气剪枝、finalist 路线复核和冲突
& $PYTHON -m unittest tests.test_planning -v

# 原生 Planning / Verifier：多站骨架、偏好变体、营业边界和 Route Leg 预算
& $PYTHON -m unittest tests.test_native_planning -v

# Weather Provider：模式一致性、TTL、失败短缓存和降级
& $PYTHON -m unittest tests.test_weather_provider -v

# Route Provider：高德 v5 契约、record/replay、TTL 和本地估算降级
& $PYTHON -m unittest tests.test_route_provider -v

# Catalog：采集/回放、snapshot schema、未知语义与单资源硬约束剪枝
& $PYTHON -m unittest tests.test_catalog tests.test_catalog_collection tests.test_snapshot_catalog -v

# Persistence：user_id 会话隔离、消息与 Planning Run 持久化
& $PYTHON -m unittest tests.test_persistence -v

# FastAPI：创建/列出/恢复会话、请求幂等、反问恢复和用户隔离
& $PYTHON -m unittest tests.test_api -v

# 离线 Demo Router
& $PYTHON -m unittest tests.test_demo_router -v

# 5 条离线行为 smoke eval（绕过 Router、Graph、HTTP、SQLite 和 React）
& $PYTHON -m evals.run_smoke

# 全部后端测试
& $PYTHON -m unittest discover -s tests -v
```

前端类型检查和生产构建：

```powershell
Set-Location frontend
pnpm run build
```

## 代码入口

- `app/orchestration/entry_graph.py`：Graph 节点、路由和 interrupt/resume。
- `app/domain/`：节点间 Pydantic 数据契约。
- `app/services/router_extractor.py`：真实 LLM 结构化抽取。
- `app/services/demo_router.py`：不需要 Key 的离线抽取器。
- `app/services/enrichment.py`：确定性规则、默认值和 provenance。
- `app/services/question_gate.py`：阻断式反问策略。
- `app/services/planning.py`：显式 2/3/4 站骨架、有界序列组合、可解释规则评分和 finalist 路线复核。
- `app/services/catalog.py`：Catalog seam、snapshot/fixture Adapter、schema 校验、坐标归一化和单资源硬约束剪枝。
- `app/services/catalog_collection.py` 与 `scripts/collect_osm_catalog.py`：Overpass/Commons 采集、去重、质量报告和离线 replay。
- `app/domain/catalog.py`：StopCandidate、CatalogSource 与 ConstraintViolation 契约。
- `app/providers/weather.py`：天气 live/record/replay/mock Adapter、缓存与降级。
- `app/providers/route.py`：路线 live/record/replay/mock Adapter、缓存与本地估算降级。
- `app/providers/web_map.py`：高德 JS API 的公开配置、安全码代理和固定上游白名单。
- `app/domain/providers.py`：Provider 模式、来源及天气/路线事实契约。
- `app/api/application.py`：FastAPI 与 LangGraph/SQLite 的组合入口。
- `app/persistence/repositories.py`：Session View、最近会话和 Planning Run 的事务接口。
- `frontend/src/App.tsx`：前端会话、方案选择与详情状态流。
- `frontend/src/AmapPlanMap.tsx`：加载高德 JS API，并用既有 `RouteLeg.geometry` 绘制路线和 N 站 marker。

## 推荐阅读顺序

第一次学习项目时，不建议从前端或 ORM 逐文件硬啃。按一次请求的流向阅读：

1. `app/domain/constraints.py`：先分清 RawConstraints、NormalizedConstraints、Assumption。
2. `app/services/demo_router.py`：用最简单的规则实现理解 Router 接口。
3. `app/services/enrichment.py`：理解“缺失可默认，显式歧义不可覆盖”。
4. `app/services/question_gate.py`：理解哪些信息会触发 blocking question。
5. `app/orchestration/entry_graph.py`：把前三个模块串成 Graph，并观察 interrupt/resume。
6. `app/services/planning.py` 与 `app/domain/planning.py`：理解候选、硬约束和冲突。
7. `app/api/application.py`：理解 HTTP 请求如何判断新一轮还是反问答案。
8. `frontend/src/App.tsx`：最后看 UI 如何消费统一的 question/plans/conflict 响应。

每读完一层，先运行该层对应的单元测试，再给一个已有测试改输入或补断言。
这样学习的是模块的接口和行为，不只是阅读实现细节。

V1 代码保留在原目录；V2 主实现位于 `app/`，离线评测位于 `evals/`。

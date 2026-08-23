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
| [`docs/learning/milestones/m2_trustworthy_planning.md`](docs/learning/milestones/m2_trustworthy_planning.md) | M2 天气、路线与 Catalog C1/C2 的领域契约、证据和限制 |
| [`docs/product/product_idea_inbox.md`](docs/product/product_idea_inbox.md) | 尚未批准实现的临时想法 |
| [`docs/collaboration/session_bootstrap.md`](docs/collaboration/session_bootstrap.md) | 新开 Codex 会话时的协作启动说明 |

> 学习进度有独立价值：代码里程碑完成只说明功能存在，学习里程碑完成还要求能够解释设计、验证行为、回答追问并完成小实验。

## 前端现在可以做什么

| 操作                      | 当前状态           | 说明                                                     |
| ------------------------- | ------------------ | -------------------------------------------------------- |
| 输入自然语言规划目标      | 已完成             | 可以自由输入，也可以点击三条预置建议                     |
| 自动补全非关键条件        | 已完成             | 日期、时间、预算、距离、同行人等默认值会显示为“本次假设” |
| 缺少关键条件时反问        | 已完成             | 前端继续输入答案，Graph 从 SQLite checkpoint 恢复        |
| 生成并比较候选方案        | 已完成             | 当前主场景是“活动 + 餐厅”的双站方案，最多显示 3 个       |
| 选择候选方案              | 已完成             | 点击方案卡后，右侧详情随选择更新                         |
| 查看行程时间线            | 已完成             | 显示开始/结束时间、价格、站点和站间耗时                  |
| 查看地图页签              | M2 路线摘要        | 显示路线来源、复核后的距离/耗时和降级原因；仍不是可交互真实地图 |
| 查看约束冲突              | 已完成             | 无可行方案时显示原因与可放宽方向，不伪造推荐结果         |
| 新建规划                  | 已完成             | 清空当前前端状态，下一次发送时创建新会话                 |
| 移动端使用                | 已完成             | 375 px 起可用，方案卡可横向滑动，详情下沉展示            |
| 最近会话列表              | 后端已存、前端未接 | 左栏目前只展示当前会话，尚不能加载历史会话               |
| 修改假设值                | 未完成             | 当前只能查看假设，还没有点击编辑控件                     |
| 天气事实与雨天可行性      | M2 切片 A 已完成   | 支持 mock/replay、live/record Adapter、缓存降级与来源展示；真实 Key 尚未验证 |
| 路线复核与完整双站时间线  | M2 切片 B 已完成   | finalist 才复核路线并重建时间线；支持缓存、replay、本地估算降级；真实 Key 尚未验证 |
| Catalog 来源与单资源剪枝  | M2 切片 C 已完成   | 默认离线读取 36 条 OSM 北京 POI；来源、许可、采集时间、坐标系和核验状态可追溯，单资源硬约束在组合前剪枝 |
| 动态 POI 与打车执行       | 未完成             | 当前 POI 是版本化静态快照且未现场核验；动态库存、可靠价格、实时 POI 检索和真实叫车仍未实现 |
| 订单/预订执行             | 占位               | “订单”页签是 M4 产品闭环的入口，目前不能下单             |

## 推荐的前端验收场景

1. **可行方案：** 点击“周六和朋友聚一下，人均150”，应看到候选卡、假设、行程时间线和地图估算。
2. **默认值：** 输入“安排一个轻松的约会，想吃甜品”，观察系统补充的日期、时间、预算和距离假设。
3. **反问恢复：** 输入“今天下午出去玩，别超预算”，收到预算反问后输入“人均300”。两条消息属于同一会话，Graph 会继续执行。
4. **约束冲突：** 上述反问后输入“人均200”，可能返回严格预算下无双站方案，并展示放宽建议。
5. **闲聊旁路：** 在离线模式输入“你好”，应直接回复，不进入规划节点。
6. **方案切换：** 生成多个候选后点击第二张方案卡，检查右侧行程和地图信息随之更新。
7. **新建会话：** 点击左侧“新建规划”，当前界面清空；数据库中的旧会话不会被删除。

静态 Catalog 中的营业时间、距离和价格核验状态会影响结果，因此某些“今天”或严格预算请求返回不可行冲突是预期业务结果，不代表接口失败。

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

`LLM_API` 只用于 RouterExtractor 的结构化语义抽取。活动与餐厅默认来自版本化的 `data/catalog/pois.csv`，不因配置 LLM Key 自动更新；路线仅在显式启用 route `live/record` 且提供后端高德 Key 时请求高德。

天气 Provider 独立使用后端环境变量；密钥不会返回前端或写入 fixture：

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

# live / record 才要求高德 Web 服务 Key
# HFT_PROVIDER_MODE=live
# AMAP_WEB_SERVICE_KEY=your-backend-only-key
```

天气和路线的 `record` 都只保存规范化事实，不保存请求 Key。`live/record` 失败时先使用可用缓存，再尝试 replay；无匹配路线回放时明确降级为本地估算。当前缓存尚未持久化，真实高德调用也尚未在仓库测试中验证。

## POI Catalog、价格与图片边界

- `data/catalog/pois.csv` 当前包含 36 条北京 POI：18 个活动、18 个餐厅，基础来源为 OpenStreetMap contributors，许可标记为 ODbL 1.0。
- CSV 保存原始 WGS84 坐标；`CsvCatalog` 在 Adapter 内转换为供当前高德路线接口使用的 GCJ-02，避免静默混用坐标系。
- 营业时间是采集时的静态基础时段，`verification_status=unverified`；C2 不表达节假日例外、多营业时段、过夜营业或实时闭店。
- 价格区分 `known / estimated / free / unknown`。当前 36 条价格都是本地估算；非严格预算可使用并显示“估算”，严格预算只接受 `known/free`，因此可能如实返回无解。
- 图片是可选远程引用。当前只收录许可可追溯的 Wikimedia Commons 图片；缺图或加载失败显示占位图，图片不参与召回、评分、硬约束或冲突判断，也不会下载到仓库。

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

# Planning：双站方案、天气剪枝、finalist 路线复核和冲突
& $PYTHON -m unittest tests.test_planning -v

# Weather Provider：模式一致性、TTL、失败短缓存和降级
& $PYTHON -m unittest tests.test_weather_provider -v

# Route Provider：高德 v5 契约、record/replay、TTL 和本地估算降级
& $PYTHON -m unittest tests.test_route_provider -v

# Catalog：fixture/Csv Adapter、schema、许可、价格语义与单资源硬约束剪枝
& $PYTHON -m unittest tests.test_catalog tests.test_csv_catalog -v

# Persistence：user_id 会话隔离和消息持久化
& $PYTHON -m unittest tests.test_persistence -v

# FastAPI：创建会话、发送消息、反问恢复和用户隔离
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
- `app/services/planning.py`：V2 规划接口、默认 CSV Catalog 和 V1 组合器适配。
- `app/services/catalog.py`：Catalog seam、CSV/fixture Adapter、schema 校验、坐标归一化和单资源硬约束剪枝。
- `app/domain/catalog.py`：StopCandidate、CatalogSource 与 ConstraintViolation 契约。
- `app/providers/weather.py`：天气 live/record/replay/mock Adapter、缓存与降级。
- `app/providers/route.py`：路线 live/record/replay/mock Adapter、缓存与本地估算降级。
- `app/domain/providers.py`：Provider 模式、来源及天气/路线事实契约。
- `app/api/application.py`：FastAPI 与 LangGraph/SQLite 的组合入口。
- `frontend/src/App.tsx`：前端会话、方案选择与详情状态流。

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

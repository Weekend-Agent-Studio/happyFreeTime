# M2 可信规划学习与证据

_代码切片与个人学习状态分开维护 · 2026-08-26_

## 当前状态

代码已完成切片 A、B、C1-C3、S0 和 D0-D3 的首版，并完成 D4 的第一批多站切片：四个版本化显式骨架覆盖 2/3/4 站，Route 调用改为按 Leg 计费且在骨架间公平分配。餐时锚点、返程、独立全程距离约束、库存和集合级多样化仍未完成，因此 M2 整体未完成。学习状态仍为 `L0 未开始`，因为尚未完成用户复述、变体实验或独立修改测试。

## 切片 A 链路

```text
NormalizedConstraints
  -> WeatherProvider(live / record / replay / mock)
  -> WeatherFact(source / timestamps / degradation)
  -> Catalog 活动召回
  -> 雨天硬规则淘汰 weather_sensitive 活动
  -> 原生显式骨架组合与有限复检
  -> CandidateSet(provider_facts + plans/conflict)
  -> FastAPI / React 天气来源展示
```

Provider 运行模式回答“如何获取”，事实来源回答“实际从哪里来”。例如 `record` 模式成功时事实仍可能来自 `amap_live`；live 失败后可能返回 `cache`、`replay` 或 `mock`，并且 `degraded_reason` 必须非空。

## 切片 B 链路

```text
本地目录召回与有界多站组合
  -> 本地估算完成初筛和评分
  -> 本地排序候选（最多验证 24 个 Route Leg、收集 3 个）
  -> RouteProvider(live / record / replay / mock)
  -> RouteFact(distance / duration / geometry / provenance)
  -> 按路线耗时重建 RouteLeg 与 Stop start/end
  -> 超时或单段超距候选淘汰
  -> FastAPI / React 路线来源与降级展示
```

组合阶段不会为所有排列请求外部路线；只有 finalist 才进入路线复核。`RouteLeg` 保存 Provider 返回的距离、耗时、geometry、来源、运行模式、核验时间和降级原因，Stop 的开始时间以对应路线段结束时间为准。高德失败时可以使用新鲜/陈旧缓存、固定回放或本地估算，但不得把降级结果标成实时路线。

## 子切片 C1 链路

```text
NormalizedConstraints
  -> Catalog.recall
  -> LocalFixtureCatalog 归一化活动与餐厅
  -> StopCandidate + CatalogSource(local fixture / unverified)
  -> 日期、人数、儿童年龄、严格预算、基础营业时间、直线距离下界剪枝
  -> 可组合 candidates + ConstraintViolation[]
  -> Planning 只组合保留候选
  -> Stop 来源与剪枝摘要进入 FastAPI / React
```

`Catalog.recall(NormalizedConstraints) -> CatalogResult` 是 C1 的稳定 seam。调用方不需要知道 JSON 文件布局或剪枝实现。严格预算才触发单资源预算硬剪枝；非严格预算仍属于可评分偏好。候选与出发地的直线距离已经超过最大距离时可以安全提前淘汰，但直线距离没有超限不代表真实路线可行，后者仍由 Route Provider 与完整 Verifier 负责。当前 fixture 的来源 URI 指向 `data/fixtures/v1/`，只作为显式测试/兼容 Adapter 使用，不会混入默认快照。

## 子切片 C2 链路

本节保留 C2 的演进背景；36 行 CSV 已由 C3 替代，不再是当前运行事实。

```text
data/catalog/pois.csv (36 条 OSM 北京 POI / WGS84)
  -> CsvCatalog 启动时 schema 与许可校验
  -> WGS84 转换为当前路线 Provider 使用的 GCJ-02
  -> StopCandidate(source/license/verification/PriceKind/ImageRef)
  -> Catalog.recall 单资源硬剪枝
  -> V1 双站组合器的中性评分兼容层
  -> finalist RouteProvider 复核
  -> FastAPI / React 来源、估算价、远程图片或占位图
```

C2 当时默认使用 `CsvCatalog`，`LocalFixtureCatalog` 只作为显式注入的测试 Adapter 保留。该 CSV 包含 18 个活动和 18 个餐厅，基础来源为 OpenStreetMap contributors，逐行保留 OSM 对象 URI 与 ODbL 1.0 标识。所有记录均为 `unverified` 静态快照；这些实现已由下述 C3 替代。

价格用 `known / estimated / free / unknown` 区分证据强度。`unknown` 不能编码为 0 元；严格预算只接受 `known/free`，其余产生 `single_resource_price_unverified` violation。当前 CSV 价格全部是本地估算，所以严格预算无解是预期保守结果。图片只保存许可可追溯的远程引用；缺图或加载失败只改变展示，不影响任何规划判断。

## 子切片 C3 链路

```text
版本化 OverpassQL + raw OSM replay + Commons 元数据 replay
  -> CatalogCollector 去重、平衡与完整度报告
  -> pois.json（200 条，100 活动 + 100 餐饮）
  -> SnapshotCatalog 坐标归一化
  -> 已知硬约束违反产生 violation；事实缺失产生 warning
  -> 最终 Stop 的 warning/source/image/category 进入 API 与 React
```

C3 将手工 36 行 CSV 改成可在线刷新、可完全离线重建的轻量数据流水线；同时删除逐字段 `estimated_fields/derived_fields/dynamic_fields_mock` 列表，以资源级来源、明确 `PriceKind` 和 Unknown/Warning 语义表达真正影响规划的证据边界。旧 M1 数据物理移动到 `data/fixtures/v1/`。

当前 200 条快照包含 104 条地址、110 条可解析基础营业时间和 72 条 Commons 许可图片，其中 10 条 POI 保留同日多营业区间，价格仍全部 Unknown。Unknown 不等于通过，也不等于违反：普通规划可保留并提醒用户；严格预算因为无法证明满足而淘汰。

## S0 原生双站规划闭环

```text
CatalogResult.candidates
  -> 活动 × 餐饮的确定性双站组合
  -> 时长、距离与严格预算初筛
  -> 偏好 / 饮食 / 场景 / 避开项 / 非严格预算规则评分
  -> 内容指纹去重并产生本地排序
  -> RouteProvider 按 Leg 预算复核，最多 24 个 Route Leg、收集 3 个可行方案
  -> 使用复核后的总时长与总距离刷新分项评分并重排
  -> CandidateSet(plans | conflict)
```

生产规划链路不再调用 V1 `generate_candidate_plans` 或 `compare_plans`。OSM ID 直接进入组合与评分，不再出现“活动不存在/餐厅不存在”。`duration_minutes` 同时参与组合硬上限和时长匹配评分；中文“甜品、安静、亲子、户外”通过版本化概念映射匹配快照标签。每个总分由带 `rule_id`、维度、分值、消息和证据的 `ScoreContribution` 组成；路线复核后，时间和距离分项会按返回的 RouteFact 刷新。

普通预算是可放宽偏好：已知或估算价格超出时降分并报告 tradeoff，Unknown 不按 0 元参与比较。严格预算仍是硬约束；只有预算确实阻塞时才返回预算 conflict。组合以资源 ID 内容指纹去重，同一 POI 组合不能靠更换标题或策略伪装成多样性。

## D0 整单验证与有界回填

```text
本地排序后的骨架组合
  -> RouteProvider 重建实际 RouteLeg 与 Stop 时间线
  -> Plan Verifier 统一检查时间窗、指定时长、单段距离与实际停留营业区间
  -> 失败候选记录结构化 violation field
  -> 在不同骨架间轮询验证后续候选，最多消费 24 个 Route Leg，得到 3 个即停止
  -> 全部失败时优先聚合所有尝试共有的约束，并给出对应放宽建议
```

营业时间采用保守的基础语法：支持一个自然日内逗号分隔的多个 `HH:MM-HH:MM` 区间；到达恰好开门、离开恰好关门可行，停留跨过关门边界不可行。不支持的 OSM 复杂表达不被猜测，也不作为“已关闭”的证据，而是保留候选并输出未核验 warning。采集器与运行时使用同一语义，离线重建后的快照不再丢失午休后的第二、第三营业区间。

回填不是无限搜索：外部路线调用成本统一由 24 个 Route Leg 上限约束，而不是继续沿用只适合双站的“12 个 Plan”预算。候选按骨架分组轮询，避免一个高分四站骨架先耗尽全部外部调用；得到 3 个可行方案即提前停止。这个切片消除了“Top-3 都因真实路线或营业边界失效，但后续候选可执行却返回无解”的假性冲突，也没有引入 Beam Search 或无界重试。

## D1-D4 多站骨架第一版

```text
NormalizedConstraints
  -> 确定性 PlanningIntent（节奏、站数上限、可选角色与证据）
  -> 选择版本化 PlanSkeleton（2/3/4 站）
  -> StopRole 映射可承担该角色的资源类型
  -> 多站角色池按语义与距离预排序到每角色最多 8 个
  -> 对有界空间完整枚举、去除同资源重复、执行本地硬剪枝与分层评分
  -> 按骨架公平选择 Route finalist
  -> 重建全部 Route Leg / Stop 时间线并交给 PlanVerifier
  -> CandidateSet(plans | conflict)
```

当前四个骨架是 `ACTIVITY -> MEAL`、`LUNCH -> ACTIVITY -> DINNER`、`ACTIVITY -> BREAK -> DINNER` 和 `ACTIVITY -> LUNCH -> ACTIVITY -> DINNER`。`StopRole` 与资源类型分离：一般 `MEAL` 可由餐厅、咖啡或甜品承担，`BREAK` 可由咖啡或甜品承担，明确的 `LUNCH/DINNER` 当前只由餐厅承担。多站每个角色最多保留 8 个候选，四站两个活动/两个餐饮角色的唯一排列上限约 3,136 个；双站不截断，从而保持 S0 行为。

`PlanningIntent` 当前由确定性规则构造，LLM 仍只在 RouterExtractor 做需求抽取。时间窗只决定某个骨架有没有资格，并不把时长硬映射成唯一站数；真实 POI、路线和完整 Verifier 决定最终可行性。尚未实现的餐时锚点意味着 `LUNCH/DINNER` 目前表达顺序角色，不保证到达时间落在独立午餐/晚餐窗口，这一点不能在面试或 README 中夸大。

## 已有自动证据

- `tests/test_weather_provider.py`：live 字段映射、mock/record/replay 一致性、成功缓存 TTL、失败短缓存与降级原因。
- `tests/test_planning.py`：固定回放中雨时，`act_002` 户外骑行被淘汰，`act_001` 室内亲子馆仍可进入方案。
- `tests/test_api.py`：消息响应公开天气 condition、source、verified/degraded 字段。
- `tests/test_route_provider.py`：高德 Route Planning 2.0 字段映射、record/replay 一致性、20 分钟成功缓存 TTL、失败短缓存和本地估算降级。
- `tests/test_web_map_provider.py`：JS Key 公开配置不包含安全码、代理覆盖客户端伪造的 `jscode`、固定高德上游和成对配置失败快显。
- `tests/test_planning.py`：只对 finalist 请求两段路线；Provider 耗时重建 Stop 时间线；复核后超时或单段超距时全部候选淘汰并返回结构化 conflict。
- `tests/test_api.py`：路线 source、provider_mode、verified_at 和 RouteLeg/Stop 时间边界通过 HTTP 可见。
- `tests/test_catalog.py`：来源缺失拒绝；本地 fixture 来源元数据；日期、人数、儿童年龄、严格预算、基础营业时间和直线距离下界的资源级剪枝及 violation 聚合。
- `tests/test_planning.py`：被 Catalog 淘汰的资源不进入组合，最终 Stop 保留来源和核验状态。
- `tests/test_api.py`：Stop 来源及 `catalog_violations` 通过 HTTP 可见；React 展示未核验/Mock 状态和剪枝数量。
- `tests/test_catalog_collection.py`：确定性采集转换、跨查询重复 OSM ID 去重、Commons 许可映射和失败降级。
- `tests/test_snapshot_catalog.py`：200 条 OSM 覆盖、100/100 类型平衡、唯一 ID、质量下限、WGS84→GCJ-02、Unknown 价格和图片许可。
- `tests/test_catalog.py`：严格预算拒绝 `estimated/unknown`，不会把价格未知当作免费。
- `tests/test_planning.py`：默认 snapshot Catalog 可离线进入组合并完成 finalist 路线复核，只公开最终 Stop 的 Unknown warnings。
- `tests/test_api.py`：HTTP 公开 OSM URI、ODbL、`price_kind`、图片字段和价格未核验 conflict。
- `tests/test_native_planning.py`：偏好与时长变体、真实 OSM ID、结构化评分、路线复核后评分刷新、饮食/场景/避开项/普通预算、冲突归因和组合去重。
- `tests/test_native_planning.py`：实际到达/完整停留营业验证、开关门边界、多营业区间、Top 候选失效后的回填、24 段路线的调用上限，以及按共同失败原因聚合 conflict。
- `tests/test_native_planning.py`：通用 `ACTIVITY -> MEAL` 角色可由 Cafe 承担；长时间窗可生成 `LUNCH -> ACTIVITY -> DINNER`、`ACTIVITY -> BREAK -> DINNER` 与 `ACTIVITY -> LUNCH -> ACTIVITY -> DINNER`；午晚餐角色不被 Cafe/Dessert 误填；长骨架失败时不会饿死低分可行骨架。
- `tests/test_planning.py`：Provider 返回跨日乃至超百小时的异常耗时时，使用数值时间线判断并返回 `time_window` conflict，不因非法 `time(hour=...)` 或字符串比较而崩溃/误放行。
- `tests/test_catalog_collection.py` 与 `tests/test_snapshot_catalog.py`：多营业区间不会在采集时截断，版本化快照实际包含多区间 POI。
- 本轮后端全量回归 102/102（另含 2 个 subtests）、5 条离线 smoke 与前端生产构建通过；200 条快照的 09:00-22:00 冒烟请求约 2.25 秒完成并返回含四站的方案。真实 Key 自检返回 `amap_live` 路线/天气，完整 UI 双站请求显示未降级高德路线，浏览器地图显示底图、路线和 3 个 marker。2026-08-28 又以本地浏览器完成了“创建两条会话、侧栏切换、刷新、前进/后退”的手工回归；该证据仍不等于仓库内可重复执行的 Playwright E2E，也不证明真实高德长期质量、POI 当前营业/价格准确或所有多站变体。

## 已知限制

- 已用本机 Key 验证一条天气、一条独立路线和一次完整双站规划；仍需补录制回放样本、更多线上失败类型与可重复浏览器 E2E，不能把单次成功外推为 SLA。
- 首片仅内置北京核心城区到天气 `adcode` 的小范围映射；完整地理编码由后续 Provider 切片接管。
- 缓存当前为进程内存，不是路线图目标的持久化 `provider_cache`。
- 天气规则只处理活动的 `weather_sensitive` 字段；完整天气 Verifier、warning 聚合与局部重规划尚未实现。
- Planner 已支持四个显式 2/3/4 站骨架；默认 mock 模式明确使用本地路线估算，live/replay 可逐段复核并有界回填候选，但尚未实现餐时锚点、返程、库存验证、完整天气 warning 聚合和集合级多样化。
- 当前默认 Catalog 是 200 条来源可追溯但未现场核验的 OSM 静态快照，不是实时 POI 搜索；实时闭店、节假日例外与定时刷新任务尚未实现。
- C1 先做请求窗口与基础营业时段是否相交，并以直线距离做明显超范围的安全下界剪枝；D0 已按实际到达/停留时间核验同日开关门边界。节假日、跨夜营业与实时临时闭店仍未知；`max_distance_km` 当前保持既有“召回半径/单段上限”语义，尚未新增独立的全程距离约束。
- 当前 snapshot 价格全部 Unknown，严格预算会全部拒绝；普通预算只报告价格证据不足，不把 Unknown 当免费。动态库存、排队与更多策略仍未实现。
- 当前 72 条 POI 具有许可明确的 Wikimedia Commons 远程图片，其余显示类别占位图；图片未做代理、下载或永久保存。
- 浏览器刷新恢复、最近会话切换和 URL 导航已实现；仓库内可重复执行的 Playwright E2E 仍待补充。

## 建议学习实验

1. 把回放天气从“中雨”改成“晴”，先预测 `act_002` 是否重新出现。
2. 把成功缓存时钟推进到 TTL 前后，解释为何调用次数从 1 变为 2。
3. 让 primary 抛出异常，比较 `source`、`mode` 和 `degraded_reason` 三者含义。
4. 把回放路线耗时从 20 分钟改成 60 分钟，预测为何方案会转为 `NO_PLAN_AFTER_ROUTE_VERIFICATION`。
5. 比较组合阶段的候选数与 RouteProvider 调用数，解释为什么不建立全量路线矩阵。
6. 对同一份 snapshot 分别启用和关闭严格预算，解释为什么 `unknown` 在前者形成 `single_resource_price_unverified`、在后者只形成价格不完整展示。
7. 在临时 snapshot 中把一条价格改为 `known`，预测它能否通过严格预算，并验证 `unknown` 不能写成 0 元。
8. 把儿童年龄从 6 改为 10，观察 `child_age_not_supported` violation 如何变化。

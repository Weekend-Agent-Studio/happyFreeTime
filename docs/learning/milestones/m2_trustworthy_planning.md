# M2 可信规划学习与证据

_代码切片与个人学习状态分开维护 · 2026-08-24_

## 当前状态

代码已完成切片 A“天气 Provider 与雨天可行性”、切片 B“Route Provider 与完整双站时间线”以及切片 C 的 C1-C3“Catalog 契约、硬剪枝与可重建北京 POI”；M2 整体仍未完成。学习状态仍为 `L0 未开始`，因为尚未完成用户复述、变体实验或独立修改测试。

## 切片 A 链路

```text
NormalizedConstraints
  -> WeatherProvider(live / record / replay / mock)
  -> WeatherFact(source / timestamps / degradation)
  -> Catalog 活动召回
  -> 雨天硬规则淘汰 weather_sensitive 活动
  -> 原双站组合与有限复检
  -> CandidateSet(provider_facts + plans/conflict)
  -> FastAPI / React 天气来源展示
```

Provider 运行模式回答“如何获取”，事实来源回答“实际从哪里来”。例如 `record` 模式成功时事实仍可能来自 `amap_live`；live 失败后可能返回 `cache`、`replay` 或 `mock`，并且 `degraded_reason` 必须非空。

## 切片 B 链路

```text
本地目录召回与双站组合
  -> 本地估算完成初筛和评分
  -> 最多 3 个 finalist
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

当前 200 条快照包含 99 条地址、110 条可解析基础营业时间和 72 条 Commons 许可图片，价格仍全部 Unknown。Unknown 不等于通过，也不等于违反：普通规划可保留并提醒用户；严格预算因为无法证明满足而淘汰。

## 已有自动证据

- `tests/test_weather_provider.py`：live 字段映射、mock/record/replay 一致性、成功缓存 TTL、失败短缓存与降级原因。
- `tests/test_planning.py`：固定回放中雨时，`act_002` 户外骑行被淘汰，`act_001` 室内亲子馆仍可进入方案。
- `tests/test_api.py`：消息响应公开天气 condition、source、verified/degraded 字段。
- `tests/test_route_provider.py`：高德 Route Planning 2.0 字段映射、record/replay 一致性、20 分钟成功缓存 TTL、失败短缓存和本地估算降级。
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
- 严格 msgpack 后端全量回归、5 条离线 smoke 和前端生产构建通过；这些证据不证明真实高德质量、POI 当前营业/价格准确或浏览器交互。

## 已知限制

- 未使用真实付费 Key 调用高德；天气与路线 live Adapter 只依据高德官方 schema 做了 Fake transport 契约测试。
- 首片仅内置北京核心城区到天气 `adcode` 的小范围映射；完整地理编码由后续 Provider 切片接管。
- 缓存当前为进程内存，不是路线图目标的持久化 `provider_cache`。
- 天气规则只处理活动的 `weather_sensitive` 字段；完整天气 Verifier、warning 聚合与局部重规划尚未实现。
- Planner 仍是双站 V1 兼容实现；默认 mock 模式明确使用本地路线估算，live/replay 可复核 finalist，但尚未实现 3/4 站组合、全程累计距离和局部替换。
- 当前默认 Catalog 是 200 条来源可追溯但未现场核验的 OSM 静态快照，不是实时 POI 搜索；实时闭店、节假日例外与定时刷新任务尚未实现。
- C1 只做请求窗口与基础营业时段是否相交，并以直线距离做明显超范围的安全下界剪枝；按实际到达/停留时间核验关门边界、按真实路线汇总全程距离属于 D。
- 当前 snapshot 价格全部 Unknown，严格预算会全部拒绝；评分使用通过硬剪枝后的中性兼容分，完整多策略质量评分属于 E。动态库存和排队仍未实现。
- 当前 72 条 POI 具有许可明确的 Wikimedia Commons 远程图片，其余显示类别占位图；图片未做代理、下载或永久保存。
- 浏览器刷新恢复仍是 M1 已知缺口，本切片未改变。

## 建议学习实验

1. 把回放天气从“中雨”改成“晴”，先预测 `act_002` 是否重新出现。
2. 把成功缓存时钟推进到 TTL 前后，解释为何调用次数从 1 变为 2。
3. 让 primary 抛出异常，比较 `source`、`mode` 和 `degraded_reason` 三者含义。
4. 把回放路线耗时从 20 分钟改成 60 分钟，预测为何方案会转为 `NO_PLAN_AFTER_ROUTE_VERIFICATION`。
5. 比较组合阶段的候选数与 RouteProvider 调用数，解释为什么不建立全量路线矩阵。
6. 对同一份 snapshot 分别启用和关闭严格预算，解释为什么 `unknown` 在前者形成 `single_resource_price_unverified`、在后者只形成价格不完整展示。
7. 在临时 snapshot 中把一条价格改为 `known`，预测它能否通过严格预算，并验证 `unknown` 不能写成 0 元。
8. 把儿童年龄从 6 改为 10，观察 `child_age_not_supported` violation 如何变化。

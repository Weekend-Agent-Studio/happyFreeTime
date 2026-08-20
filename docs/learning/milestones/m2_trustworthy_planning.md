# M2 可信规划学习与证据

_代码切片与个人学习状态分开维护 · 2026-08-20_

## 当前状态

代码已完成切片 A“天气 Provider 与雨天可行性”、切片 B“Route Provider 与完整双站时间线”以及子切片 C1“Catalog 契约与本地资源硬剪枝”；正式 C 和 M2 整体均未完成。学习状态仍为 `L0 未开始`，因为尚未完成用户复述、变体实验或独立修改测试。

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

`Catalog.recall(NormalizedConstraints) -> CatalogResult` 是 C1 的稳定 seam。调用方不需要知道 JSON 文件布局或剪枝实现。严格预算才触发单资源预算硬剪枝；非严格预算仍属于可评分偏好。候选与出发地的直线距离已经超过最大距离时可以安全提前淘汰，但直线距离没有超限不代表真实路线可行，后者仍由 Route Provider 与完整 Verifier 负责。当前 fixture 的来源 URI 指向仓库文件，`verification_status=unverified`，价格、评分、库存、排队等字段通过 `dynamic_fields_mock` 明确声明为 Mock。

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
- 严格 msgpack 后端全量回归 `51/51`、5 条离线 smoke 和前端生产构建通过；这些证据不证明真实高德质量、真实 POI 或浏览器交互。

## 已知限制

- 未使用真实付费 Key 调用高德；天气与路线 live Adapter 只依据高德官方 schema 做了 Fake transport 契约测试。
- 首片仅内置北京核心城区到天气 `adcode` 的小范围映射；完整地理编码由后续 Provider 切片接管。
- 缓存当前为进程内存，不是路线图目标的持久化 `provider_cache`。
- 天气规则只处理活动的 `weather_sensitive` 字段；完整天气 Verifier、warning 聚合与局部重规划尚未实现。
- Planner 仍是双站 V1 兼容实现；默认 mock 模式明确使用本地路线估算，live/replay 可复核 finalist，但尚未实现 3/4 站组合、全程累计距离和局部替换。
- 当前只有 4 个活动和 4 个餐厅本地 fixture；来源契约已经存在，但它们不是已核验真实 POI。正式 C 仍缺 30–50 个许可明确的北京 POI。
- C1 只做请求窗口与基础营业时段是否相交，并以直线距离做明显超范围的安全下界剪枝；按实际到达/停留时间核验关门边界、按真实路线汇总全程距离属于 D。
- 动态价格、评分、库存和排队仍是 Mock；完整 `warning` 聚合尚未实现。
- 浏览器刷新恢复仍是 M1 已知缺口，本切片未改变。

## 建议学习实验

1. 把回放天气从“中雨”改成“晴”，先预测 `act_002` 是否重新出现。
2. 把成功缓存时钟推进到 TTL 前后，解释为何调用次数从 1 变为 2。
3. 让 primary 抛出异常，比较 `source`、`mode` 和 `degraded_reason` 三者含义。
4. 把回放路线耗时从 20 分钟改成 60 分钟，预测为何方案会转为 `NO_PLAN_AFTER_ROUTE_VERIFICATION`。
5. 比较组合阶段的候选数与 RouteProvider 调用数，解释为什么不建立全量路线矩阵。
6. 把严格人均预算从 100 改为 130，预测哪些资源会重新进入 Catalog 候选。
7. 把儿童年龄从 6 改为 10，观察 `child_age_not_supported` violation 如何变化。

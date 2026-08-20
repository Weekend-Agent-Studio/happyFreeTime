# M2 可信规划学习与证据

_代码切片与个人学习状态分开维护 · 2026-08-20_

## 当前状态

代码已完成切片 A“天气 Provider 与雨天可行性”的首个最小纵向闭环；M2 整体未完成。学习状态仍为 `L0 未开始`，因为尚未完成用户复述、变体实验或独立修改测试。

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

## 已有自动证据

- `tests/test_weather_provider.py`：live 字段映射、mock/record/replay 一致性、成功缓存 TTL、失败短缓存与降级原因。
- `tests/test_planning.py`：固定回放中雨时，`act_002` 户外骑行被淘汰，`act_001` 室内亲子馆仍可进入方案。
- `tests/test_api.py`：消息响应公开天气 condition、source、verified/degraded 字段。
- 后端全量回归与前端生产构建用于证明既有链路和类型/打包未回退；它们不证明真实高德质量或浏览器交互。

## 已知限制

- 未使用真实付费 Key 调用高德；live Adapter 只依据高德官方 forecast schema 做了 Fake transport 契约测试。
- 首片仅内置北京核心城区到天气 `adcode` 的小范围映射；完整地理编码由后续 Provider 切片接管。
- 缓存当前为进程内存，不是路线图目标的持久化 `provider_cache`。
- 天气规则只处理活动的 `weather_sensitive` 字段；完整天气 Verifier、warning 聚合与局部重规划尚未实现。
- Planner 仍是双站 V1 兼容实现，路线仍为本地估算；POI 来源、实际到达营业时间和库存均未进入本切片。
- 浏览器刷新恢复仍是 M1 已知缺口，本切片未改变。

## 建议学习实验

1. 把回放天气从“中雨”改成“晴”，先预测 `act_002` 是否重新出现。
2. 把成功缓存时钟推进到 TTL 前后，解释为何调用次数从 1 变为 2。
3. 让 primary 抛出异常，比较 `source`、`mode` 和 `degraded_reason` 三者含义。

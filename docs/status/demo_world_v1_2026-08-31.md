# Demo World V1 + POI 详情全栈贯通（2026-08-31）

## 已完成

- 在 `data/demo_world/v1/` 建立 V1 商业演示世界：以 200 条 OSM snapshot anchor 为基线，所有 `resource_id`、名称和坐标锚点不变。
- `scripts/build_demo_world.py` 以 `resource_id + 固定版本种子` 离线生成 enrichment；`scripts/validate_demo_world.py` 检验生成物、覆盖率和必填字段。当前覆盖率为 **200/200（100%）**。
- 每条 enrichment 包含商圈/地址展示、描述、场景/设施、室内与天气、亲子、参考价格、演示评分和评论数、完整展示营业时段、建议时长、预约/排队、风险、预订方式与画廊。
- `DemoCatalog` 保持 `Catalog.recall(...)` 不变，合并 anchor 与 enrichment；`HFT_CATALOG_MODE=demo|snapshot` 可切换，默认 `demo`。模拟价格、营业、亲子和天气字段已进入现有剪枝/规划/Verifier，而不是只给前端装饰。
- `PoiPresentation` 是独立展示契约，按方案中实际使用的 `resource_id` 批量返回。`AgentResponse` 和 SQLite Planning Run 快照保存它，因此刷新会话不会丢失详情。
- 前端在现有“日光晴蓝”布局中加入可展开 POI 详情：画廊或明确示意图、地址/标签、演示评分、完整营业摘要、参考价格、预约/排队/亲子/天气提示、地图入口和折叠数据说明；移动端仍使用全屏详情工作区。
- 默认 API 使用 `DemoAvailabilityProvider`，它生成稳定的模拟 available/unavailable 状态，但仍由既有 AvailabilityProvider/Verifier/Repair Contract 消费；不声称实时库存。

## 数据语义

| 类别 | 内容 |
| --- | --- |
| SourceFact | OSM snapshot 的名称、类别、资源 ID、坐标、已有地址和 Wikimedia 图片元数据 |
| DerivedFeature | WGS84→GCJ02、路线和几何、时间线、评分和 verifier 结论 |
| SimulatedState | 商业参考价格/评分/评论、营业展示、设施、亲子/天气适配、预约/排队和 Demo Availability |

这种形式不是“随机 Mock”：同一输入和资源永远得到同一业务世界，因而预算筛选、局部修复、回放、评测和 UI 演示都可复现；又不需要把项目重心变成 200 家商户的人工采集。页面统一提示商业信息为模拟数据、地图与路线来自高德。

## 明确边界

- 没有真实库存、支付、订单、商户履约、电话、官网或外部预订 URL。
- 不使用运行时爬虫，不复制其他项目的数据，不下载许可不明确的图片。
- `snapshot` 模式仍保留原始 OSM 的未知价格/营业语义，供基线和测试使用。

## 本轮验证

- `python scripts/validate_demo_world.py`
- `pytest tests/test_demo_world.py -q`：4 passed
- `pytest tests/test_api.py -q`：15 passed, 2 subtests passed
- 后续全量 `pytest`、smoke、Vitest、build、Playwright 以本轮最终汇总为准。

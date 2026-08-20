# M2“可信规划”纵向切片计划

_状态快照与实施计划 · 2026-08-20；路线图仍以 `docs/canonical/v2_roadmap.md` 为准_

## 基线与差距

基线 `a99dc45` 已具备 Router → Enrichment → Gate → Planning → API/SQLite → React 主链，后端 30/30、前端生产构建通过。当前 Planner 仍是活动 + 餐厅双站 Mock 组合，只进行结构、结束时间、严格预算和正分数复检；路线是 Haversine 估算，POI 缺统一来源元数据，也没有按实际到达时间验证营业、库存、天气和全程距离。

M2 不一次重写 Planner，而是让每片都增加一条用户可观察、可测试、可降级的可信能力。共同不变量：LLM 不决定或放宽硬约束；单候选失败叫 `violation`，全部淘汰才叫 `conflict`；偏好损失才叫 `tradeoff`；数据不确定或降级叫 `warning`。

## A. 天气 Provider 与雨天可行性

**实现状态：** 已完成并提交；M2 整体未完成。

**用户故事：** 北京雨天亲子半日规划仍能离线重复运行；户外敏感活动不进入结果，室内候选保留，用户能看到天气事实来自哪里以及是否降级。

**接口与契约：** `WeatherProvider.get_weather(WeatherRequest) -> WeatherFact`。`WeatherFact` 公开 `source`、`mode`、`observed_at`、`verified_at`、`cache_age_seconds`、`degraded` 和 `degraded_reason`；运行模式与事实来源正交。`CandidateSet.provider_facts` 把规划实际消费的天气事实带到 HTTP/UI。

**验收：** `mock/replay` 无网络可运行；雨天淘汰 `weather_sensitive=true` 候选；室内候选可形成方案；live/record 缺 Key 时明确拒绝启动；实时失败可用新鲜/陈旧缓存、replay 或 mock 降级且不冒充实时。

**测试：** Provider contract；mock/record/replay 观测一致性；成功 TTL；失败短缓存；高德字段映射与密钥不出现在领域输出；雨天规划行为；HTTP 来源字段；前端生产构建。

**不做：** 不声明已用真实 Key 验证；不实现路线、完整多站 Verifier、POI 元数据迁移或时间强度；不把 Mock 天气称为真实天气。

## B. Route Provider 与完整双站时间线

**实现状态：** 已完成本切片；finalist 路线复核支持超时与单段超距淘汰。全程累计距离属于 D 的完整 Verifier，尚未实现。

**用户故事：** 用户看到的到达时间、停留时间和结束时间包含每一段路线；路线服务失败时仍能规划，但每段明确标为估算及原因。

**接口与契约：** `RouteProvider.route(RouteRequest) -> RouteFact`；`RouteLeg` 保存 origin/destination、mode、distance、duration、geometry、source、verified_at、degraded_reason。组合阶段只用本地估算，finalist 才调用 Provider，返回后重建时间线。

**验收：** 路线复核后所有 Stop 的 arrival/start/end 重新计算；若复核导致超时或超距则淘汰/局部替换；高德失败按缓存 → 本地估算降级并公开。

**测试：** Provider contract、TTL、失败短缓存、只调用 finalist、复核后时间线重建、超时淘汰、API/UI 路线来源。

**不做：** 不建立全量路线矩阵，不引入 Beam Search，不实现地图双向联动。

## C. Catalog 与单资源硬约束剪枝

**用户故事：** 进入组合器的每个 POI 已经在日期、人数、儿童年龄、预算和基础营业信息上具备单资源可行性，并能追溯数据来源。

**接口与契约：** 通用 `StopCandidate`；POI 元数据包含 `source_uri/source_name`、`collected_at`、`last_verified_at`、`verification_status`。Catalog 召回后返回候选及结构化 `ConstraintViolation[]` 剪枝摘要。

**验收：** 30–50 个真实北京 POI 基础字段可追溯；动态价格/库存明确仍是 Mock；不适龄、不容纳人数、单资源已超预算或当天不营业的资源不进入组合。

**测试：** 元数据 schema、日期/人数/年龄/预算/营业边界、来源缺失拒绝、剪枝原因聚合。

**不做：** 不抓取未确认许可的数据，不宣称动态库存真实，不做大规模语义检索。

## D. 2/3/4 站组合与完整 Verifier

**用户故事：** 短时、半日和一日方案在实际到达时间、营业、天气、预算、距离、路线时间和时间冲突上全部可验证。

**接口与契约：** `PlanVerifier.verify(plan, constraints, facts) -> VerificationResult`；结果只含 `violations` 与 `warnings`。可行 Plan 另由评分模块产生 `tradeoffs`；所有候选淘汰后由聚合器产生 `ConstraintConflict`。

**验收：** 支持合法 2/3/4 核心停靠点；到达恰好开门可行、到达/停留越过关门按正式规则淘汰；预算和距离按全程汇总；硬约束绝不因高分保留；无解给结构化放宽建议。

**测试：** 营业边界、路线导致到达变化、全程预算/距离、时间重叠、天气与库存、无解与放宽、用户隔离回归。

**不做：** 未批准 `IDEA-007` 前，不给所有时间字段统一追加 hard/soft，也不发明正负半小时容差。

## E. 多策略与真实多样性

**用户故事：** 用户得到三个都可行、但在可观察指标上真正不同的方案，而不是相同 POI 换标题。

**接口与契约：** 策略固定为 `balanced`、`low_cost`、`low_travel`、`experience`、`family_safe`、`weather_safe`；评分维度固定，策略只改变权重。多样化以 POI 重叠、成本、路程、类别和策略为可观察指标。

**验收：** 三方案均通过 Verifier；至少一个指标达到预设差异门槛；不足三个可行差异方案时如实返回较少结果或冲突说明，不复制方案凑数。

**测试：** 权重与硬约束正交、重复指纹、多样性阈值、候选不足、策略证据。

**不做：** 不用 LLM 编排行程，不上 Beam Search，不用标题差异代替内容差异。

## F. Presenter、地图时间线与评测

**用户故事：** 用户能在桌面和移动端理解三个方案、天气/路线来源和降级状态，并用地图与时间线核对同一事实。

**接口与契约：** 模板 Presenter 是必选默认；可选 LLM 只能改写已存在证据。前端消费结构化 facts/warnings，不解析自由文本来判断可行性。

**验收：** 无 LLM 时完整展示；Provider 来源与降级可见；地图 marker 和时间线联动；smoke/eval 断言硬约束与事实来源；组件测试与 Playwright 桌面/移动主路径齐备。

**测试：** 模板快照/结构断言、Provider 状态组件、方案切换、地图时间线联动、Playwright 主路径和离线 eval。

**不做：** 不把 build 或单元测试称为 E2E，不把底层隐式推理暴露给 UI，不在 M2 顺手补 M1 刷新恢复。

## Inbox 分析（均未获准实现）

### IDEA-007：时间语义、来源与强度

**建议用户故事：** 用户说“下午”“晚上吃饭”或“最晚 18:00 到家”时，系统保留原始证据，并区分可交易的期望时段与不可违反的截止时间。

**建议契约：** 新增 `ConstraintStrength = hard | preference`，与 `ConstraintSource` 正交；Router 只抽取原文、语义类型和证据，确定性规则根据明确措辞与已确认值赋 strength。建议将 `availability_window`、`meal_window`、`return_by` 分开建模，不把一个 `TimeWindow` 同时承担所有语义。

**建议验收：** “最晚 18:00 到家”违反时淘汰；系统默认下午窗口偏离时形成 tradeoff；“晚上吃饭”按版本化产品规则归一化；任何放宽都需要用户确认；Presenter 不得更改 strength。

**建议测试：** source × strength 参数化、明确截止边界、默认窗口 tradeoff、用餐衔接、冲突放宽、Router 证据保留。

**明确不做：** 不采用通用正负半小时；不把所有用户明确表达自动设为 hard；不在本切片实现。需用户批准 `IDEA-007` 后才能进入 D 或 M3。

### 其他相关想法

- `IDEA-002`：与 F 的 Provider/Router 能力徽标相邻，适合在 F 统一设计；A 只显示本次天气事实，不实现全局运行模式徽标。
- `IDEA-004`：更适合 M3 约束编辑交互，不在 M2 天气切片改约束摘要。
- `IDEA-005`：会影响 C/D 的人数与儿童剪枝，但 `minimum_people` 与 `exact_people` 尚无验收标准，不能自动实现。
- `IDEA-003`：不实现前端 Key 配置；所有 Key 仅来自后端环境。

## Git 与实施边界

- 基线提交、原工作区 HEAD 和 `origin/feature/m1-core-entry` 均为 `a99dc459ed038a2a67c9ca49160cb24196c222b2`。
- 独立分支：`feature/m2-trustworthy-planning`；原 `feature/m1-core-entry` 不切换、不修改。
- 每片按行为测试 → 最小实现 → 全量回归 → 文档 → 独立提交推进；只推送该 feature 分支，不合并 develop。

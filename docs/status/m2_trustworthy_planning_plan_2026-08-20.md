# M2“可信规划”纵向切片计划

_状态快照与实施计划 · 创建于 2026-08-20，更新于 2026-08-30；路线图仍以 `docs/canonical/v2_roadmap.md` 为准_

## 基线与差距

基线 `a99dc45` 已具备 Router → Enrichment → Gate → Planning → API/SQLite → React 主链，后端 30/30、前端生产构建通过。该基线当时的 Planner 仍是活动 + 餐厅双站 Mock 组合，只进行结构、结束时间、严格预算和正分数复检；路线是 Haversine 估算，POI 缺统一来源元数据，也没有按实际到达时间验证营业、库存、天气和全程距离。

截至 2026-08-30，M2 已完成可信规划收口。Planner 保持稳定的 `PlanningService.plan(...)` 接口与版本化 2/3/4 站显式骨架，并在真实路线逐段重建后复核营业、时间窗、指定时长、单段距离、餐时锚点、返程到家时间和独立全程距离；未归一化的返程截止时间会进入 Gate 澄清，不会被静默忽略。GeocodingProvider 将显式地点收敛为坐标、行政区、来源和解析状态；AvailabilityProvider 只批量检查 finalist，verified unavailable 进入 Verifier violation，unknown/stale/degraded 仅形成 warning。天气、路线、可用性和 Catalog warning 只在最终 Plan 使用相关事实时进入 CandidateSet/API；前端以最小提示显示。内部 Repair Contract 把可定位的 availability、营业、单段路线和返程超时失败统一调度为本地替换、下一个 finalist 或关闭当前链：每条 root chain 最多 2 轮，预算显式可见，单链耗尽不会结束整个请求；天气继续作为组合前剪枝。LLM 规划启发仍是目标能力，不得描述为当前能力。

M2 不一次重写 Planner，而是让每片都增加一条用户可观察、可测试、可降级的可信能力。共同不变量：LLM 不决定或放宽硬约束；单候选失败叫 `violation`，全部淘汰才叫 `conflict`；偏好损失才叫 `tradeoff`；数据不确定或降级叫 `warning`。

## 已确认的多站规划设计

外部 seam 保持 `PlanningService.plan(NormalizedConstraints) -> CandidateSet`。骨架生成、候选组合、穷举/Beam、分层评分和多样化都属于该深模块的内部实现；Catalog、Route、Weather 等真实外部依赖继续通过可替换 Adapter 注入。

| 阶段 | 结构搜索 | LLM 参与 | 状态 |
| --- | --- | --- | --- |
| 当前代码 | 四个版本化显式 `PlanSkeleton`；2/3/4 站角色池有界后完整枚举；Route Leg 预算在骨架间公平分配 | RouterExtractor 需求提取；规划内由确定性规则构造 `PlanningIntent`，LLM 不参与 | D1-D4/E 已完成首版 |
| M2 基础展示 | 模板 Presenter 只读取已验证 Plan/约束；地图与时间线通过同一 RouteLeg 索引互相定位 | 可选 LLM 仅能证据化润色，仍不作为可信基线前置条件 | F 基础展示、Vitest/RTL 与离线桌面/375px Playwright 已完成 |
| 目标形态 | 有限角色状态机/规划语法，含必选/可选角色、先后关系、站数范围和 `FINISH`；Beam 仅在指标证明必要时替换组合器 | 可选 LLM PlanningIntent、语义评分、已验证候选 PlanCritic、有界修复和 Presenter | M2 基线稳定后演进 |

核心术语：`StopRole` 是活动、午餐、晚餐、休息等行程作用，不等于餐厅、咖啡馆等资源类别；`PlanSkeleton` 不含具体 POI，也不等于 `PlanStrategy`。时长只参与骨架资格与先验，不硬分配唯一站数；只有带强约束语义或经确认的站数、角色和先后关系才成为硬条件，其余用户表达保留为偏好。合法骨架再按需求覆盖、时间锚点、节奏、时间利用、缓冲和候选可得性计算结构分，并保留多个骨架到具体 POI 和路线阶段。

评分分为单站角色分、部分行程启发分、完整方案分和候选集合多样化四层。Hard Constraint 在每层独立剪枝，不能用负分代替；真实路线返回后必须重新计算完整方案分，不同站数不能靠累加单站分天然获胜。最终三个结果来自可行候选的集合级多样化，不是机械取相似的数值 Top-3。

LLM 可以实质影响可行候选之间的选择，但不拥有可行性裁决权：它可提出带证据的角色覆盖、先后关系、站数范围、节奏与主题，也可评价“有设计感”“松弛”“适合聊天”等语义匹配；确定性代码仍负责 Constraint Strength、骨架合法性、路线、时间、预算、营业、库存和 Verifier。所有可选 LLM 步骤必须结构化、版本化、有确定性回退，并通过 eval 证明相对规则基线的收益。

## A. 天气 Provider 与雨天可行性

**实现状态：** 已完成；下文的“切片”表述保留实施历史，不再表示 M2 未完成。

**用户故事：** 北京雨天亲子半日规划仍能离线重复运行；户外敏感活动不进入结果，室内候选保留，用户能看到天气事实来自哪里以及是否降级。

**接口与契约：** `WeatherProvider.get_weather(WeatherRequest) -> WeatherFact`。`WeatherFact` 公开 `source`、`mode`、`observed_at`、`verified_at`、`cache_age_seconds`、`degraded` 和 `degraded_reason`；运行模式与事实来源正交。`CandidateSet.provider_facts` 把规划实际消费的天气事实带到 HTTP/UI。

**验收：** `mock/replay` 无网络可运行；雨天淘汰 `weather_sensitive=true` 候选；室内候选可形成方案；live/record 缺 Key 时明确拒绝启动；实时失败可用新鲜/陈旧缓存、replay 或 mock 降级且不冒充实时。

**测试：** Provider contract；mock/record/replay 观测一致性；成功 TTL；失败短缓存；高德字段映射与密钥不出现在领域输出；雨天规划行为；HTTP 来源字段；前端生产构建。

**后续验证：** 2026-08-27 已用本机 Key 验证一条真实天气响应；该样本只证明 live 接线可用，不证明预报长期准确或降级链覆盖所有线上故障。

## B. Route Provider 与完整双站时间线

**实现状态：** 已完成本切片；finalist 路线复核支持超时与单段超距淘汰，D4 已补齐全程累计距离 Verifier。

**用户故事：** 用户看到的到达时间、停留时间和结束时间包含每一段路线；路线服务失败时仍能规划，但每段明确标为估算及原因。

**接口与契约：** `RouteProvider.route(RouteRequest) -> RouteFact`；`RouteLeg` 保存 origin/destination、mode、distance、duration、geometry、source、verified_at、degraded_reason。组合阶段只用本地估算，finalist 才调用 Provider，返回后重建时间线。

**验收：** 路线复核后所有 Stop 的 arrival/start/end 重新计算；若复核导致超时或超距则淘汰/局部替换；高德失败按缓存 → 本地估算降级并公开。

**测试：** Provider contract、TTL、失败短缓存、只调用 finalist、复核后时间线重建、超时淘汰、API/UI 路线来源；2026-08-27 已额外完成真实 Key 路线自检与完整 UI 双站规划联调。

**不做：** 不建立全量路线矩阵，不引入 Beam Search；地图由既有 `RouteLeg.geometry` 绘制，不再次向路线服务请求数据。

## C. Catalog 与单资源硬约束剪枝

**实现状态：** C1-C3 已完成：Catalog seam、资源级剪枝、200 条可离线 replay 的北京 OSM POI、Unknown/Warning 语义、Fixture 隔离、坐标归一化和 HTTP/UI 图片降级均已实现。动态 POI、可靠实时价格和现场核验不属于 M2 范围。

**用户故事：** 进入组合器的每个 POI 已经在日期、人数、儿童年龄、预算、基础营业信息和召回距离下界上具备单资源可行性，并能追溯数据来源。

**接口与契约：** 通用 `StopCandidate`；POI 元数据包含 `source_uri/source_name/source_license`、`collected_at`、`last_verified_at`、`verification_status`、坐标系、`PriceKind` 与可选 `ImageRef`。`SnapshotCatalog` 校验并归一化生成快照，Catalog 召回后返回候选、结构化 `ConstraintViolation[]` 和非阻断 `CatalogWarning[]`。

**验收：** 36 个真实北京 POI 基础字段可追溯；动态价格/库存明确仍未核验；不适龄、不容纳人数、单资源已超预算、价格证据不足或当天不营业的资源不进入严格组合。图片缺失/失败不影响规划。

**测试：** 元数据 schema、重复 ID、坐标/营业时间/图片许可、日期/人数/年龄/预算证据/直线距离下界、来源缺失拒绝、剪枝原因聚合、离线 Planning/API 和前端生产构建。

**不做：** 不抓取未确认许可的数据，不宣称动态库存真实，不做大规模语义检索。

## D. 2/3/4 站组合与完整 Verifier

**实现状态：** D0-D4 已完成。通用组合器按 `PlanSkeleton.roles` 填充四个 2/3/4 站显式骨架；多站的单角色候选先用语义、场景、饮食、避开项、预算和出发地距离确定性预排序到最多 8 个，再完整枚举。路线重建后核验每个 Stop 的实际停留营业边界、同日多营业区间、时间窗、指定时长和单段距离，并新增餐时锚点（`LUNCH` 到店 11:00-14:00、`DINNER` 到店 17:00-20:30）、`return_by` 返程到家时间和独立 `total_distance_km` 全程距离校验；外部复核按最多 24 个 Route Leg 计费并在骨架间轮询，避免高分长骨架耗尽预算。Mock/Replay Availability 与天气/路线降级 warning 已被最终 CandidateSet 聚合；它们不宣称实时库存或完整气象模型。

**用户故事：** 短时、半日和一日方案在实际到达时间、营业、天气、预算、距离、路线时间和时间冲突上全部可验证。

**接口与契约：** `PlanVerifier.verify(plan, constraints, facts) -> VerificationResult`；结果只含 `violations` 与 `warnings`。可行 Plan 另由评分模块产生 `tradeoffs`；所有候选淘汰后由聚合器产生 `ConstraintConflict`。

**实施顺序与当前状态：**

1. **D1 通用序列内核——已完成：** 活动 × 餐厅专用组合已改为按 `PlanSkeleton.roles` 填充的有界序列组合，双站回归保持通过。
2. **D2 骨架候选——已完成首版：** 已加入 `StopRole`、`PlanSkeleton`、确定性 `PlanningIntent`、骨架资格与结构评分；同一长时请求可保留 2/3/4 站骨架。
3. **D3 三/四站——已完成首版：** `LUNCH -> ACTIVITY -> DINNER`、`ACTIVITY -> BREAK -> DINNER` 与 `ACTIVITY -> LUNCH -> ACTIVITY -> DINNER` 已打通；更丰富顺序由后续状态机/规划语法演进，餐时锚点属于 D4。
4. **D4 全程语义——已完成：** 总预算按所有 Stop 汇总；Route Provider 按 24 个 Route Leg 统一预算并在骨架间公平取样。`LUNCH/DINNER` 到店被锚定到版本化用餐窗口；表达 `return_by` 时追加一段以“出发地”为终点的返程，并校验其存在、终点和到家边界；`total_distance_km` 独立于单段 `max_distance_km` 校验全程累计距离。Demo Graph 的“最晚十八点回家”可在同会话以 `18:00` 恢复，无法归一化的全程距离或非法返程时钟会在 Gate 阻断。

M2 对最多 4 站优先完整枚举与早期剪枝。当前多站角色池上限为 8：四站且两个活动、两个餐饮角色时，去除同资源重复前最多 `8^4` 个组合，实际唯一排列最多约 3,136 个；不会对 100×100×99×99 的完整 Catalog 直接展开。只有评测显示这一有界枚举仍在组合数量或延迟上超出门槛后才引入 Beam Search；届时部分状态至少包含当前位置、当前时间、已覆盖/剩余必选角色、预算、缓冲和 `FINISH`，不能以“时间未满就继续加站”为停止规则。

**验收：** 支持合法 2/3/4 核心停靠点；到达恰好开门可行、到达/停留越过关门按正式规则淘汰；预算和距离按全程汇总；硬约束绝不因高分保留；无解给结构化放宽建议。

**测试：** 营业边界、路线导致到达变化、全程预算/距离、时间重叠、天气与库存、无解与放宽、用户隔离回归。

**不做：** 未批准 `IDEA-007` 前，不给所有时间字段统一追加 hard/soft，也不发明正负半小时容差。

## E. 多策略与真实多样性

**实现状态：** 已完成首版。先复核不超过 24 个 Route Leg 的可行候选池，再按 POI 重叠（Jaccard）、总成本和总路程三个可观察指标贪心选出最多 3 个差异方案；不足 3 个差异方案时如实返回较少结果。六种固定策略（`balanced/low_cost/low_travel/experience/family_safe/weather_safe`）按用户偏好、儿童和不利天气启用并写入结构化 `strategy` 评分证据；价格不完整的 Plan 记录 `cost=uncomparable`，不参与 `low_cost`，其余策略仅把该软维度中性化。策略只重排已通过 Verifier 的候选，不能放宽硬约束。LLM PlanCritic 语义重排仍未实现。

**用户故事：** 用户得到三个都可行、但在可观察指标上真正不同的方案，而不是相同 POI 换标题。

**接口与契约：** 策略固定为 `balanced`、`low_cost`、`low_travel`、`experience`、`family_safe`、`weather_safe`；评分维度固定，策略只改变权重。多样化以 POI 重叠、成本、路程、类别和策略为可观察指标。

**验收：** 三方案均通过 Verifier；至少一个指标达到预设差异门槛；不足三个可行差异方案时如实返回较少结果或冲突说明，不复制方案凑数。

**测试：** 权重与硬约束正交、重复指纹、多样性阈值、候选不足、策略证据。

**不做：** M2 不让 LLM 自由生成 POI/时间线或改变 Verifier 结论，不在没有基线 eval 时接入 PlanCritic，不为算法名提前上 Beam Search，不用标题差异代替内容差异。目标架构中的 LLM 语义重排在确定性多站基线稳定后按 Roadmap 演进。

## F. Presenter、地图时间线与评测

**实现状态：** F 的可信展示已完成基础版：高德 JS API 使用后端公开配置与安全代理，前端直接绘制 `RouteLeg.geometry` 和 N 站 marker；未配置或加载失败时保留文字摘要。模板 Presenter 只总结已验证的方案事实和活跃约束；时间线显式展示“出发地→首站”，中间段与返程各只显示一次，地图、路线列表和时间线共用 RouteLeg index。Vitest/RTL 覆盖方案切换、首/中/返程联动、地图降级/错误与来源；离线 Playwright 已实际通过桌面 Chromium 与 375px 主路径，不依赖真实高德或 LLM。2026-08-27 浏览器验收也已确认真实底图、路线折线和“起点 + 两个停靠点”共 3 个 marker，控制台无错误。

**用户故事：** 用户能在桌面和移动端理解三个方案、天气/路线来源和降级状态，并用地图与时间线核对同一事实。

**接口与契约：** 模板 Presenter 是必选默认；可选 LLM 只能根据已存在的 Plan、评分、facts、warnings、tradeoffs 和 assumptions 解释站数、顺序、地点与方案差异。前端消费结构化字段，不解析自由文本来判断可行性。

**验收：** 无 LLM 时完整展示；Provider 来源与降级可见；地图 marker 和时间线联动；smoke/eval 断言硬约束与事实来源；组件测试与 Playwright 桌面/移动主路径齐备。上述基础验收已具备；仍需扩大场景覆盖，而非把当前 mock/offline 结果外推为真实地图或 LLM 的长期质量。

**测试：** 模板快照/结构断言、Provider 状态组件、方案切换、地图时间线联动、Playwright 主路径和离线 eval。

**不做：** 不把 build 或单元测试称为 E2E，不把底层隐式推理暴露给 UI，不在 M2 顺手补 M1 刷新恢复。

## 与 M2.5、M3 和 M3.5 的边界

2026-08-28 竞品评审后，canonical 路线图已增加 M2.5 产品呈现与 M3.5 可控记忆。M2 已完成 D4 餐时/返程/独立全程距离、E 集合级多样化和 F 的 eval/地图时间线基础验收；后续按路线图进入 M2.5，而不倒改其可信边界。

- M2 可以完成模板 Presenter 和地图时间线的可信事实展示；不在此阶段重做完整视觉层级、POI 详情抽屉和管家式对话。
- M2.5 只消费 M2 已稳定的 Plan、RouteLeg、facts、warnings 和 score breakdown，不修改 `PlanningService.plan(...)` 的可行性语义。
- M3 才实现约束 Patch、锁定、PlanDiff、Plan Version、局部重规划和撤销。
- M3.5 才实现 `MemoryService`、本人/家庭 scope、反馈写入、记忆控制和 `MemoryInfluence`；M2 当前的 `ConstraintSource.MEMORY` 只是契约预留，不代表长期记忆已实现。
- LLM PlanningIntent、PlanCritic 和记忆候选提取仍是可选增强，必须在确定性基线和对应 eval 后进入，不能成为上述切片的前置条件。

## Inbox 分析与相邻已晋升需求

### IDEA-007：时间语义、来源与强度

**建议用户故事：** 用户说“下午”“晚上吃饭”或“最晚 18:00 到家”时，系统保留原始证据，并区分可交易的期望时段与不可违反的截止时间。

**建议契约：** 新增 `ConstraintStrength = hard | preference`，与 `ConstraintSource` 正交；Router 只抽取原文、语义类型和证据，确定性规则根据明确措辞与已确认值赋 strength。建议将 `availability_window`、`meal_window`、`return_by` 分开建模，不把一个 `TimeWindow` 同时承担所有语义。

**建议验收：** “最晚 18:00 到家”违反时淘汰；系统默认下午窗口偏离时形成 tradeoff；“晚上吃饭”按版本化产品规则归一化；任何放宽都需要用户确认；Presenter 不得更改 strength。

**建议测试：** source × strength 参数化、明确截止边界、默认窗口 tradeoff、用餐衔接、冲突放宽、Router 证据保留。

**明确不做：** 不采用通用正负半小时；不把所有用户明确表达自动设为 hard；不在本切片实现。需用户批准 `IDEA-007` 后才能进入 D 或 M3。

### 其他相关想法

- `IDEA-002`：与 F 的 Provider/Router 能力徽标相邻，适合在 F 统一设计；A 只显示本次天气事实，不实现全局运行模式徽标。
- `IDEA-004`：已晋升到 M2.5；可在该切片中改约束摘要，但不得改变 M2 的约束语义。
- `IDEA-005`：会影响 C/D 的人数与儿童剪枝，但 `minimum_people` 与 `exact_people` 尚无验收标准，不能自动实现。
- `IDEA-003`：不实现前端 Key 配置；所有 Key 仅来自后端环境。

## Git 与实施边界

- 历史 M2 起点是 `a99dc459ed038a2a67c9ca49160cb24196c222b2`；C3 已形成提交 `0757352`。
- 当前实现分支为 `codex/m2-s0-native-planning`；本状态文档描述的是该分支当前工作树，不应再按旧的 `feature/m2-trustworthy-planning` 路径寻找代码。
- 每片仍按行为测试 → 最小实现 → 全量回归 → 文档推进；未经用户要求不自动推送、合并或清理工作树。

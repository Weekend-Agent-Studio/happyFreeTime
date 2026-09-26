# Mock Design: Weekend Planning Agent

_归档状态：V1 Mock 设计记录 · 已被 V2 架构基线替代_

---

> ⚠️ **历史文档：** 仅用于理解早期 Mock 能力和设计演进，不得作为当前实现契约。当前依据见 [`../../canonical/architecture_v2.md`](../../canonical/architecture_v2.md)。

## 1. 目标

本项目的 Mock 层用于模拟一个本地生活平台的核心能力，让周末规划 Agent 可以像调用真实服务一样完成：

- 理解用户约束后的资源检索
- 活动、餐厅、商品的可行性校验
- 多候选方案的评分和排序
- 预约、订票、下单等交易动作的模拟执行
- 无票、满座、天气不适合、距离过远等异常情况的兜底

Mock 层不是简单返回固定答案，而是一个可替换、可验证、结果稳定的本地服务层。后续如果接入真实地图、天气或本地生活开放平台 API，应尽量只替换数据源或 service 实现，而不改 Agent 的主体流程。

> 所以你可以把 Mock 层理解成：**模拟真实世界中 Agent 会依赖的外部环境和业务平台能力。**
>
> (Mock 层 = 假数据层 + 假服务层 + 工具包装层)
>
> *Mock 层是 Agent 工具后端的本地模拟实现，包括结构化假数据、确定性的业务服务和工具包装。后续接真实 API 或 MCP 时，优先替换 service/provider 实现，保持工具接口和 Agent 编排不变。*

## 2. 设计原则

1. **结构化数据优先**
   所有餐厅、活动、商品都应来自结构化数据文件，而不是由 LLM 临时编造。

2. **业务逻辑独立**
   搜索、评分、库存校验、营业时间判断等逻辑放在 service 层，不直接写进 Agent prompt。

3. **工具输出稳定**
   相同输入应尽量返回相同结果，方便调试、演示和自动测试。

4. **真实约束驱动**
   Mock 数据需要包含价格、距离、营业时间、排队风险、库存、适合人群、缺点等字段，让 Agent 有真实取舍。

5. **交易动作需确认**
   预约、订票、下单等执行类工具只能在用户明确确认后调用。

6. **可替换适配**
   对外暴露的工具接口应接近真实平台能力，例如搜索、详情、库存、路线、下单，而不是只服务某一个 Demo 文案。

## 3. 推荐目录结构

```text
data/
  activities.json
  restaurants.json
  products.json
  test_cases.json

services/
  catalog_service.py
  feasibility_service.py
  scoring_service.py
  planning_service.py
  order_service.py

tools/
  search_tools.py
  check_tools.py
  planning_tools.py
  order_tools.py

Agents/
  intent_agent.py
  slot_agent.py
  planning_agent.py
```

职责划分：

- `data/`：只保存 Mock 数据，不写逻辑。
- `services/`：实现确定性的业务逻辑。
- `tools/`：把 service 包装成 LangChain/LangGraph 可调用工具。
- `Agents/`：负责意图理解、槽位补全、工具选择、自然语言解释和用户确认。

如果前期想简化，可以先不拆 `tools/` 目录，直接在现有 `Tools.py` 中包装 service。但数据仍建议尽早从工具函数里拆出去。

## 4. 核心 Demo 场景（用于测试

### 4.1 家庭亲子

用户示例：

> 今天下午想带老婆孩子出去玩 4 小时，别太远，老婆最近减肥，孩子 5 岁。

关键约束：

- 人群：2 位成人 + 1 位儿童
- 儿童年龄：5 岁
- 偏好：亲子、轻松、不要太远
- 饮食：健康、低卡、儿童友好
- 时间：下午，约 4 小时
- 风险：避免长时间排队、避免户外天气影响

期望能力：

- 推荐亲子活动和健康餐厅
- 排除距离太远、雨天不适合、儿童年龄不匹配的活动
- 给出 2-3 个方案并解释取舍
- 用户确认后可模拟订票和订餐位

### 4.2 朋友聚会

用户示例：

> 周六晚上 4 个朋友想聚一下，预算人均 150 左右，最好能吃饭再玩点轻松的。

关键约束：

- 人群：4 位成人
- 偏好：聚餐、娱乐、轻松
- 时间：周六晚上
- 预算：人均约 150
- 执行：可预约餐厅或娱乐项目

期望能力：

- 推荐餐厅 + 桌游/密室/Livehouse/运动娱乐等组合
- 校验餐厅桌位、活动库存和营业时间
- 生成低预算、体验丰富、少奔波等不同方案

### 4.3 情侣约会

用户示例：

> 这周日想安排一个轻松点的约会，不想太累，吃饭环境好一点，最好有甜品。

关键约束：

- 人群：2 位成人
- 偏好：约会、轻松、环境好
- 餐饮：餐厅 + 甜品/咖啡
- 时间：周日
- 风险：避免嘈杂、避免高强度运动

期望能力：

- 推荐低体力消耗、氛围好的活动和餐饮
- 解释为什么不选某些高评分但不适合约会的选项
- 用户确认后可模拟订座和下单甜品

## 5. 数据模型

### 5.1 Activity

活动类数据覆盖亲子乐园、展览、公园、运动场馆、桌游、密室、citywalk 等。

```json
{
  "id": "act_001",
  "name": "星河亲子探索馆",
  "type": "activity",
  "category": ["亲子", "室内", "科普"],
  "district": "朝阳区",
  "address": "北京市朝阳区某某路 88 号",
  "lat": 39.92,
  "lng": 116.48,
  "avg_price": 88,
  "rating": 4.6,
  "review_count": 1240,
  "duration_minutes": 90,
  "open_hours": {
    "sat": "10:00-20:00",
    "sun": "10:00-20:00"
  },
  "suitable_for": ["family", "children_3_8", "rainy_day"],
  "not_suitable_for": ["quiet_date"],
  "tags": ["亲子友好", "室内", "低体力消耗", "可预约"],
  "weather_sensitive": false,
  "queue_risk": "medium",
  "booking_required": true,
  "availability": [
    {
      "date": "2026-05-23",
      "time": "14:30",
      "status": "available",
      "slots": 12
    }
  ],
  "packages": [
    {
      "id": "pkg_act_001_family",
      "name": "亲子双人票",
      "price": 158,
      "includes": ["1 adult", "1 child"]
    }
  ],
  "review_summary": {
    "pros": ["互动项目多", "适合 5 岁左右孩子", "室内不受天气影响"],
    "cons": ["周末下午人较多", "停车紧张"]
  }
}
```

### 5.2 Restaurant

餐厅数据需要支持饮食偏好、人数、桌位、儿童友好和预约能力。

```json
{
  "id": "rest_001",
  "name": "青禾轻食餐厅",
  "type": "restaurant",
  "category": ["轻食", "西餐", "健康餐"],
  "district": "朝阳区",
  "address": "北京市朝阳区某某商场 5 层",
  "lat": 39.921,
  "lng": 116.482,
  "avg_price": 72,
  "rating": 4.7,
  "review_count": 860,
  "open_hours": {
    "sat": "10:30-21:30",
    "sun": "10:30-21:30"
  },
  "diet_tags": ["低卡", "高蛋白", "儿童餐", "少油"],
  "scene_tags": ["家庭", "朋友聚餐", "安静"],
  "party_size_supported": [2, 3, 4, 5, 6],
  "has_private_room": false,
  "has_child_chair": true,
  "reservation_required": true,
  "available_tables": [
    {
      "date": "2026-05-23",
      "time": "16:30",
      "party_size": 3,
      "status": "available"
    }
  ],
  "signature_items": ["鸡胸肉能量碗", "儿童番茄意面", "低脂酸奶杯"],
  "review_summary": {
    "pros": ["沙拉选择多", "有儿童餐", "距离商场活动区近"],
    "cons": ["饭点出餐慢"]
  }
}
```

### 5.3 Product

商品类用于模拟鲜花、蛋糕、甜品、小吃、伴手礼等增量消费。

```json
{
  "id": "prod_001",
  "name": "莓果低糖蛋糕",
  "type": "product",
  "category": "蛋糕",
  "vendor_name": "幸福西饼",
  "district": "朝阳区",
  "address": "北京市朝阳区某某路 18 号",
  "avg_price": 168,
  "rating": 4.6,
  "tags": ["低糖", "可配送", "适合家庭"],
  "delivery_available": true,
  "pickup_available": true,
  "stock": 8,
  "delivery_slots": [
    {
      "date": "2026-05-23",
      "time": "18:00-19:00",
      "status": "available"
    }
  ],
  "review_summary": {
    "pros": ["甜度低", "适合孩子", "配送稳定"],
    "cons": ["热门时段需要提前下单"]
  }
}
```

### 5.4 Booking / Order Result

执行类工具返回统一结果结构，方便 UI 展示。

```json
{
  "success": true,
  "action_type": "book_tickets",
  "confirmation_id": "TK20260523-0001",
  "target_id": "act_001",
  "target_name": "星河亲子探索馆",
  "scheduled_time": "2026-05-23 14:30",
  "party_size": 3,
  "total_price": 246,
  "message": "订票成功，请在 14:20 前到场核验。"
}
```

失败结果示例：

```json
{
  "success": false,
  "action_type": "reserve_restaurant",
  "target_id": "rest_002",
  "target_name": "花园西餐厅",
  "error_code": "NO_TABLE",
  "message": "16:30 已无 4 人桌，可选择 17:00 或更换餐厅。",
  "alternatives": [
    {
      "time": "17:00",
      "status": "available"
    }
  ]
}
```

## 6. 服务与工具设计

### 6.1 搜索类

搜索类工具负责从数据中召回候选，不负责最终方案生成。

```text
search_activities(tags, location, radius_km, time_window, party_profile)
search_restaurants(diet_tags, scene_tags, location, radius_km, party_size, time_window)
search_products(category, tags, location, delivery_time)
get_resource_detail(resource_id)
```

输出建议包含：

- 基本信息
- 匹配到的标签
- 初步过滤原因
- 后续需要校验的事项

### 6.2 可行性校验类

可行性工具负责判断候选是否能被安排进计划。

```text
check_open_hours(resource_id, arrival_time)
check_availability(resource_id, date, time, party_size)
estimate_route(origin, destination, mode)
estimate_queue_time(resource_id, date, time)
check_weather_suitability(activity_id, weather)
```

前期 `estimate_route` 可以用经纬度近似距离 + 固定速度模拟：

- 步行：5 km/h
- 骑行：12 km/h
- 打车：25 km/h

后续如有时间，可替换成地图 API。

### 6.3 评分类

评分服务应返回分数和解释，而不是只返回排序。

```text
score_activity(activity, constraints)
score_restaurant(restaurant, constraints)
score_product(product, constraints)
score_plan(plan, constraints)
```

建议初版使用规则评分，总分 100：

```text
人群匹配：20
距离匹配：15
预算匹配：15
时间/营业状态：15
库存/可预约：15
天气适配：10
评分/口碑：5
排队风险：5
```

扣分原因也要返回，例如：

```json
{
  "score": 84,
  "reasons": ["适合 5 岁儿童", "室内不受天气影响", "距离 3.2km"],
  "penalties": ["周末下午排队风险中等"]
}
```

### 6.4 方案生成类

方案生成服务将活动、餐厅、路线组合成完整 itinerary。

```text
generate_candidate_plans(constraints, activity_candidates, restaurant_candidates)
validate_plan_timeline(plan)
compare_plans(plans)
```

计划结构示例：

```json
{
  "plan_id": "plan_family_safe_001",
  "title": "稳妥亲子室内方案",
  "plan_type": "safe",
  "total_score": 88,
  "total_price": 318,
  "total_duration_minutes": 230,
  "items": [
    {
      "start": "14:00",
      "end": "14:25",
      "type": "route",
      "title": "从当前位置打车前往星河亲子探索馆",
      "duration_minutes": 25
    },
    {
      "start": "14:30",
      "end": "16:00",
      "type": "activity",
      "resource_id": "act_001",
      "title": "星河亲子探索馆"
    },
    {
      "start": "16:30",
      "end": "17:30",
      "type": "restaurant",
      "resource_id": "rest_001",
      "title": "青禾轻食餐厅"
    }
  ],
  "highlights": ["室内亲子", "低卡餐厅", "总路程短"],
  "tradeoffs": ["亲子馆周末下午可能人较多"],
  "required_confirmations": [
    {
      "action": "book_tickets",
      "resource_id": "act_001"
    },
    {
      "action": "reserve_restaurant",
      "resource_id": "rest_001"
    }
  ]
}
```

初版建议至少生成三类方案：

- `safe`：稳妥、省心、少奔波
- `budget`：价格更低
- `rich`：体验更丰富，但可能距离或价格稍高

### 6.5 执行类

执行类工具模拟真实交易动作。

```text
book_tickets(activity_id, package_id, date, time, count)
reserve_restaurant(restaurant_id, date, time, party_size, requirements)
place_order(product_id, quantity, delivery_address, delivery_time)
cancel_booking(confirmation_id)
```

注意：

- 这些工具不应由 Agent 在未确认时自动调用。
- 执行后应返回订单号、价格、时间、失败原因或可替代选项。
- 可以在内存中模拟库存减少；如果项目暂时不做持久化，也要在文档里说明。

## 7. 失败与兜底场景

Mock 数据中需要刻意设计一些冲突，展示 Agent 的规划能力。

建议至少覆盖：

1. 活动评分高但距离超出用户要求。
2. 户外活动免费但雨天不推荐。
3. 餐厅健康但目标时间无桌，临近时间有桌。
4. 活动适合亲子但儿童年龄不匹配。
5. 餐厅评分高但没有儿童椅。
6. 方案体验丰富但超预算。
7. 商品可配送但热门时间段库存不足。
8. 预约动作失败后给出替代时间或替代商家。

这些冲突能让最终回答更像产品，而不是列表推荐。

## 8. 第一阶段数据规模

第一阶段不要急着做 40-50 条。建议先做 10 条高质量数据跑通完整链路：

```text
Activity: 4 条
- 室内亲子活动 1 条
- 户外公园/骑行 1 条
- 情侣/展览类 1 条
- 朋友聚会娱乐 1 条

Restaurant: 4 条
- 健康轻食 1 条
- 家庭友好餐厅 1 条
- 朋友聚餐餐厅 1 条
- 情侣约会餐厅 1 条

Product: 2 条
- 低糖蛋糕/甜品 1 条
- 鲜花/伴手礼 1 条
```

第二阶段扩充到 40 条左右：

```text
亲子活动：8 条
朋友聚会活动：8 条
情侣/轻松约会活动：6 条
轻食/健康餐厅：8 条
普通聚餐餐厅：8 条
甜品/咖啡/小吃：5 条
公园/citywalk/展览：5 条
```

## 9. 测试用例

测试用例用于验证工具链路，不用于让模型背答案。

```json
{
  "case_id": "family_weekend_001",
  "user_input": "今天下午想带老婆孩子出去玩 4 小时，别太远，老婆最近减肥，孩子 5 岁",
  "expected_constraints": ["亲子", "健康饮食", "4小时", "距离近"],
  "expected_tools": [
    "search_activities",
    "search_restaurants",
    "estimate_route",
    "check_availability"
  ],
  "success_criteria": [
    "包含适合 5 岁儿童的活动",
    "包含健康或低卡餐厅",
    "总时长不超过 4 小时",
    "解释至少一个未选择候选的原因",
    "下单或预约前请求用户确认"
  ]
}
```

建议准备 5-8 条：

- 亲子半日
- 朋友周六晚上聚会
- 情侣周日约会
- 雨天户外需求改室内
- 预算很低
- 指定活动无票
- 指定餐厅无桌
- 用户修改已有计划

## 10. 与 Agent 的交互边界

Agent 应该负责：

- 理解用户原始表达
- 抽取和补全约束
- 决定调用哪些工具
- 组织候选方案
- 用自然语言解释方案和取舍
- 向用户确认执行动作

Mock service 应该负责：

- 读取数据
- 检索候选
- 校验营业时间和库存
- 估算路线和排队
- 计算分数
- 执行模拟订单

不建议让 LLM 自己决定某个商家是否有座、价格是多少、离用户多远。这些应来自 Mock 工具。

## 11. 推荐实现顺序

1. 定义 `Activity`、`Restaurant`、`Product`、`BookingResult` 数据结构。
2. 准备第一批 10 条 Mock 数据。
3. 实现数据加载和详情查询。
4. 实现活动、餐厅、商品搜索。
5. 实现营业时间、库存、路线、天气适配校验。
6. 实现评分函数，并返回原因和扣分项。
7. 实现候选方案组合和时间线校验。
8. 实现预约、订票、下单的 Mock 执行。
9. 将 service 包装成 Agent 工具。
10. 用 5-8 个测试用例验证链路。
11. 扩充数据到 40 条左右。

## 12. 仍需确认的问题

下面这些问题会影响 Mock 数据和工具接口，建议在真正写代码前确定：

1. **城市范围**
   Demo 是固定北京朝阳区，还是允许用户选择城市？

2. **真实 API 接入范围**
   第一版是否接天气或地图路线 API？如果不接，全部使用 Mock 是否可接受？

3. **前端展示重点**
   UI 更强调聊天体验，还是强调工具调用轨迹和方案对比？

4. **交易闭环深度**
   下单/预约只返回成功结果，还是需要模拟库存减少、取消订单、订单状态查询？

5. **用户画像**
   是否需要保存用户偏好，例如常住位置、预算、饮食偏好、是否有孩子？

6. **时间处理**
   是否需要支持“今天/明天/这周六”这类相对时间的确定性解析？如果需要，建议统一由 slot agent 或时间 service 处理。

7. **评分是否可解释展示**
   UI 是否展示每个方案的评分明细？如果展示，评分服务需要输出更结构化的原因。

## 13. 验收标准

第一阶段完成后，至少应能做到：

- QA输入一个自然语言需求，系统能提取约束。
- 工具能从 Mock 数据中找到候选活动和餐厅。
- 系统能过滤明显不合适的候选。
- 系统能生成 2-3 个带时间线的方案。
- 每个方案有推荐理由和取舍说明。
- 用户确认前不会执行预约或下单。
- 执行类工具能返回成功或失败结果。
- 至少 5 个测试用例可以稳定跑通。

达到这些后，再扩数据、做 UI、接真实 API，收益会更高。

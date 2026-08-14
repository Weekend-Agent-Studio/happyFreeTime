# Weekend Planning Agent Migration Notes

_归档状态：Hackathon 阶段迁移记录 · 不描述当前 V2 实现_

---

> ⚠️ **历史文档：** 仅用于追溯项目起点。当前能力见根目录 `README.md`，当前目标设计见 [`../../canonical/architecture_v2.md`](../../canonical/architecture_v2.md)。

Source thread: "分析周末规划智能体方案"

Migrated on: 2026-05-19

## Project Positioning

Build a local-life weekend planning AI agent for a hackathon AI track. The product should help users describe a loose weekend need, then produce a feasible plan and optionally execute mock booking or ordering actions after explicit confirmation.

Recommended positioning:

> A local-life AI Agent prototype that supports natural-language requirement parsing, structured constraint extraction, multi-tool planning, candidate itinerary generation, user confirmation, mock reservation/order execution, and fallback handling.

Avoid positioning the project as a crawler or as a system using unauthorized Meituan/Dianping production data.

## Core Principle

Do not build the project on unofficial scraping of Meituan or Dianping.

Better approach:

1. Use high-quality mock commerce data for merchants, activities, inventory, queue risk, packages, bookings, and orders.
2. Use legal public/open APIs only where appropriate, such as weather, map POI, geocoding, or route estimation.
3. Design the data and tools as replaceable adapters so future real platform APIs can be plugged in.
4. Keep all transactional actions behind user confirmation.

## Core User Scenarios

Start with three scenes:

1. Family outing: parent, spouse, child, time-limited, child-friendly, healthy food.
2. Friends gathering: casual entertainment, food, budget, travel distance.
3. Couple date: relaxed activity, restaurant or dessert, mood and time fit.

Example input:

> 今天下午想带老婆孩子出去玩 4 小时，别太远，老婆最近减肥，孩子 5 岁。

Expected behavior:

1. Extract constraints: family, child age, healthy diet, afternoon, 4 hours, nearby.
2. Search suitable activities and restaurants.
3. Check weather, opening hours, availability, route time, and queue risk.
4. Generate 2-3 candidate plans with tradeoffs.
5. Ask for confirmation before booking tickets, reserving seats, or placing an order.

## Suggested Architecture

```text
User
  -> Intent Agent
  -> Slot/Constraint Agent
  -> Planning Agent
  -> Tool Layer
       -> Time/Location/Weather
       -> Place/Activity/Restaurant Search
       -> Feasibility Checks
       -> Route/Queue/Availability
       -> Mock Booking/Ordering
  -> Plan Explanation + Confirmation
  -> Execution Result
```

The current repository already has the beginning of this shape:

- `main.py`: demo entrypoint for intent classification.
- `Agents/intent_agent.py`: intent recognition graph.
- `Agents/slot_agent.py`: slot completion graph with tool calling.
- `Agents/graph.py`: unfinished graph wiring.
- `Tools.py`: mock time/location/weather/search/booking/order tools.

Important current issue: several Python files contain mojibake Chinese text, likely caused by an encoding mismatch. Some strings also appear syntactically broken. Fixing encoding and syntax should be the first engineering cleanup before feature work.

## Tool API Categories

### User Understanding

```text
parse_user_intent(text)
extract_constraints(text)
update_user_preference(user_id, preference)
```

Example constraint output:

```json
{
  "date": "2026-05-16",
  "time_window": "14:00-18:00",
  "group": {
    "adults": 2,
    "children": 1,
    "child_age": 5
  },
  "goals": ["family", "relax", "healthy_food"],
  "constraints": {
    "max_distance_km": 8,
    "budget_per_person": 120,
    "avoid": ["long_queue", "high_calorie"]
  }
}
```

### Local Resource Search

```text
search_places(query, location, tags, radius_km)
search_activities(tags, time_window, group_profile)
search_restaurants(cuisine, diet_tags, party_size, time)
get_place_detail(place_id)
```

### Feasibility Checks

```text
check_open_hours(place_id, arrival_time)
check_availability(place_id, time, party_size)
estimate_route(origin, destination, mode)
estimate_queue_time(place_id, time)
```

### Plan Generation And Evaluation

```text
score_plan(plan, constraints)
compare_plans(plans)
explain_recommendation(plan)
```

These can live inside the backend rather than being exposed as external tools.

### Execution Actions

```text
create_booking(place_id, time, party_size)
create_order(item_id, quantity)
reserve_coupon(coupon_id)
send_itinerary(contact, plan)
create_calendar_event(plan)
cancel_booking(booking_id)
```

All execution actions must require explicit user confirmation.

## Data Strategy

Do not make the 30-50 records into question-answer samples. The main dataset should be structured local-life resources:

- Restaurants
- Parent-child activities
- Exhibitions
- Parks
- Dessert or coffee shops
- Citywalk routes
- Board games, escape rooms, sports venues

Small QA/test cases are still useful, but only as evaluation cases.

Suggested distribution:

```text
Parent-child activities: 8
Friends gathering activities: 8
Couple/date activities: 6
Light/healthy restaurants: 8
General restaurants: 8
Dessert/coffee/snacks: 5
Parks/citywalk/exhibitions: 5
```

40-48 high-quality records are enough for a strong demo.

## Place Schema

```json
{
  "id": "place_001",
  "name": "星河亲子探索馆",
  "type": "activity",
  "category": ["亲子", "室内", "科普"],
  "address": "北京市朝阳区某某路 88 号",
  "district": "朝阳区",
  "lat": 39.92,
  "lng": 116.48,
  "open_hours": {
    "sat": "10:00-20:00",
    "sun": "10:00-20:00"
  },
  "avg_price": 88,
  "rating": 4.6,
  "review_count": 1240,
  "duration_minutes": 90,
  "suitable_for": ["family", "children_3_8", "rainy_day"],
  "tags": ["亲子友好", "低体力消耗", "室内", "可预约"],
  "not_suitable_for": ["情侣安静约会"],
  "queue_risk": "medium",
  "booking_required": true,
  "availability": [
    {
      "time": "14:30",
      "status": "available",
      "slots": 12
    }
  ],
  "packages": [
    {
      "id": "pkg_001",
      "name": "亲子双人票",
      "price": 158
    }
  ],
  "review_summary": {
    "pros": ["互动项目多", "适合 5 岁左右孩子", "室内不受天气影响"],
    "cons": ["周末下午人较多", "停车紧张"]
  }
}
```

## Restaurant Schema

```json
{
  "id": "rest_001",
  "name": "青禾轻食餐厅",
  "type": "restaurant",
  "category": ["轻食", "西餐", "健康餐"],
  "avg_price": 72,
  "diet_tags": ["低卡", "高蛋白", "儿童餐", "少油"],
  "party_size_supported": [2, 3, 4, 5, 6],
  "has_private_room": false,
  "reservation_required": true,
  "available_tables": [
    {
      "time": "16:30",
      "party_size": 3,
      "status": "available"
    }
  ],
  "review_summary": {
    "pros": ["沙拉选择多", "有儿童餐", "距离商场近"],
    "cons": ["饭点出餐慢"]
  }
}
```

## Test Case Format

Use 5-8 evaluation cases to verify agent behavior rather than to train the model to memorize answers.

```json
{
  "case_id": "family_weekend_001",
  "user_input": "今天下午想带老婆孩子出去玩 4 小时，别太远，老婆最近减肥，孩子 5 岁",
  "expected_constraints": ["亲子", "健康饮食", "4小时", "低距离"],
  "expected_tools": [
    "search_activities",
    "search_restaurants",
    "estimate_route",
    "check_availability"
  ],
  "success_criteria": [
    "包含亲子活动",
    "包含低卡餐厅",
    "总时长不超过 4 小时",
    "下单前请求用户确认"
  ]
}
```

## Two-Person Division Of Labor

Person A: Agent and backend

- Intent recognition
- Slot extraction
- Planning graph
- Tool calling
- Scoring and explanation
- Confirmation and execution flow

Person B: data, mock APIs, and frontend demo

- Structured mock dataset
- Search and feasibility APIs
- Booking/order mocks
- UI for chat, plans, tool trace, and confirmation
- Demo cases and presentation material

Shared responsibilities:

- Product scenarios
- Evaluation criteria
- Hackathon presentation story
- Final integration

## Product-Level Differentiators

1. Make constraints visible: show why a plan matches the user.
2. Show tool traces: weather, route, availability, queue, budget.
3. Generate multiple plans, not just one answer.
4. Provide a "why not" explanation for rejected options.
5. Add confirmation gates before transactional steps.
6. Support fallback: if unavailable, expensive, rainy, too far, or long queue, generate alternatives.

## Resume-Friendly Description

Recommended wording:

> 构建本地生活周末规划 AI Agent，支持自然语言需求解析、结构化约束抽取、多工具调用、候选路线生成、预约/下单 Mock 执行及异常兜底。设计 Place/Activity/Restaurant 数据 Schema 与可插拔 Tool API，使用 40+ 条高质量场景数据模拟商家库存、排队、套餐和评价摘要，支持后续替换真实开放平台接口。

When asked where the data comes from:

> 黑客松阶段没有接入美团真实生产 API，而是按照真实本地生活平台的数据结构设计 Mock Commerce API。项目重点是 Agent 的规划与执行链路，数据层做成 Adapter，可替换为地图 POI、商家开放平台或企业授权接口。

## Recommended Next Steps

1. Fix file encoding and syntax issues in the current repo.
2. Define final Place, Restaurant, Activity, Order schemas.
3. Move mock data out of tool functions into JSON files.
4. Implement mock search and feasibility checks over the JSON data.
5. Wire graph flow: intent -> slots -> planning -> confirmation -> execution.
6. Add 5-8 deterministic demo/evaluation cases.
7. Build a simple UI that shows chat, candidate plans, tool trace, and confirmation actions.
8. Optionally integrate one real external API, preferably weather or map route, after the mock flow is stable.

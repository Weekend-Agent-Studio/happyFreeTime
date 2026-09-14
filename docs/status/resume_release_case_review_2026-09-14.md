# Resume Release E2E 用例人工审核记录

> 审核日期：2026-09-14  
> 审核人：用户与 Codex 共同确认  
> 数据文件：`evals/resume_release_cases.json`  
> 审核范围：35 条 E2E holdout（规划、澄清、冲突、单站替换和多轮替换）

## 1. 审核说明

本记录把临时侧边会话中逐条讨论、用户确认纳入的结论落盘。审核依据是用户输入、产品范围、当前领域合同和已明确的 Fixture 语义，不使用系统本轮输出反向修改答案。

对话中并没有为每一条保留逐字的“通过”原文，因此下表的“审核过程摘要”是对讨论过程的规范化记录，不是伪造的逐字 transcript。后续交接时应同时阅读本文件和数据文件。

`reviewed` 的含义是“预期行为已经由人工确认”，不是“任何 Variant 都必须通过”。B0 规则基线失败可以是消融结果；预期为 `question` 或 `conflict` 的 Case 也可以在正确返回反问/冲突时计为成功。

本轮 35 条均决定纳入 E2E 主评测分母；额外指标按现有 `category`、`tags` 和 `expected` 字段切分：

- hard constraint：7 条（只在这些 Case 上计算硬约束 Case 通过率）。
- clarification：4 条；conflict：4 条；planning：18 条；modification：9 条。
- semantic/advice/retrieval/weather 等标签用于相应断言，不改变人工审核状态。
- 本轮没有把任何 Case 标成 capability boundary；如果后续产品范围改变，应新增一次审核修订，不得因某次模型失败反向改标签。

## 2. 逐条审核记录

| # | case_id | 结论 | 主要评测范围 | 审核过程摘要 |
|---:|---|---|---|---|
| 1 | `plan_exact_departure_1520` | reviewed / 纳入 | planning + hard constraint | 明确“15:20 准时出发、20:00 前回家”是两个独立硬约束；预期比较 `departure_at` 与 `return_by`，不把精确时间降级为“下午”窗口。 |
| 2 | `plan_all_day_date_relaxed` | reviewed / 纳入 | planning + semantic + advice | “跟女朋友约会一整天、不要太累”同时表达全天范围、约会场景和低疲劳目标；全天编译和语义目标召回均属于当前 Resume Release 故事。 |
| 3 | `plan_dinner_only_light` | reviewed / 纳入 | planning + structure + semantic + advice | “只安排一家晚饭、清淡些”明确要求单站晚餐与低辣/清淡偏好；S1-B 已定义不补活动、不改成双站。 |
| 4 | `plan_lunch_only_structure` | reviewed / 纳入 | planning + structure + time | 单站午饭是明确业务需求，午餐角色和 11:30–14:00 时间假设已补齐；不能因为早期只实现晚餐而删除该 Case。 |
| 5 | `plan_parents_novel_not_tiring` | reviewed / 纳入 | planning + semantic + retrieval + advice | 父母同行、新鲜感、少走路是三个可执行语义目标；候选是否真正有相应资料由召回证据与后置验证判断。 |
| 6 | `plan_child_indoor_explore` | reviewed / 纳入 | planning + semantic + retrieval + advice | 六岁孩子、室内、互动探索构成家庭友好与新鲜感需求；室内是候选资料和天气安全检查的依据。 |
| 7 | `plan_quiet_date_chat` | reviewed / 纳入 | planning + semantic + advice | 安静约会、慢慢聊天是独立的语义目标；用于观察 Router/PlanningIntent 是否保留并传递 quiet、romantic、conversation_friendly。 |
| 8 | `plan_rain_indoor_fallback` | reviewed / 纳入 | planning + weather + advice | “先看天气、下雨改室内”固定为 rainy Fixture；纳入依据是天气 Provider、室内过滤和证据化解释链，不要求模型自由执行工具循环。 |
| 9 | `plan_local_food_novelty` | reviewed / 纳入 | planning + semantic + retrieval + advice | 北京本地特色且排除连锁快餐是开放语义和排除条件；是否命中由 POI Profile/召回证据决定，不用字符串包含冒充质量。 |
| 10 | `plan_single_activity_only` | reviewed / 纳入 | planning + structure | “只去一个活动、不安排吃饭”明确为单站 activity 骨架；单站活动已作为产品能力补齐。 |
| 11 | `plan_evening_short_trip` | reviewed / 纳入 | planning + pace | 晚上七点后、简单转转、不要太满，验证 evening 窗口和 fewer_stops/low_fatigue 的软目标表达。 |
| 12 | `plan_strict_budget_200` | reviewed / 纳入 | planning + hard constraint | 两人出门、人均 200 元且严格不超；预期同时检查预算值和 strict 语义，费用由代码后置验证。 |
| 13 | `plan_total_distance_limit` | reviewed / 纳入 | planning + hard constraint | “全程不超过 15 公里”是全程距离硬约束；需要在完整路线事实产生后验证，不能由模型估计。 |
| 14 | `plan_explicit_range` | reviewed / 纳入 | planning + hard constraint | “上午十点到下午四点之间”对应公开 `time_window`，不使用不存在的 `time_start/time_end` 虚拟字段。 |
| 15 | `plan_novel_indoor_not_museum` | reviewed / 纳入 | planning + semantic + retrieval + advice | 排除普通公园/传统博物馆，同时要求新鲜室内体验；审核确认这是开放语义与候选覆盖的联合 Case。 |
| 16 | `plan_relaxed_lakeside` | reviewed / 纳入 | planning + semantic + retrieval + advice | 湖边、可休息、顺便吃饭表达低疲劳和轻松交流需求；候选资料不足时应如实暴露，而不是改写用户目标。 |
| 17 | `plan_family_home_style_dinner` | reviewed / 纳入 | planning + semantic + food + advice | 一家人和稳妥家常菜是家庭友好/餐饮资料检索需求；资料证据不足时不能声称真实口碑。 |
| 18 | `plan_fast_meal_more_activity` | reviewed / 纳入 | planning + semantic + pace + advice | 吃饭快、给活动留时间、整体别太累，验证用餐时长/节奏与软目标的组合；不要求新增骨架。 |
| 19 | `clarify_unknown_date` | reviewed / 纳入 | clarification + gate | “等忙完那天”没有可规划日期；正确行为是反问 date，而不是猜测日期继续规划。 |
| 20 | `clarify_strict_budget_without_amount` | reviewed / 纳入 | clarification + gate | 用户表达严格预算但没有金额；正确行为是询问人均预算，不把“千万不能超”当成数值。 |
| 21 | `clarify_ambiguous_location` | reviewed / 纳入 | clarification + geocoding | “我公司附近”固定为 `not_found` Fixture；位置不能依赖 Provider 偶然结果，必须反问。 |
| 22 | `clarify_vague_return_deadline` | reviewed / 纳入 | clarification + gate | “晚饭前后回家”没有明确时刻；审核确认应询问 `return_by`，不能擅自选择一个时间。 |
| 23 | `conflict_departure_after_return` | reviewed / 纳入 | conflict + hard constraint | 17:30 出发、17:00 前回家只制造一个明确矛盾；预期冲突码固定为 `DEPARTURE_NOT_BEFORE_RETURN_BY`。 |
| 24 | `conflict_impossible_budget` | reviewed / 纳入 | conflict + hard constraint | 人均最多 10 元且要求活动和晚饭，验证严格预算无可行方案时返回冲突，而不是悄悄超预算。 |
| 25 | `conflict_impossible_total_distance` | reviewed / 纳入 | conflict + hard constraint | 两个地点但全程 0.1 公里以内，验证路线事实和可行性冲突；不能让模型替代路线计算。 |
| 26 | `conflict_dinner_unavailable` | reviewed / 纳入 | conflict + availability | 固定 `all_unavailable`；已确认无位时必须不返回可执行晚餐，并返回明确冲突。未知状态与已确认不可用不能混淆。 |
| 27 | `modify_activity_shorter` | reviewed / 纳入 | modification + diff + route | 前端显式替换活动并要求更短通勤；只重召回目标站，完整复验路线，其他晚饭站 identity 必须保持。 |
| 28 | `modify_activity_quieter` | reviewed / 纳入 | modification + diff + semantic + advice | 目标 index 明确，输入偏好为安静/适合聊天；验证单站替换和候选级 PlanDiff，不把它当自然语言目标识别准确率。 |
| 29 | `modify_dinner_less_spicy` | reviewed / 纳入 | modification + diff + food + semantic | 活动保持、晚餐换成少辣；验证目标站是晚餐、非目标活动保持、召回证据与解释均对应少辣。 |
| 30 | `modify_activity_similar` | reviewed / 纳入 | modification + diff | “换个类似的”是显式 preset；验证最多两个候选和替换 diff，不要求再次调用 PlanningIntent。 |
| 31 | `modify_middle_stop_three_station` | reviewed / 纳入 | modification + three-stop + diff | 三站中的中间站替换，验证任意 index、锁定其他站和候选级 diff；不是只针对两站的特例。 |
| 32 | `modify_last_stop_four_station` | reviewed / 纳入 | modification + four-stop + route | 四站最后一站替换并要求更近；验证 1–4 站通用链路和完整路线裁决。 |
| 33 | `modify_first_stop_parents` | reviewed / 纳入 | modification + parents + semantic + diff | 三站首站替换为父母更轻松的地点；验证目标站语义、非目标 identity 和低疲劳证据。 |
| 34 | `modify_two_rounds_one_at_a_time` | reviewed / 纳入 | modification + multi-turn + diff | 连续两轮、每轮只换一个目标；验证 active Plan Version 推进、旧选择清空和第二轮基于新版本继续。 |
| 35 | `modify_preserves_selected_anchor` | reviewed / 纳入 | modification + identity + diff | 用户明确“只换这一站，其他保持”；验证 selected plan 锚点、目标站 identity 和非目标站保持不变。 |

## 3. 交接与使用规则

1. 运行前先读取 `evals/resume_release_cases.json` 的 `label_status`，当前 35 条应为 `reviewed`；本文件用于解释为什么，不替代机器可读数据。
2. 完整 B0–B3 运行时，35 条可作为同一组 E2E 分母；B0 的失败是基线结果，不应通过修改预期掩盖。
3. Retrieval 的 16 条是独立数据集，不能和这 35 条合并成一个成功率；其中 15 条进入 Recall/nDCG/MRR 分母，1 条只做 grounding safety。
4. 如果未来决定把某条降为能力边界，应新增审核修订记录、说明 reviewer/date/reason，并重新生成报告；不能只因模型输出失败就把它移出分母。
5. 当前审核记录不包含 API Key、模型原始响应或未脱敏用户数据。

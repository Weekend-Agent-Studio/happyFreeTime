"""不调用外部模型的确定性 Router，用于本地开发、演示和离线回归。"""

from __future__ import annotations

import re
from time import perf_counter

from app.domain.catalog import ResourceType
from app.domain.constraints import (
    CommandOperation,
    ConstraintPatch,
    ConversationCommand,
    EventClockProposal,
    Intent,
    Interpretation,
    RawConstraints,
    RouteObjective,
    StopRole,
    PeriodProposal,
    TargetReference,
    TripRangeProposal,
    TimeProposal,
    TimeScope,
)
from app.domain.runtime import RuntimeDecision
from app.domain.turn import TurnCompiler
from app.domain.turn import (
    CheckWeatherProposal,
    ChitchatProposal,
    CreatePlanProposal,
    PatchConstraintsProposal,
    QueryPlanProposal,
    ReplaceStopProposal,
    TurnProposal,
    TurnTargetProposal,
)
from app.services.enrichment import TemporalCompiler
from app.services.router_extractor import RouterContext, TurnInterpreterResult


def _parse_budget_amount(value: str) -> int | None:
    """Parse the small Chinese amount vocabulary used by the Demo Router.

    This is intentionally bounded and only turns an amount already anchored by
    ``人均/每人`` into a number.  It is not a general natural-language number
    parser; an unrecognized amount remains raw evidence and Gate can ask for a
    concrete budget.
    """

    if value.isdigit():
        return int(value)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if not value or any(char not in digits and char not in units for char in value):
        return None
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
            continue
        unit = units[char]
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    return total + section + number


class DemoRouter:
    """用小型规则集实现与真实 Router 相同的 ``interpret`` 接口。

    它不是生产级中文理解器，只覆盖 README 中列出的演示表达。价值在于无需
    API Key 也能真实走完 Graph、反问、规划、持久化和前端链路。
    """

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        text = user_input.strip()
        if text in {"你好", "嗨", "谢谢", "你是谁"}:
            return Interpretation(
                primary_intent=Intent.CHITCHAT,
                intent_scores={Intent.CHITCHAT: 1.0},
                reply="你好，我可以帮你规划活动、餐厅和 1–4 站行程。",
            )

        if (
            "餐厅保留" in text
            and "活动" in text
            and "换" in text
            and any(phrase in text for phrase in ("近一点", "近点", "更近"))
        ):
            return Interpretation(
                primary_intent=Intent.REFINE_PLAN,
                intent_scores={Intent.REFINE_PLAN: 1.0},
                conversation_command=ConversationCommand(
                    operation=CommandOperation.REPLACE,
                    target=TargetReference(
                        role=StopRole.ACTIVITY,
                        raw_text="活动",
                    ),
                    locked_targets=(
                        TargetReference(
                            resource_type=ResourceType.RESTAURANT,
                            raw_text="餐厅",
                        ),
                    ),
                    replacement_criteria=(RouteObjective(),),
                    evidence={
                        "replace": "活动换近一点",
                        "keep": "餐厅保留",
                    },
                ),
            )

        if (
            context.has_selected_plan
            and "活动保留" in text
            and any(value in text for value in ("晚餐", "晚饭"))
            and any(value in text for value in ("少辣", "清淡"))
        ):
            return Interpretation(
                primary_intent=Intent.REFINE_PLAN,
                intent_scores={Intent.REFINE_PLAN: 1.0},
                conversation_command=ConversationCommand(
                    operation=CommandOperation.REPLACE,
                    target=TargetReference(role=StopRole.DINNER, raw_text="晚餐"),
                    locked_targets=(TargetReference(role=StopRole.ACTIVITY, raw_text="活动"),),
                    constraint_patch=ConstraintPatch(
                        diet_tags=("少辣" if "少辣" in text else "清淡",),
                    ),
                    evidence={"replace": "晚餐", "keep": "活动"},
                ),
            )

        # Keep deterministic text interpretation inside this Demo Router;
        # downstream services receive the same bounded wire proposal as the LLM.
        patch = self.patch_proposal_from_text(
            text,
            has_plans=context.has_plans,
        )
        if patch is not None:
            return Interpretation(
                primary_intent=Intent.REFINE_PLAN,
                intent_scores={Intent.REFINE_PLAN: 1.0},
                conversation_command=ConversationCommand(
                    operation=CommandOperation.PATCH_CONSTRAINTS,
                    constraint_patch=patch,
                    evidence={"patch": text},
                ),
            )

        # Unresolved natural-language replacement enters the same field-level
        # clarification flow as a Gate question. Keep only the raw target.
        if context.has_selected_plan and any(marker in text for marker in ("换", "替换", "更换")):
            raw_target = next(
                (value for value in ("活动", "晚餐", "晚饭", "午饭", "餐厅", "那个地方", "那一站") if value in text),
                "那个地方",
            )
            target_aliases = {
                "活动": TargetReference(role=StopRole.ACTIVITY, raw_text=raw_target),
                "晚餐": TargetReference(role=StopRole.DINNER, raw_text=raw_target),
                "晚饭": TargetReference(role=StopRole.DINNER, raw_text=raw_target),
                "午饭": TargetReference(role=StopRole.LUNCH, raw_text=raw_target),
                "餐厅": TargetReference(resource_type=ResourceType.RESTAURANT, raw_text=raw_target),
            }
            target = target_aliases.get(raw_target)
            if target is not None:
                return Interpretation(
                    primary_intent=Intent.REFINE_PLAN,
                    intent_scores={Intent.REFINE_PLAN: 1.0},
                    conversation_command=ConversationCommand(
                        operation=CommandOperation.REPLACE,
                        target=target,
                        replacement_criteria=(
                            (RouteObjective(),)
                            if any(value in text for value in ("近一点", "近点", "更近"))
                            else ()
                        ),
                        evidence={"target": raw_target},
                    ),
                )
            return Interpretation(
                primary_intent=Intent.REFINE_PLAN,
                intent_scores={Intent.REFINE_PLAN: 1.0},
                target_reference=raw_target,
                evidence_map={"target": raw_target},
                requires_clarification=True,
            )

        # 普通创建请求仍由这一组有限规则抽取；字段级反问恢复不会把答案
        # 拼回这里；Graph 会把回答编译成字段级 RequestPatch 并交给 ConstraintEngine。
        budget_match = re.search(
            r"(?:人均|每人)\s*"
            r"(?:(?:最多|不超过|至多|不超|严格控制在|控制在|约|大约)\s*)?"
            r"(?P<amount>\d{1,4}|[零〇一二三四五六七八九十百千万两]+)"
            r"\s*(?:元|块)?",
            text,
        )
        distance_text = next(
            (phrase for phrase in ("步行可达", "附近", "别太远") if phrase in text),
            None,
        )
        origin_match = re.search(
            r"(?:从|由)(?P<origin>[^，,。；;]{2,30})(?:出发|出门|离开)",
            text,
        )
        planning_area_match = re.search(
            r"(?:在|去)(?P<area>[^，,。；;]{2,20})(?:玩|安排|活动|逛)",
            text,
        )
        origin_text = origin_match.group("origin") if origin_match else None
        planning_area_text = planning_area_match.group("area") if planning_area_match else None
        (
            date_text,
            date_reference,
            weekday,
            week_offset,
            absolute_date,
        ) = TemporalCompiler.extract_date(text)
        if date_text is None:
            date_text = TemporalCompiler.extract_unresolved_date_text(text)
        departure_match = re.search(
            r"(?:(?:上午|早上|早|下午|晚上)\s*)?(?:\d{1,2}[:：]\d{2}|[一二三四五六七八九十两]+点(?:半|[一二三四五六七八九十两]+分?)?)"
            r"\s*(?:准时\s*)?(?:出发|离开)",
            text,
        )
        departure_period_match = re.search(
            r"(?:上午|早上|早|下午|晚上|夜里|夜间).{0,8}(?:出发|出门|离开)|"
            r"(?:出发|出门|离开).{0,8}(?:上午|早上|早|下午|晚上|夜里|夜间)",
            text,
        )
        time_proposals: list[TimeProposal] = []
        if departure_match:
            departure_clock = TemporalCompiler.normalize_clock_text(
                departure_match.group(0)
            )
            if departure_clock:
                time_proposals.append(
                    EventClockProposal(
                        event="departure",
                        clock=departure_clock,
                        evidence=departure_match.group(0),
                    )
                )
        elif departure_period_match:
            departure_period = TemporalCompiler.extract_departure_period(
                departure_period_match.group(0)
            )
            if departure_period is not None:
                time_proposals.append(
                    PeriodProposal(
                        event="departure",
                        period=departure_period,
                        evidence=departure_period_match.group(0),
                    )
                )
        dinner_only_match = re.search(
            r"(?P<exclusive>(?:只|仅|就)(?:安排|去|吃))"
            r"(?:一(?:家|顿)|个)?(?:餐厅)?(?:吃)?(?P<dinner>晚饭|晚餐)",
            text,
        )
        lunch_only_match = re.search(
            r"(?P<exclusive>(?:只|仅|就)(?:安排|去|吃))"
            r"(?:一(?:家|顿)|个)?(?:餐厅)?(?:吃)?(?P<lunch>午饭|午餐)",
            text,
        )
        activity_only_match = re.search(
            r"(?P<exclusive>(?:只|仅|就)(?:安排|去|看))\s*"
            r"(?P<count>一个|一项|一场|一站|一处|个)?\s*"
            r"(?P<activity>活动|项目|展览|演出|景点|地方|展)"
            # Do not turn “只安排一个活动和晚饭” into an activity-only
            # request; a conjunction with a meal means the user asked for a
            # multi-stop structure. Explicit no-meal wording is allowed.
            r"(?!(?:\s*(?:和|、|并|后|[,，])\s*(?!不安排|不吃|不去).*"
            r"(?:吃饭|用餐|晚饭|午饭|午餐|晚餐)))"
            r"(?P<no_meal>\s*[,，、]\s*(?:不安排|不吃|不去)"
            r"(?:吃饭|用餐|晚饭|午饭|午餐|晚餐))?",
            text,
        )
        # “只安排活动和晚饭”“只安排看展，自己解决” do not prove that
        # exactly one activity is intended. Require an explicit quantity or an
        # explicit no-meal clause before turning the phrase into a hard count.
        if activity_only_match and not (
            activity_only_match.group("count")
            or activity_only_match.group("no_meal")
        ):
            activity_only_match = None

        # Explicit multi-role requests are still a closed, deterministic
        # extraction. They describe required slots; an explicitly exclusive
        # “只/仅/就安排 A 和 B” also fixes the total count. A specific
        # DINNER/LUNCH role is retained so PlanSpecCompiler can bind it to a
        # generic MEAL role where appropriate, without hard-coding another
        # shape here.
        role_mentions: list[tuple[int, StopRole, str]] = []
        for role, patterns in (
            (
                StopRole.ACTIVITY,
                ("活动", "项目", "展览", "演出", "景点", "逛展", "看展", "游玩"),
            ),
            (StopRole.LUNCH, ("午饭", "午餐")),
            (StopRole.DINNER, ("晚饭", "晚餐")),
            (StopRole.MEAL, ("吃饭", "用餐")),
        ):
            for phrase in patterns:
                match = re.search(re.escape(phrase), text)
                if match is not None:
                    # A negated clause applies to every role it names, not
                    # just the first role: “不安排活动和晚饭” must not become
                    # an explicit two-stop structure.  Keep the check bounded
                    # by punctuation so a later positive clause can opt back
                    # in (for example, “不安排活动，晚饭照常”).
                    clause_before = re.split(r"[，,。；;]", text[: match.start()])[-1]
                    if re.search(
                        r"(?:^|\s)(?:不安排|不去|不看|不吃|别安排|别去|别看|别吃|"
                        r"不要安排|不要去|不要看|不要吃|不想安排|不想去|不想看|不想吃|"
                        r"无需安排|无需去|无需看|无需吃|不用安排|不用去|不用看|不用吃)"
                        r"\s*[^，,。；;]*$",
                        clause_before,
                    ):
                        break
                    # “晚饭自己解决/不安排晚饭” explicitly removes the meal
                    # from the requested itinerary; it is not a required slot.
                    if role in {StopRole.LUNCH, StopRole.DINNER, StopRole.MEAL}:
                        head = text[max(0, match.start() - 8) : match.start()]
                        tail = text[match.end() : match.end() + 8]
                        # “晚饭前后回家”“晚饭后散步” use the meal word as a
                        # temporal anchor, not as a requested itinerary stop.
                        if re.match(r"\s*(?:前|后|前后)", tail):
                            break
                        if re.search(
                            r"(?:不安排|不吃|不去|不想安排|不想吃|不想去)\s*$",
                            head,
                        ) or re.match(
                            r"\s*[,，、]?\s*(?:自己解决|不安排|不吃|不去|不想安排|不想吃|不想去)",
                            tail,
                        ):
                            break
                    role_mentions.append((match.start(), role, phrase))
                    break
        role_mentions.sort(key=lambda item: item[0])
        multi_role_mentions: list[tuple[StopRole, str]] = []
        for _, role, phrase in role_mentions:
            if not multi_role_mentions or multi_role_mentions[-1][0] != role:
                multi_role_mentions.append((role, phrase))
        multi_required_roles = (
            tuple(role for role, _ in multi_role_mentions)
            if len(multi_role_mentions) >= 2
            else ()
        )
        exclusive_multi_match = (
            re.search(
                r"(?<!不)(?:只|仅|就)(?:想|要)?\s*(?:安排|计划|去|看|吃)",
                text,
            )
            if multi_required_roles
            else None
        )
        if multi_required_roles:
            # A single-role regex may match the prefix of “只安排一顿午饭和
            # 活动”。Once two roles are explicitly present, prefer the
            # multi-role interpretation instead of silently dropping one.
            dinner_only_match = None
            lunch_only_match = None
            activity_only_match = None
        multi_role_evidence = (
            "、".join(phrase for _, phrase in multi_role_mentions)
            if multi_required_roles
            else None
        )
        single_stop_match = next(
            (
                (role, match, group_name)
                for role, match, group_name in (
                    (StopRole.DINNER, dinner_only_match, "dinner"),
                    (StopRole.LUNCH, lunch_only_match, "lunch"),
                    (StopRole.ACTIVITY, activity_only_match, "activity"),
                )
                if match is not None
            ),
            (None, None, None),
        )
        single_stop_role, single_stop_match, single_stop_group = single_stop_match
        party_evidence = "约会" if "约会" in text else None
        party_size = 2 if party_evidence else None
        preferences = [
            label
            for keyword, label in (
                ("轻松", "轻松"),
                ("不希望太累", "不希望太累"),
                ("不太累", "不太累"),
                ("不累", "不累"),
                ("慢慢走", "慢慢走"),
                ("甜品", "甜品"),
                ("安静", "安静"),
                ("聊天", "适合聊天"),
                ("新鲜感", "新鲜感"),
                ("拍照", "适合拍照"),
                ("少辣", "少辣"),
            )
            if keyword in text
        ]
        strict_budget = bool(
            budget_match
            and any(
                phrase in text
                for phrase in (
                    "最多",
                    "不超过",
                    "至多",
                    "不超",
                    "严格控制",
                    "控制在",
                    "别超预算",
                    "严格预算",
                    "不能超预算",
                    "绝对不能超",
                    "千万不能超",
                )
            )
            or (
                "预算" in text
                and any(
                    phrase in text
                    for phrase in (
                        "别超预算",
                        "严格预算",
                        "不能超预算",
                        "绝对不能超",
                        "千万不能超",
                    )
                )
            )
        )
        require_availability_confirmation = any(
            phrase in text
            for phrase in (
                "确认有位",
                "确认有空位",
                "必须有位",
                "必须可预约",
                "确认可预约",
            )
        )
        return_by_text_match = re.search(
            r"(?:最晚|最迟|不晚于|在).{0,12}?(?:到家|回家|回来)"
            r"|\d{1,2}[:：]\d{2}\s*(?:前|之前)\s*(?:到家|回家|回来)"
            r"|(?:(?:上午|下午|晚上)\s*)?[一二三四五六七八九十两]+点"
            r"(?:(?:半)|(?:[零〇一二三四五六七八九十两]+)分?)?"
            r"\s*(?:前|之前)?\s*(?:到家|回家|回来)",
            text,
        )
        vague_return_by_match = re.search(
            r"(?:晚饭|午饭|天黑|晚上)\s*前后"
            r"(?:一定(?:要)?|要|得)?\s*(?:到家|回家|回来)",
            text,
        )
        effective_return_by_text_match = return_by_text_match or vague_return_by_match
        return_clock_match = (
            re.search(
                r"(?:(?:上午|早上|中午|下午|晚上|夜里|夜间)\s*)?"
                r"(?:\d{1,2}[:：]\d{2}|[零〇一二两三四五六七八九十]+点"
                r"(?:半|[零〇一二三四五六七八九十]+分?)?)",
                effective_return_by_text_match.group(0),
            )
            if effective_return_by_text_match
            else None
        )
        # Graph 恢复时会把原请求和答案拼接。裸 ``18:00`` 只有在原请求
        # 已明确出现“最晚…回家”语义时，才可作为返程 deadline，避免把普通
        # 时间表达误当成返程约束。
        resumed_return_by_match = (
            re.search(r"(?:^|\n)用户补充：\s*(\d{1,2})[:：](\d{2})\s*$", text)
            if effective_return_by_text_match and not return_clock_match
            else None
        )
        return_clock = (
            TemporalCompiler.normalize_clock_text(return_clock_match.group(0))
            if return_clock_match
            else TemporalCompiler.normalize_clock_text(resumed_return_by_match.group(1))
            if resumed_return_by_match
            else None
        )
        if effective_return_by_text_match:
            return_evidence = effective_return_by_text_match.group(0)
            if return_clock:
                time_proposals.append(
                    EventClockProposal(
                        event="return",
                        clock=return_clock,
                        evidence=return_evidence,
                    )
                )
            else:
                period = (
                    TimeScope.AFTERNOON
                    if "午饭" in return_evidence
                    else TimeScope.EVENING
                )
                time_proposals.append(
                    PeriodProposal(
                        event="return",
                        period=period,
                        evidence=return_evidence,
                    )
                )

        trip_text = text
        for matched in (departure_match, departure_period_match, effective_return_by_text_match):
            if matched is not None:
                trip_text = trip_text.replace(matched.group(0), " ", 1)
        trip_time_text, trip_scope, trip_window = TemporalCompiler.extract_time(trip_text)
        activity_scope = TemporalCompiler.extract_activity_period(trip_text)
        if trip_time_text and activity_scope is not None and trip_scope is not None:
            time_proposals.append(
                PeriodProposal(
                    event="activity",
                    period=activity_scope,
                    evidence=trip_time_text,
                )
            )
        elif trip_time_text and trip_window is not None:
            time_proposals.append(
                TripRangeProposal(
                    start=trip_window.start,
                    end=trip_window.end,
                    evidence=trip_time_text,
                )
            )
        elif trip_time_text and trip_scope is not None:
            time_proposals.append(
                PeriodProposal(
                    event="trip",
                    period=trip_scope,
                    evidence=trip_time_text,
                )
            )
        total_distance_match = re.search(
            r"((?:全程|总路程|总距离)\s*(?:不超过|不超|最多|至多|≤)?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:公里|km))",
            text,
            flags=re.IGNORECASE,
        )
        total_distance = (
            float(total_distance_match.group(2))
            if total_distance_match
            else None
        )
        raw = RawConstraints(
            date_text=date_text,
            date_reference=date_reference,
            weekday=weekday,
            week_offset=week_offset,
            absolute_date=absolute_date,
            exact_stop_count=(
                1
                if single_stop_role is not None
                else len(multi_required_roles)
                if exclusive_multi_match is not None
                else None
            ),
            required_stop_roles=(
                (single_stop_role,)
                if single_stop_role is not None
                else multi_required_roles
            ),
            adults=party_size,
            budget_text=budget_match.group(0) if budget_match else None,
            budget_per_person=(
                _parse_budget_amount(budget_match.group("amount"))
                if budget_match
                else None
            ),
            strict_budget=strict_budget,
            require_availability_confirmation=require_availability_confirmation,
            origin_text=origin_text,
            planning_area_text=planning_area_text,
            max_distance_text=distance_text,
            preferences=preferences,
            scene_tags=["约会"] if "约会" in text else [],
            total_distance_text=(
                total_distance_match.group(1) if total_distance_match else None
            ),
            total_distance_km=total_distance,
        )
        # 与真实 Router 一样保留证据映射，方便后续评测抽取是否有依据。
        evidence = {
            field: value
            for field, value in {
                "date_text": date_text,
                "date_reference": date_text if date_reference is not None else None,
                "weekday": date_text if weekday is not None else None,
                "week_offset": date_text if week_offset is not None else None,
                "absolute_date": date_text if absolute_date is not None else None,
                "exact_stop_count": (
                    single_stop_match.group("exclusive")
                    if single_stop_match
                    else exclusive_multi_match.group(0)
                    if exclusive_multi_match
                    else None
                ),
                "required_stop_roles": (
                    single_stop_match.group(single_stop_group)
                    if single_stop_match and single_stop_group
                    else multi_role_evidence
                ),
                "budget_per_person": budget_match.group(0) if budget_match else None,
                "max_distance_text": distance_text,
                "party": party_evidence,
                "require_availability_confirmation": (
                    "确认有位"
                    if require_availability_confirmation
                    else None
                ),
                "origin_text": origin_text,
                "planning_area_text": planning_area_text,
                "total_distance_km": (
                    total_distance_match.group(1) if total_distance_match else None
                ),
            }.items()
            if value is not None
        }
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=raw,
            time_proposals=tuple(time_proposals),
            extraction_confidence={
                field: 0.85 if field == "party" else 1.0
                for field in evidence
            },
            evidence_map=evidence,
            inferred_fields={"party"} if party_evidence else set(),
        )

    @staticmethod
    def patch_proposal_from_text(text: str, *, has_plans: bool) -> ConstraintPatch | None:
        """Build the bounded patch proposal used by the offline Demo Router."""
        if not has_plans:
            return None
        value = text.strip()
        if not value or any(
            marker in value
            for marker in ("换站", "换这站", "换个", "换活动", "换晚餐", "替换", "更换")
        ):
            return None
        if "保留" in value and any(marker in value for marker in ("活动", "晚餐", "晚饭", "餐厅")):
            return None
        date_text, _, _, _, _ = TemporalCompiler.extract_date(value)
        time_text, scope, explicit_window = TemporalCompiler.extract_time(value)
        activity_scope = TemporalCompiler.extract_activity_period(value)
        departure_period = TemporalCompiler.extract_departure_period(value)
        departure = None
        clock_like = re.search(
            r"(?:\d{1,2}[:：]\d{1,2}|[零〇一二两三四五六七八九十]+点(?:半|[零〇一二两三四五六七八九十]+分?)?)",
            value,
        )
        if re.search(r"出发|出门|离开", value) and clock_like and TemporalCompiler.normalize_clock_text(value):
            departure = value
            time_text = None
        return_by = value if re.search(r"回家|到家|回来", value) else None
        strict_budget: bool | None = None
        budget = None
        if "预算不限" in value or "不设预算" in value:
            strict_budget = False
        elif "预算" in value or "人均" in value or "每人" in value:
            budget = value
            strict_budget = True
        max_distance = value if re.search(r"(?:公里|千米|km|KM)", value) else None
        origin_match = re.search(r"(?:从|由)(?P<origin>[^，,。；;]{2,30})(?:出发|出门|离开)", value)
        planning_area_match = re.search(
            r"(?:在|去)(?P<area>[^，,。；;]{2,20})(?:玩|安排|活动|逛)",
            value,
        )
        origin = origin_match.group("origin") if origin_match else None
        planning_area = planning_area_match.group("area") if planning_area_match else None
        preferences = tuple(
            label
            for keyword, label in (("安静", "安静"), ("聊天", "适合聊天"), ("浪漫", "浪漫"), ("轻松", "轻松"), ("不累", "不累"))
            if keyword in value
        )
        diet_tags = tuple(
            label for keyword, label in (("少辣", "少辣"), ("清淡", "清淡"))
            if keyword in value
        )
        avoid = ("博物馆",) if "不要博物馆" in value else ()
        clear_structure = any(
            phrase in value
            for phrase in ("不用限制站数", "不限制站数", "不限站数", "站数不限")
        )
        count_match = re.search(
            r"(?:改成|改为|总共|一共|正好|安排|规划)\s*"
            r"(?P<count>\d+|零|一|两|二|三|四|五|六|七|八|九|十)\s*"
            r"(?:站|个地方|个地点|处|家|个活动)",
            value,
        )
        exact_stop_count = (
            _parse_budget_amount(count_match.group("count"))
            if count_match
            else None
        )
        role_mentions: list[tuple[int, StopRole]] = []
        for role, phrases in (
            (StopRole.LUNCH, ("午饭", "午餐")),
            (StopRole.DINNER, ("晚饭", "晚餐")),
            (StopRole.ACTIVITY, ("活动", "项目", "展览", "景点", "地方")),
            (StopRole.MEAL, ("吃饭", "用餐")),
        ):
            for phrase in phrases:
                match = re.search(re.escape(phrase), value)
                if match is None:
                    continue
                tail = value[match.end() : match.end() + 4]
                if role in {StopRole.LUNCH, StopRole.DINNER, StopRole.MEAL} and re.match(
                    r"\s*(?:前|后|前后)", tail
                ):
                    continue
                role_mentions.append((match.start(), role))
                break
        role_mentions.sort(key=lambda item: item[0])
        # Keep the ordered sequence stable while removing adjacent duplicate
        # mentions; repeated activity is meaningful only when explicitly said.
        deduped_roles: list[StopRole] = []
        for _, role in role_mentions:
            if not deduped_roles or deduped_roles[-1] != role:
                deduped_roles.append(role)
        structure_roles = tuple(deduped_roles) if len(deduped_roles) >= 1 else ()
        append_roles = (
            structure_roles
            if structure_roles and any(marker in value for marker in ("再安排", "再加", "增加"))
            and not any(marker in value for marker in ("改成", "改为", "总共", "一共", "正好"))
            else ()
        )
        replace_roles = () if append_roles else structure_roles
        vague_structure = (
            value
            if any(phrase in value for phrase in ("多安排几个地方", "多安排几个地点", "多去几个地方"))
            and exact_stop_count is None
            else None
        )
        has_patch_marker = any(
            marker in value
            for marker in (
                "补充", "忘了说", "对了", "另外", "再加", "重新规划", "改到", "改成", "改为",
                "安排", "总共", "一共", "正好", "不用限制站数", "多安排几个",
            )
        )
        if not (
            has_patch_marker or date_text or departure or departure_period or return_by
            or budget or strict_budget is not None or origin or preferences or diet_tags
            or avoid or max_distance or planning_area
            or scope is not None or explicit_window is not None
            or exact_stop_count is not None or replace_roles or append_roles or clear_structure
            or vague_structure
        ):
            return None
        return ConstraintPatch(
            date_text=date_text,
            return_by_text=return_by,
            departure_at_text=departure,
            departure_period=departure_period,
            time_window_text=(
                value
                if (
                    time_text
                    and departure is None
                    and departure_period is None
                    and return_by is None
                    and activity_scope is None
                )
                else None
            ),
            activity_time_scope=(
                scope
                if scope is not None and activity_scope is not None
                else None
            ),
            activity_time_text=(
                time_text
                if time_text and activity_scope is not None
                else None
            ),
            exact_stop_count=exact_stop_count,
            required_stop_roles=replace_roles,
            add_required_stop_roles=append_roles,
            structure_hint_text=vague_structure,
            clear_structure=clear_structure,
            origin_text=origin,
            planning_area_text=planning_area,
            budget_text=budget,
            max_distance_text=max_distance,
            preferences=preferences,
            diet_tags=diet_tags,
            avoid=avoid,
            strict_budget=strict_budget,
            clear_fields=("budget_per_person", "strict_budget") if strict_budget is False else (),
        )

    def interpret_with_runtime(
        self,
        user_input: str,
        context: RouterContext,
    ) -> TurnInterpreterResult:
        started_at = perf_counter()
        interpretation = self.interpret(user_input, context)
        # Keep the rule extractor's rich ``Interpretation`` as an internal
        # semantic projection, but cross the same public proposal contract as
        # the live Router before entering the compiler.  This makes the Demo
        # adapter exercise the production action boundary instead of keeping a
        # second interpretation-to-action route in the graph.
        proposal = self._proposal_from_interpretation(interpretation)
        compilation = TurnCompiler.compile(
            proposal,
            context=context.decision_context,
        )
        # The action is compiled from the proposal; preserve non-routing
        # presentation metadata from the deterministic extractor for the
        # response/diagnostic projection.
        compilation = compilation.model_copy(
            update={
                "interpretation": compilation.interpretation.model_copy(
                    update={
                        "reply": interpretation.reply,
                        "requires_clarification": interpretation.requires_clarification,
                        "selected_plan_index": interpretation.selected_plan_index,
                        "extraction_confidence": dict(interpretation.extraction_confidence),
                        "inferred_fields": set(interpretation.inferred_fields),
                    }
                )
            }
        )
        return TurnInterpreterResult(
            interpretation=compilation.interpretation,
            action=compilation.action,
            runtime=RuntimeDecision(
                stage="turn_interpreter",
                adapter="demo_rule",
                model_invoked=False,
                model_name=None,
                attempts=0,
                fallback_reason=None,
                latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
            ),
        )

    @staticmethod
    def _proposal_from_interpretation(interpretation: Interpretation) -> TurnProposal:
        """Project the deterministic extractor into the public UserAct wire.

        ``Interpretation`` remains useful to the rule extractor and request
        compilers as a constraint projection.  It is not allowed to become an
        action source: all actions below are compiled from the same
        discriminated ``TurnProposal`` used by the model Router.
        """

        command = interpretation.conversation_command
        if command is not None:
            if command.operation == CommandOperation.PATCH_CONSTRAINTS:
                return TurnProposal(
                    act=PatchConstraintsProposal(
                        constraint_patch=command.constraint_patch,
                        evidence_map=dict(command.evidence),
                    )
                )
            if command.operation == CommandOperation.REPLACE:
                target = command.target or TargetReference(raw_text="那个地方")

                def to_turn_target(reference: TargetReference) -> TurnTargetProposal:
                    return TurnTargetProposal(
                        role=reference.role,
                        resource_type=reference.resource_type,
                        stop_index=reference.stop_index,
                        raw_text=reference.raw_text,
                    )

                return TurnProposal(
                    act=ReplaceStopProposal(
                        target=to_turn_target(target),
                        locked_targets=tuple(
                            to_turn_target(item) for item in command.locked_targets
                        ),
                        replacement_criteria=command.replacement_criteria,
                        evidence=dict(command.evidence),
                    )
                )

        if interpretation.primary_intent == Intent.REFINE_PLAN:
            return TurnProposal(
                act=ReplaceStopProposal(
                    target=TurnTargetProposal(
                        raw_text=interpretation.target_reference or "那个地方"
                    ),
                    evidence=dict(interpretation.evidence_map),
                )
            )

        if interpretation.primary_intent in {Intent.PLAN_OUTING, Intent.FIND_ACTIVITY}:
            return TurnProposal(
                act=CreatePlanProposal(
                    raw_constraints=interpretation.raw_constraints,
                    time_proposals=interpretation.time_proposals,
                    evidence_map=dict(interpretation.evidence_map),
                )
            )
        if interpretation.primary_intent == Intent.CHECK_WEATHER:
            return TurnProposal(
                act=CheckWeatherProposal(
                    raw_constraints=interpretation.raw_constraints,
                    time_proposals=interpretation.time_proposals,
                    evidence_map=dict(interpretation.evidence_map),
                )
            )
        if interpretation.primary_intent == Intent.QUERY_PLAN:
            return TurnProposal(act=QueryPlanProposal(query=interpretation.reply or "当前方案"))
        return TurnProposal(act=ChitchatProposal())

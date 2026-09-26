"""不调用外部模型的确定性 Router，用于本地开发、演示和离线回归。"""

from __future__ import annotations

import re
from time import perf_counter

from app.domain.catalog import ResourceType
from app.domain.constraints import (
    CommandOperation,
    ConstraintPatch,
    ConversationCommand,
    Intent,
    Interpretation,
    RawConstraints,
    StopRole,
    TargetReference,
)
from app.domain.runtime import RuntimeDecision
from app.services.enrichment import TemporalCompiler
from app.services.constraint_patch import ConstraintPatchCompiler
from app.services.router_extractor import RouterContext


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
                    constraint_patch=ConstraintPatch(prefer_shorter_travel=True),
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

        # A follow-up that adds a constraint to the selected plan is compiled
        # by the same bounded proposal adapter used by the patch compiler.  No
        # Graph or clarification service scans the message independently.
        patch = ConstraintPatchCompiler.proposal_from_text(
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
                        constraint_patch=ConstraintPatch(
                            prefer_shorter_travel=any(value in text for value in ("近一点", "近点", "更近")),
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
        # 拼回这里，而是由 ClarificationResolver 直接投影到待补字段。
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
        (
            date_text,
            date_reference,
            weekday,
            week_offset,
            absolute_date,
        ) = TemporalCompiler.extract_date(text)
        if date_text is None:
            date_text = TemporalCompiler.extract_unresolved_date_text(text)
        time_text, time_scope, explicit_time_window = TemporalCompiler.extract_time(text)
        departure_match = re.search(
            r"(?:(?:上午|早上|下午|晚上)\s*)?(?:\d{1,2}[:：]\d{2}|[一二三四五六七八九十两]+点(?:半|[一二三四五六七八九十两]+分?)?)"
            r"\s*(?:准时\s*)?(?:出发|离开)",
            text,
        )
        departure_period = TemporalCompiler.extract_departure_period(text)
        # ``time_scope`` describes the whole outing.  A period attached only
        # to “出发/出门” must remain a departure hint; it is not a 09:00–12:00
        # trip window.  Keep the historical fuzzy scope when an exact clock is
        # present so old callers can still inspect the broad evidence, while
        # Enrichment gives the exact departure precedence.
        if departure_period is not None and departure_match is None:
            time_scope = None
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
        # DINNER/LUNCH role is retained so the compiler can bind it to the
        # generic MEAL template without inventing a new skeleton.
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
            r"(?:一定要|要|得)?\s*(?:到家|回家|回来)",
            text,
        )
        effective_return_by_text_match = return_by_text_match or vague_return_by_match
        return_by_match = re.search(
            r"(?:最晚|最迟|不晚于|在)\s*(\d{1,2})[:：](\d{2})"
            r"\s*(?:点|点钟)?\s*(?:前|之前)?\s*(?:到家|回家|回来)",
            text,
        ) or re.search(
            r"(\d{1,2})[:：](\d{2})\s*(?:前|之前)\s*(?:到家|回家|回来)",
            text,
        )
        # Graph 恢复时会把原请求和答案拼接。裸 ``18:00`` 只有在原请求
        # 已明确出现“最晚…回家”语义时，才可作为返程 deadline，避免把普通
        # 时间表达误当成返程约束。
        resumed_return_by_match = (
            re.search(r"(?:^|\n)用户补充：\s*(\d{1,2})[:：](\d{2})\s*$", text)
            if effective_return_by_text_match and not return_by_match
            else None
        )
        return_clock_match = return_by_match or resumed_return_by_match
        return_by = (
            f"{int(return_clock_match.group(1)):02d}:{int(return_clock_match.group(2)):02d}"
            if return_clock_match
            else None
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
            time_text=time_text,
            time_scope=time_scope,
            explicit_time_window=explicit_time_window,
            departure_period=departure_period,
            departure_at_text=(departure_match.group(0) if departure_match else None),
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
            max_distance_text=distance_text,
            preferences=preferences,
            scene_tags=["约会"] if "约会" in text else [],
            return_by_text=(
                effective_return_by_text_match.group(0)
                if effective_return_by_text_match
                else None
            ),
            return_by=return_by,
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
                "time_text": time_text,
                "time_scope": time_text if time_scope is not None else None,
                "explicit_time_window": time_text if explicit_time_window is not None else None,
                "departure_period": time_text if departure_period is not None else None,
                "departure_at_text": departure_match.group(0) if departure_match else None,
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
                "return_by_text": (
                    effective_return_by_text_match.group(0)
                    if effective_return_by_text_match
                    else None
                ),
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
            extraction_confidence={
                field: 0.85 if field == "party" else 1.0
                for field in evidence
            },
            evidence_map=evidence,
            inferred_fields={"party"} if party_evidence else set(),
        )

    def interpret_with_runtime(
        self,
        user_input: str,
        context: RouterContext,
    ) -> tuple[Interpretation, RuntimeDecision]:
        started_at = perf_counter()
        interpretation = self.interpret(user_input, context)
        return interpretation, RuntimeDecision(
            stage="turn_interpreter",
            adapter="demo_rule",
            model_invoked=False,
            model_name=None,
            attempts=0,
            fallback_reason=None,
            latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
        )

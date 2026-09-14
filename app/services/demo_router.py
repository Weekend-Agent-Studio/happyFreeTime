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
    TimeScope,
)
from app.domain.runtime import RuntimeDecision
from app.services.router_extractor import RouterContext


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

        # 反问答案会与原问题拼成一段文本重新进入 Router，因此正则可以同时
        # 处理初始请求和“用户补充：人均300”这种恢复后的输入。
        budget_match = re.search(r"(?:人均|每人)\s*(\d{2,4})", text)
        distance_text = next(
            (phrase for phrase in ("步行可达", "附近", "别太远") if phrase in text),
            None,
        )
        date_text = next(
            (phrase for phrase in ("今天", "明天", "后天", "周六") if phrase in text),
            None,
        )
        time_scope = None
        time_text = next(
            (
                phrase
                for phrase in (
                    "一整天",
                    "全天",
                    "从早到晚",
                    "玩一天",
                    "上午",
                    "中午",
                    "下午",
                    "晚上",
                )
                if phrase in text
            ),
            None,
        )
        if time_text in {"一整天", "全天", "从早到晚", "玩一天"}:
            time_scope = TimeScope.ALL_DAY
        elif time_text == "上午":
            time_scope = TimeScope.MORNING
        elif time_text == "下午":
            time_scope = TimeScope.AFTERNOON
        elif time_text == "晚上":
            time_scope = TimeScope.EVENING
        departure_match = re.search(
            r"(?:(?:上午|下午|晚上)\s*)?(?:\d{1,2}[:：]\d{2}|[一二三四五六七八九十两]+点(?:半|[一二三四五六七八九十两]+分?)?)"
            r"\s*(?:准时\s*)?(?:出发|离开)",
            text,
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
        strict_budget = any(
            phrase in text for phrase in ("别超预算", "严格预算", "不能超预算")
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
            if return_by_text_match and not return_by_match
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
            time_text=time_text,
            time_scope=time_scope,
            departure_at_text=(departure_match.group(0) if departure_match else None),
            exact_stop_count=1 if single_stop_role is not None else None,
            required_stop_roles=(single_stop_role,) if single_stop_role is not None else (),
            adults=party_size,
            budget_text=budget_match.group(0) if budget_match else None,
            budget_per_person=int(budget_match.group(1)) if budget_match else None,
            strict_budget=strict_budget,
            require_availability_confirmation=require_availability_confirmation,
            max_distance_text=distance_text,
            preferences=preferences,
            scene_tags=["约会"] if "约会" in text else [],
            return_by_text=(
                return_by_text_match.group(0) if return_by_text_match else None
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
                "time_text": time_text,
                "departure_at_text": departure_match.group(0) if departure_match else None,
                "exact_stop_count": (
                    single_stop_match.group("exclusive")
                    if single_stop_match
                    else None
                ),
                "required_stop_roles": (
                    single_stop_match.group(single_stop_group)
                    if single_stop_match and single_stop_group
                    else None
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
                    return_by_text_match.group(0) if return_by_text_match else None
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

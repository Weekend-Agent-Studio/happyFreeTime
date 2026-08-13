"""集中管理“何时必须打断流程向用户提问”的产品策略。"""

from pydantic import BaseModel, ConfigDict

from app.domain.constraints import (
    EnrichmentResult,
    Intent,
    Interpretation,
    QuestionDecision,
)


class GateContext(BaseModel):
    """只有到特定阶段才成立的动态条件，例如预订和年龄限制。"""
    model_config = ConfigDict(extra="forbid")

    has_plans: bool = False
    booking_required: bool = False
    child_age_required: bool = False


class NeedQuestionGate:
    """只在缺失信息会阻断有效下游动作时反问。

    普通的预算或同行人数缺失由 Enrichment 的可见默认值处理；严格预算、无法
    解析的显式约束、预订人数和受年龄限制的人群信息才是 blocking。
    判断顺序也代表产品优先级：一次只问一个最重要的问题，减少盘问感。
    """

    def decide(
        self,
        interpretation: Interpretation,
        enrichment: EnrichmentResult,
        context: GateContext,
    ) -> QuestionDecision:
        constraints = enrichment.constraints

        if interpretation.primary_intent == Intent.EXECUTE_PLAN:
            if not context.has_plans or interpretation.selected_plan_index is None:
                return QuestionDecision(
                    need_question=True,
                    field="selected_plan_index",
                    question="你想执行哪个方案？可以选择第一个、第二个或第三个方案。",
                    severity="blocking",
                )

        if constraints.strict_budget and constraints.budget_per_person is None:
            return QuestionDecision(
                need_question=True,
                field="budget_per_person",
                question="你的预算大概是多少？可以告诉我总预算或人均预算。",
                severity="blocking",
            )

        raw = interpretation.raw_constraints
        if raw.date_text and constraints.date is None:
            return QuestionDecision(
                need_question=True,
                field="date",
                question="你具体想安排在哪一天？可以直接告诉我日期或说今天、明天。",
                severity="blocking",
            )

        if raw.time_text and constraints.time_window is None:
            return QuestionDecision(
                need_question=True,
                field="time_window",
                question="你大概想从几点到几点？",
                severity="blocking",
            )

        if raw.location_text and constraints.location is None:
            return QuestionDecision(
                need_question=True,
                field="location",
                question="我还不能确定这个位置，能提供更具体的地点或地标吗？",
                severity="blocking",
            )

        if raw.max_distance_text and constraints.max_distance_km is None:
            return QuestionDecision(
                need_question=True,
                field="max_distance_km",
                question="你能接受的最远距离大概是多少公里？",
                severity="blocking",
            )

        if context.booking_required:
            party = constraints.party
            if party is None or party.source.value == "default_rule":
                return QuestionDecision(
                    need_question=True,
                    field="party",
                    question="实际需要预订几位成人和几位儿童？",
                    severity="blocking",
                )

        if context.child_age_required:
            party = constraints.party.value if constraints.party else None
            if party is None or party.children > 0 and party.child_age is None:
                return QuestionDecision(
                    need_question=True,
                    field="child_age",
                    question="同行儿童几岁？部分活动有明确的年龄限制。",
                    severity="blocking",
                )

        return QuestionDecision(need_question=False)

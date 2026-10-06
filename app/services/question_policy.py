"""Deterministic policy for deciding whether a structured issue blocks work."""

from pydantic import BaseModel, ConfigDict

from app.domain.constraints import (
    ClarificationIssue,
    Intent,
    PlanRequest,
    QuestionDecision,
)


class QuestionPolicyContext(BaseModel):
    """只有到特定阶段才成立的动态条件，例如预订和年龄限制。"""
    model_config = ConfigDict(extra="forbid")

    has_plans: bool = False
    selected_plan_index: int | None = None
    booking_required: bool = False
    child_age_required: bool = False


class QuestionPolicy:
    """Render structured issues as questions; never inspect raw user proposals.

    ConstraintEngine and the request compilers decide which fields are unresolved.
    This policy owns deterministic wording and interaction-only questions such as
    selecting an existing plan or collecting booking-specific party details.
    """

    def decide(
        self,
        intent: Intent,
        request: PlanRequest,
        context: QuestionPolicyContext,
        issue: ClarificationIssue | None = None,
    ) -> QuestionDecision:
        if issue is not None:
            return self._for_issue(issue, request)

        if intent == Intent.EXECUTE_PLAN:
            if not context.has_plans or context.selected_plan_index is None:
                return QuestionDecision(
                    need_question=True,
                    field="selected_plan_index",
                    question="你想执行哪个方案？可以选择第一个、第二个或第三个方案。",
                    severity="blocking",
                    issue_kind="selection",
                    request_revision=request.revision,
                    rule_id="question.selected_plan.v1",
                )

        if context.booking_required:
            party = request.party
            if party is None or party.source.value == "default_rule":
                return QuestionDecision(
                    need_question=True,
                    field="party",
                    question="实际需要预订几位成人和几位儿童？",
                    severity="blocking",
                    request_revision=request.revision,
                    rule_id="question.party.booking.v1",
                )

        if context.child_age_required:
            party = request.party.value if request.party else None
            if party is None or party.children > 0 and party.child_age is None:
                return QuestionDecision(
                    need_question=True,
                    field="child_age",
                    question="同行儿童几岁？部分活动有明确的年龄限制。",
                    severity="blocking",
                    request_revision=request.revision,
                    rule_id="question.child_age.required.v1",
                )

        return QuestionDecision(need_question=False, request_revision=request.revision)

    @staticmethod
    def _for_issue(issue: ClarificationIssue, request: PlanRequest) -> QuestionDecision:
        questions = {
            "location": (
                "我还不能确定这个位置，能提供更具体的地点或地标吗？"
                if issue.code == "LOCATION_REQUIRES_RESOLUTION"
                else "你希望从哪里出发？可以输入小区、地铁站或附近地标。",
                "question.location.unresolved.v1",
            ),
            "planning_area": (
                "你希望主要在哪个区域活动？可以输入商圈、街道或地标。",
                "question.planning_area.unresolved.v1",
            ),
            "date": ("你具体想安排在哪一天？可以直接告诉我日期或说今天、明天。", "question.date.unresolved.v1"),
            "time_window": ("你希望大约几点开始、几点结束？", "question.time_window.required.v1"),
            "departure_at": ("你希望早上/下午/晚上大概几点出发？请给一个具体时间。", "question.departure_at.unresolved.v1"),
            "return_by": ("你最晚几点需要到家？请用例如 20:00 的时间告诉我。", "question.return_by.unresolved.v1"),
            "budget_per_person": ("你的预算大概是多少？可以告诉我总预算或人均预算。", "question.budget_per_person.v1"),
            "max_distance_km": ("你能接受的最远距离大概是多少公里？", "question.max_distance_km.unresolved.v1"),
            "total_distance_km": ("你希望全程总路程最多是多少公里？请给一个数字，例如 12 公里。", "question.total_distance_km.unresolved.v1"),
            "party": ("实际需要预订几位成人和几位儿童？", "question.party.booking.v1"),
            "child_age": ("同行儿童几岁？部分活动有明确的年龄限制。", "question.child_age.required.v1"),
        }
        question, rule_id = questions.get(
            issue.field,
            ("还需要补充一个信息才能继续规划。", f"question.{issue.field}.unresolved.v1"),
        )
        return QuestionDecision(
            need_question=True,
            field=issue.field,
            question=question,
            severity="blocking",
            issue_kind="constraint",
            request_revision=issue.request_revision if issue.request_revision is not None else request.revision,
            rule_id=rule_id,
        )

"""不调用 LLM 的轻量行为评测，用固定用例守住关键产品结果。"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.constraints import ActorContext, IdentityType, Interpretation
from app.domain.evaluation import EvalCase, EvalOutcome, EvalReport, ExpectedOutcome
from app.services.enrichment import EnrichmentService, EnvironmentContext
from app.services.planning import PlanningService
from app.services.question_gate import GateContext, NeedQuestionGate


def load_cases(path: Path) -> list[EvalCase]:
    """从 JSON 加载并通过 Pydantic 校验评测用例。"""
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase.model_validate(case) for case in raw_cases]


def run_smoke_cases(
    cases: list[EvalCase],
    environment: EnvironmentContext,
) -> EvalReport:
    """运行 Enrichment -> Gate -> Planning，并按结果类型判分。

    这不是最终质量评测：它不测真实 Router 抽取准确率，也不评价推荐主观质量；
    它用于快速发现默认规则、反问字段或冲突语义的回归。
    """
    enrichment_service = EnrichmentService()
    gate = NeedQuestionGate()
    planner = PlanningService()
    actor = ActorContext(
        user_id="eval-user",
        session_id="eval-session",
        identity_type=IdentityType.DEMO,
    )
    outcomes: list[EvalOutcome] = []

    for case in cases:
        interpretation = Interpretation(
            primary_intent=case.intent,
            intent_scores={case.intent: 1.0},
            raw_constraints=case.raw_constraints,
        )
        enrichment = enrichment_service.enrich(interpretation, actor, environment)
        decision = gate.decide(interpretation, enrichment, GateContext())

        # 每个用例只关心一个稳定的外部结果：需要提问、生成方案或返回冲突。
        # 这样内部算法可以重构，而评测仍围绕用户可观察行为。
        if decision.need_question:
            actual = ExpectedOutcome.QUESTION
            passed = (
                case.expected_outcome == actual
                and decision.field == case.expected_question_field
            )
            details = f"question_field={decision.field}"
        else:
            candidate_set = planner.plan(enrichment.constraints)
            if candidate_set.plans:
                actual = ExpectedOutcome.PLAN
                passed = case.expected_outcome == actual
                details = f"plans={len(candidate_set.plans)}"
            else:
                actual = ExpectedOutcome.CONFLICT
                conflict_code = candidate_set.conflict.code if candidate_set.conflict else None
                passed = (
                    case.expected_outcome == actual
                    and conflict_code == case.expected_conflict_code
                )
                details = f"conflict_code={conflict_code}"

        outcomes.append(
            EvalOutcome(
                case_id=case.case_id,
                passed=passed,
                actual_outcome=actual,
                details=details,
            )
        )

    return EvalReport(
        total=len(outcomes),
        passed=sum(outcome.passed for outcome in outcomes),
        outcomes=outcomes,
    )

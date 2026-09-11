"""使用真实 LLM 的语义入口。

TurnInterpreter 只把自然语言转换成 Interpretation，不负责查询天气、解析日期、
补默认值或生成方案。这个职责限制让一次 LLM 调用更稳定，也使后续确定性逻辑
可以脱离模型单独测试。
"""

from __future__ import annotations

import os
from time import perf_counter
from datetime import date
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict

from app.domain.constraints import Intent, Interpretation
from app.domain.runtime import RuntimeDecision
from app.services.model_usage import ModelTokenUsage, TokenUsageAccumulator


class StructuredModel(Protocol):
    """Router 所需的最小模型接口；测试可注入 Fake，而不依赖网络。"""
    def invoke(self, messages: list[object]) -> object:
        ...


class RouterContext(BaseModel):
    """允许注入 Prompt 的稳定上下文，不包含天气、POI 等工具结果。"""
    model_config = ConfigDict(extra="forbid")

    current_date: date
    timezone: str = "Asia/Shanghai"
    has_plans: bool = False
    has_selected_plan: bool = False
    previous_intent: Intent | None = None


SYSTEM_PROMPT = """你是本地生活规划系统的语义入口。

你只负责：
1. 识别用户的主要意图和相关意图置信度。
2. 从用户原话中抽取规划约束，保留原始表达。
3. 为抽取结果记录简短证据片段和置信度。
4. 将创建、选择或修改请求写成受 Schema 限制的 conversation_command。

输出要求：只返回一个合法的 JSON 对象，不要返回 Markdown 代码围栏、解释文字或
JSON 对象之外的内容。字段缺失时使用 schema 允许的默认值、null 或空数组；不要
为了凑字段编造信息。

可用意图：plan_outing、find_activity、check_weather、refine_plan、execute_plan、cancel_execution、chitchat。

约束：
- 未明确表达的信息保持为空，不填系统默认值。
 - 不解析相对日期、模糊时间和模糊距离，只保留 date_text、time_text、max_distance_text。
 - “一整天”“全天”“从早到晚”填写 time_scope="all_day"，同时保留 time_text 作为证据。
 - “下午两点半准时出发”要保留 departure_at_text；如果能可靠规范化，也可填写
   departure_at="14:30"，但不要把到家时间写成出发时间。Enrichment 会再次校验时钟。
 - “最晚 18:00 到家”要保留 return_by_text，并在时钟明确时填写 return_by；
   “全程不超过 10 公里”要保留 total_distance_text 并填写 total_distance_km。不要把
   全程距离写入 max_distance_km，后者表示单段/召回距离。
 - 对“只安排一家晚饭”“就吃个晚饭”这类同时表达排他和晚餐的请求，填写
   exact_stop_count=1、required_stop_roles=["dinner"]；分别在 evidence_map 中保留
   exact_stop_count 的排他短语和 required_stop_roles 的晚餐短语。没有排他语义时，
   不要从“想吃晚饭”“晚饭后散步”等表达推断一站行程。
- 不调用工具，不生成地点、价格、库存、路线等事实。
- “餐厅保留，只把活动换近一点”输出 operation=replace，target.role=activity，
  locked_targets 中使用 resource_type=restaurant，constraint_patch.prefer_shorter_travel=true。
  只保留用户语言引用，不猜测 resource_id；对象解析和锁定由 Harness 完成。
- “严格控制预算”“千万别超预算”等表达令 strict_budget=true；只有明确金额才填写 budget_per_person。
- 有儿童时尽量提取 children 和 child_age；不能确定时保持为空。
- 由用户原话间接推断、而非直接陈述的字段写入 inferred_fields；例如从“约会”推断同行人数时写入 party。
- 只有真正闲聊才输出 chitchat。解析不确定不等于闲聊。
"""


class TurnInterpreter:
    """将一轮用户输入转换成通过 Pydantic 校验的 Interpretation。

    首次结构化输出失败时只重试一次；第二次仍失败则返回显式澄清意图，避免
    未校验的字典继续流入 Graph。这里重试的是“输出格式”，不是业务规划。
    """

    def __init__(self, model: StructuredModel, *, model_name: str | None = None) -> None:
        self._model = model
        self._model_name = model_name

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        interpretation, _ = self.interpret_with_runtime(user_input, context)
        return interpretation

    def interpret_with_runtime(
        self,
        user_input: str,
        context: RouterContext,
    ) -> tuple[Interpretation, RuntimeDecision]:
        started_at = perf_counter()
        messages: list[object] = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=self._build_context(user_input, context)),
        ]
        attempts = 0
        token_usage = TokenUsageAccumulator()
        try:
            attempts = 1
            raw_result = self._model.invoke(messages)
            token_usage.record(raw_result)
            interpretation = self._validate(raw_result)
            return interpretation, self._runtime_decision(
                adapter="llm",
                model_invoked=True,
                attempts=attempts,
                latency_ms=_elapsed_ms(started_at),
                token_usage=token_usage.total,
            )
        except Exception as first_error:
            if attempts == 1 and token_usage.attempt_count == 0:
                token_usage.record_unknown()
            # 把第一次校验错误反馈给模型，有助于修复漏字段或类型错误；限制为
            # 一次重试，防止格式错误演变成不可控的模型循环和延迟。
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次输出未通过结构校验。请严格返回合法 JSON 格式的 Interpretation 结构，"
                        f"不要补充解释。校验错误：{first_error}"
                    )
                ),
            ]
            try:
                attempts = 2
                raw_retry = self._model.invoke(retry_messages)
                token_usage.record(raw_retry)
                interpretation = self._validate(raw_retry)
                return interpretation, self._runtime_decision(
                    adapter="llm",
                    model_invoked=True,
                    attempts=attempts,
                    latency_ms=_elapsed_ms(started_at),
                    token_usage=token_usage.total,
                )
            except Exception:
                if token_usage.attempt_count < 2:
                    token_usage.record_unknown()
                return Interpretation(
                    primary_intent=Intent.CLARIFY,
                    intent_scores={Intent.CLARIFY: 1.0},
                    requires_clarification=True,
                    reply="我还不能可靠理解这个需求，请换一种方式重新描述一下。",
                ), self._runtime_decision(
                    adapter="fallback",
                    model_invoked=True,
                    attempts=2,
                    fallback_reason="invalid_output",
                    latency_ms=_elapsed_ms(started_at),
                    token_usage=token_usage.total,
                )

    def _runtime_decision(
        self,
        *,
        adapter: str,
        model_invoked: bool,
        attempts: int,
        fallback_reason: str | None = None,
        latency_ms: int | None = None,
        token_usage: ModelTokenUsage = ModelTokenUsage(),
    ) -> RuntimeDecision:
        return RuntimeDecision(
            stage="turn_interpreter",
            adapter=adapter,
            model_invoked=model_invoked,
            model_name=self._model_name,
            attempts=attempts,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
        )

    @staticmethod
    def _validate(result: object) -> Interpretation:
        if isinstance(result, dict) and (
            "parsed" in result or "parsing_error" in result
        ):
            if result.get("parsing_error") is not None:
                raise ValueError(f"structured interpretation parsing failed: {result['parsing_error']}")
            parsed = result.get("parsed")
            if parsed is None:
                raise ValueError("structured interpretation parser returned no result")
            return TurnInterpreter._validate(parsed)
        if isinstance(result, Interpretation):
            return result
        return Interpretation.model_validate(result)

    @staticmethod
    def _build_context(user_input: str, context: RouterContext) -> str:
        lines = [
            f"当前日期：{context.current_date.isoformat()}",
            f"时区：{context.timezone}",
            f"用户本轮输入：{user_input}",
        ]
        if context.has_plans:
            lines.append("会话中已有候选方案。")
        if context.has_selected_plan:
            lines.append("用户已经显式选择当前版本中的一个方案。")
        if context.previous_intent is not None:
            lines.append(f"上一轮主要意图：{context.previous_intent.value}")
        return "\n".join(lines)


RouterExtractor = TurnInterpreter


def build_default_turn_interpreter() -> TurnInterpreter:
    """根据环境变量创建 OpenAI 兼容的生产 TurnInterpreter。"""
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    llm = ChatOpenAI(
        model=model_name,
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        # Function Calling 会把 Pydantic Schema 作为工具参数契约传给
        # DeepSeek；本地 Pydantic 仍然是最终校验入口。
        max_tokens=2048,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return TurnInterpreter(
        llm.with_structured_output(
            Interpretation,
            method="function_calling",
            include_raw=True,
        ),
        model_name=model_name,
    )


build_default_router_extractor = build_default_turn_interpreter


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))

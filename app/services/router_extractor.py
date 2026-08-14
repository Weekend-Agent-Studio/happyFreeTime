"""使用真实 LLM 的语义入口。

RouterExtractor 只把自然语言转换成 Interpretation，不负责查询天气、解析日期、
补默认值或生成方案。这个职责限制让一次 LLM 调用更稳定，也使后续确定性逻辑
可以脱离模型单独测试。
"""

from __future__ import annotations

import os
from datetime import date
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict

from app.domain.constraints import Intent, Interpretation


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
    previous_intent: Intent | None = None


SYSTEM_PROMPT = """你是本地生活规划系统的语义入口。

你只负责：
1. 识别用户的主要意图和相关意图置信度。
2. 从用户原话中抽取规划约束，保留原始表达。
3. 为抽取结果记录简短证据片段和置信度。
4. 识别方案选择、修改目标和取消目标。

可用意图：plan_outing、find_activity、check_weather、refine_plan、execute_plan、cancel_execution、chitchat。

约束：
- 未明确表达的信息保持为空，不填系统默认值。
- 不解析相对日期、模糊时间和模糊距离，只保留 date_text、time_text、max_distance_text。
- 不调用工具，不生成地点、价格、库存、路线等事实。
- “严格控制预算”“千万别超预算”等表达令 strict_budget=true；只有明确金额才填写 budget_per_person。
- 有儿童时尽量提取 children 和 child_age；不能确定时保持为空。
- 由用户原话间接推断、而非直接陈述的字段写入 inferred_fields；例如从“约会”推断同行人数时写入 party。
- 只有真正闲聊才输出 chitchat。解析不确定不等于闲聊。
"""


class RouterExtractor:
    """将一轮用户输入转换成通过 Pydantic 校验的 Interpretation。

    首次结构化输出失败时只重试一次；第二次仍失败则返回显式澄清意图，避免
    未校验的字典继续流入 Graph。这里重试的是“输出格式”，不是业务规划。
    """

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        messages: list[object] = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=self._build_context(user_input, context)),
        ]
        try:
            return self._validate(self._model.invoke(messages))
        except Exception as first_error:
            # 把第一次校验错误反馈给模型，有助于修复漏字段或类型错误；限制为
            # 一次重试，防止格式错误演变成不可控的模型循环和延迟。
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次输出未通过结构校验。请严格返回 Interpretation 结构，"
                        f"不要补充解释。校验错误：{first_error}"
                    )
                ),
            ]
            try:
                return self._validate(self._model.invoke(retry_messages))
            except Exception:
                return Interpretation(
                    primary_intent=Intent.CLARIFY,
                    intent_scores={Intent.CLARIFY: 1.0},
                    requires_clarification=True,
                    reply="我还不能可靠理解这个需求，请换一种方式重新描述一下。",
                )

    @staticmethod
    def _validate(result: object) -> Interpretation:
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
        if context.previous_intent is not None:
            lines.append(f"上一轮主要意图：{context.previous_intent.value}")
        return "\n".join(lines)


def build_default_router_extractor() -> RouterExtractor:
    """根据环境变量创建 OpenAI 兼容的生产 Router。"""
    llm = ChatOpenAI(
        model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return RouterExtractor(llm.with_structured_output(Interpretation))

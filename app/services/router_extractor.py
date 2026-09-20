"""使用真实 LLM 的语义入口。

TurnInterpreter 只把自然语言转换成 Interpretation，不负责查询天气、解析日期、
补默认值或生成方案。这个职责限制让一次 LLM 调用更稳定，也使后续确定性逻辑
可以脱离模型单独测试。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from datetime import date
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.constraints import (
    Intent,
    Interpretation,
    STRUCTURED_OUTPUT_RULE_CODES,
)
from app.services.llm_compat import thinking_extra_body, structured_output_schema
from app.domain.runtime import RuntimeDecision
from app.services.model_errors import model_failure_reason
from app.services.model_usage import ModelTokenUsage, TokenUsageAccumulator


class StructuredModel(Protocol):
    """Router 所需的最小模型接口；测试可注入 Fake，而不依赖网络。"""
    def invoke(self, messages: list[object]) -> object:
        ...


STRUCTURED_OUTPUT_DIAGNOSTIC_CODES = (
    "missing_tool_call",
    "provider_parsing_error",
    "invalid_json_arguments",
    "pydantic_validation_failed",
    "cross_field_contract_failed",
) + STRUCTURED_OUTPUT_RULE_CODES

_INFERRED_FIELD_DIAGNOSTIC_CODES = frozenset(
    {
        "inferred_value_missing",
        "inferred_evidence_missing",
        "inferred_confidence_missing",
    }
)


@dataclass(frozen=True)
class StructuredOutputDiagnostic:
    """Safe diagnosis of a received structured-output response.

    Only bounded codes, field paths and Pydantic error types are retained.
    Provider payloads, user text and exception messages deliberately stay out
    of this object.
    """

    code: str
    paths: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()


class _StructuredOutputValidationError(ValueError):
    """Internal validation exception carrying only safe diagnostics."""

    def __init__(self, diagnostic: StructuredOutputDiagnostic) -> None:
        super().__init__(diagnostic.code)
        self.diagnostic = diagnostic


class RouterContext(BaseModel):
    """允许注入 Prompt 的稳定上下文，不包含天气、POI 等工具结果。"""
    model_config = ConfigDict(extra="forbid")

    current_date: date
    timezone: str = "Asia/Shanghai"
    has_plans: bool = False
    has_selected_plan: bool = False
    previous_intent: Intent | None = None


DEFAULT_MODEL_TIMEOUT_SECONDS = 15


SYSTEM_PROMPT = """你是本地生活规划系统的语义入口。

你只负责：
1. 识别用户的主要意图和相关意图置信度。
2. 从用户原话中抽取规划约束，保留原始表达。
3. 为抽取结果记录简短证据片段和置信度。
4. 将创建、选择或修改请求写成受 Schema 限制的 conversation_command。

输出要求：只返回一个合法的 JSON 对象，不要返回 Markdown 代码围栏、解释文字或
JSON 对象之外的内容。字段缺失时使用 schema 允许的默认值、null 或空数组；不要
为了凑字段编造信息。

- conversation_command 如需填写，必须是嵌套 JSON 对象而不是 JSON 字符串；其
  operation、target、locked_targets 和 constraint_patch 按 Schema 的对象/数组
  结构填写。没有创建、选择或修改命令时填写 null。

可用意图：plan_outing、find_activity、check_weather、refine_plan、execute_plan、cancel_execution、chitchat。

约束：
- 未明确表达的信息保持为空，不填系统默认值。
 - 不计算最终日历日期，也不猜模糊时间；为可识别的日期表达填写有限的
   date_reference（today/tomorrow/day_after_tomorrow/weekday/absolute），并继续保留
   date_text 作为原文证据。weekday 需要同时填写 weekday；“下周”填写 week_offset=1，
   “本周/这周”填写 week_offset=0，普通“周六”不填写 week_offset。absolute 必须填写
   ISO 格式 absolute_date。无法可靠归类时只保留 date_text，等待 Enrichment/Gate。
 - “今晚”填写 date_reference="today"、time_scope="evening”；“明晚”填写
   date_reference="tomorrow"、time_scope="evening”。不要把“晚上”单独推断成今天。
 - “一整天”“全天”“从早到晚”填写 time_scope="all_day"，同时保留 time_text 作为证据。
 - 明确的数字范围（例如“10:00–16:00”）填写 time_scope="explicit_range" 和
   explicit_time_window={"start":"10:00","end":"16:00"}，并保留 time_text。
 - 任何 date_reference、weekday、week_offset、absolute_date 或
   explicit_time_window 只在能从用户原话确认时填写；对应 evidence_map 和
   extraction_confidence 必须同时提供。模糊的“晚饭前后”“有空时”不能编译成精确时钟。
 - “下午两点半准时出发”要保留 departure_at_text；如果能可靠规范化，也可填写
   departure_at="14:30”，但不要把到家时间写成出发时间。Enrichment 会再次校验时钟。
   如果同时出现“上午/下午/晚上”和精确出发时刻，保留两者；精确时刻优先，不能仅因为
   它超出模糊时段的默认边界就判定冲突。只有用户明确给出时间范围，且精确时刻超出该
   范围时，才由后续 Harness 判定时间冲突。
 - “最晚 18:00 到家”要保留 return_by_text，并在时钟明确时填写 return_by；
   “全程不超过 10 公里”要保留 total_distance_text 并填写 total_distance_km。不要把
   全程距离写入 max_distance_km，后者表示单段/召回距离。
- 对“只安排一家晚饭”“就吃个晚饭”这类同时表达排他和晚餐的请求，填写
  exact_stop_count=1、required_stop_roles=["dinner"]；分别在 evidence_map 中保留
  exact_stop_count 的排他短语和 required_stop_roles 的晚餐短语。没有排他语义时，
  不要从“想吃晚饭”“晚饭后散步”等表达推断一站行程。
- 对“只安排一个活动”“只看一个展，不安排吃饭”这类明确单站活动请求，填写
  exact_stop_count=1、required_stop_roles=["activity"]；对“只安排一顿午饭”“就吃个午餐”
  填写 exact_stop_count=1、required_stop_roles=["lunch"]。只有排他语义明确时才填写
  exact_stop_count，保留
  对应原话证据；不要把普通的“安排活动/吃午饭”误判成单站。
- 当用户明确要求多个角色（例如“安排活动和晚饭”“先逛展再吃午饭”）时，填写
  required_stop_roles，按用户提及顺序保留 activity、lunch、dinner 或 meal；普通
  “安排 A 和 B”没有排他语义时不要填写 exact_stop_count。若用户说“只/仅/就安排
  A 和 B”，这表示恰好这些角色，exact_stop_count 填写为角色数量。dinner/lunch
  是具体饭食角色，后续 StructureCompiler 可以把它们绑定到现有通用 meal 模板，
  不要为此编造新的 POI 或骨架。
- 不调用工具，不生成地点、价格、库存、路线等事实。
- “餐厅保留，只把活动换近一点”输出 operation=replace，target.role=activity，
  locked_targets 中使用 resource_type=restaurant，constraint_patch.prefer_shorter_travel=true。
  只保留用户语言引用，不猜测 resource_id；对象解析和锁定由 Harness 完成。
- “严格控制预算”“千万别超预算”等表达令 strict_budget=true；只有明确金额才填写 budget_per_person。
- 只有用户明确要求“确认有位”“必须可预约”等动态库存确认时，才填写
  require_availability_confirmation=true；普通的“想去某餐厅/景点”保持 false。
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
        attempts = 1
        try:
            raw_result = self._model.invoke(messages)
            token_usage.record(raw_result)
        except Exception as error:
            token_usage.record_unknown()
            return self._clarification_with_runtime(
                started_at=started_at,
                attempts=attempts,
                fallback_reason=model_failure_reason(error),
                token_usage=token_usage.total,
            )

        try:
            interpretation = self._validate(raw_result)
        except Exception as error:
            diagnostic = _diagnostic_from_exception(error, raw_result)
            # Only a response that was received but failed local decoding gets
            # one format-repair attempt.  Network/auth/rate-limit failures are
            # handled above and are never retried here.
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次输出未通过结构校验。请严格返回合法 JSON 格式的 Interpretation 结构，"
                        "不要补充解释。错误类型：invalid_output；"
                        + _repair_hint(diagnostic)
                    )
                ),
            ]
            attempts = 2
            try:
                raw_retry = self._model.invoke(retry_messages)
                token_usage.record(raw_retry)
            except Exception as error:
                if token_usage.attempt_count < 2:
                    token_usage.record_unknown()
                return self._clarification_with_runtime(
                    started_at=started_at,
                    attempts=attempts,
                    fallback_reason=model_failure_reason(error),
                    token_usage=token_usage.total,
                )
            try:
                interpretation = self._validate(raw_retry)
            except Exception as error:
                if token_usage.attempt_count < 2:
                    token_usage.record_unknown()
                diagnostic = _diagnostic_from_exception(error, raw_retry)
                return self._clarification_with_runtime(
                    started_at=started_at,
                    attempts=attempts,
                    fallback_reason="invalid_output",
                    token_usage=token_usage.total,
                    diagnostic=diagnostic,
                )

        return interpretation, self._runtime_decision(
            adapter="llm",
            model_invoked=True,
            attempts=attempts,
            latency_ms=_elapsed_ms(started_at),
            token_usage=token_usage.total,
        )

    def _clarification_with_runtime(
        self,
        *,
        started_at: float,
        attempts: int,
        fallback_reason: str,
        token_usage: ModelTokenUsage,
        diagnostic: StructuredOutputDiagnostic | None = None,
    ) -> tuple[Interpretation, RuntimeDecision]:
        return (
            Interpretation(
                primary_intent=Intent.CLARIFY,
                intent_scores={Intent.CLARIFY: 1.0},
                requires_clarification=True,
                reply="我还不能可靠理解这个需求，请换一种方式重新描述一下。",
            ),
            self._runtime_decision(
                adapter="fallback",
                model_invoked=True,
                attempts=attempts,
                fallback_reason=fallback_reason,
                latency_ms=_elapsed_ms(started_at),
                token_usage=token_usage,
                diagnostic=diagnostic,
            ),
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
        diagnostic: StructuredOutputDiagnostic | None = None,
    ) -> RuntimeDecision:
        return RuntimeDecision(
            stage="turn_interpreter",
            adapter=adapter,
            model_invoked=model_invoked,
            model_name=self._model_name,
            attempts=attempts,
            fallback_reason=fallback_reason,
            diagnostic_code=diagnostic.code if diagnostic is not None else None,
            diagnostic_paths=diagnostic.paths if diagnostic is not None else (),
            diagnostic_error_types=(
                diagnostic.error_types if diagnostic is not None else ()
            ),
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
                raise _StructuredOutputValidationError(
                    _diagnose_structured_response(
                        result.get("raw"), result.get("parsing_error")
                    )
                )
            parsed = result.get("parsed")
            if parsed is None:
                raise _StructuredOutputValidationError(
                    _diagnose_structured_response(result.get("raw"), None)
                )
            return TurnInterpreter._validate(parsed)
        if isinstance(result, Interpretation):
            return result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError as error:
                raise _StructuredOutputValidationError(
                    StructuredOutputDiagnostic(
                        code="invalid_json_arguments",
                        error_types=("json_invalid",),
                    )
                ) from error
        result = _normalize_interpretation_wire_value(result)
        try:
            return Interpretation.model_validate(result)
        except ValidationError as error:
            raise _StructuredOutputValidationError(
                _diagnose_pydantic_validation(error)
            ) from error

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


def classify_structured_output_failure(
    result: object,
    error: BaseException | None = None,
) -> StructuredOutputDiagnostic:
    """Classify one received structured-output failure without exposing it.

    The helper is shared by the bounded diagnostic runner and the production
    TurnInterpreter.  It intentionally returns only fixed categories, safe
    field paths and Pydantic error types.
    """

    return _diagnostic_from_exception(error, result)


def _diagnostic_from_exception(
    error: BaseException | None,
    result: object,
) -> StructuredOutputDiagnostic:
    if isinstance(error, _StructuredOutputValidationError):
        return error.diagnostic
    validation_error = _find_validation_error(error)
    if validation_error is not None:
        return _diagnose_pydantic_validation(validation_error)
    return _diagnose_structured_response(
        _raw_response(result),
        error,
    )


def _diagnose_structured_response(
    raw: object,
    error: BaseException | None,
) -> StructuredOutputDiagnostic:
    validation_error = _find_validation_error(error)
    if validation_error is not None:
        return _diagnose_pydantic_validation(validation_error)
    if _has_invalid_tool_calls(raw) or _looks_like_json_argument_error(error):
        return StructuredOutputDiagnostic(
            code="invalid_json_arguments",
            error_types=("json_arguments_invalid",),
        )
    if raw is None:
        return StructuredOutputDiagnostic(code="provider_parsing_error")
    if not _has_tool_calls(raw):
        return StructuredOutputDiagnostic(code="missing_tool_call")
    return StructuredOutputDiagnostic(code="provider_parsing_error")


def _diagnose_pydantic_validation(
    error: ValidationError,
) -> StructuredOutputDiagnostic:
    try:
        entries = error.errors(include_url=False, include_context=True)
    except TypeError:  # pragma: no cover - compatibility with older Pydantic
        entries = error.errors()
    paths: list[str] = []
    error_types: list[str] = []
    rule_codes: list[str] = []
    cross_field = False
    for entry in entries[:8]:
        loc = entry.get("loc", ())
        error_type = _safe_diagnostic_token(entry.get("type"), "validation_error")
        path = _safe_inferred_field_path(entry, error_type) or _safe_error_path(loc)
        if path not in paths:
            paths.append(path)
        if error_type not in error_types:
            error_types.append(error_type)
        if error_type in STRUCTURED_OUTPUT_RULE_CODES and error_type not in rule_codes:
            rule_codes.append(error_type)
        if not loc or (error_type == "value_error" and len(loc) <= 1):
            cross_field = True
    return StructuredOutputDiagnostic(
        code=(
            rule_codes[0]
            if rule_codes
            else "cross_field_contract_failed"
            if cross_field
            else "pydantic_validation_failed"
        ),
        paths=tuple(paths),
        error_types=tuple(error_types),
    )


def _safe_inferred_field_path(entry: Mapping[str, object], error_type: str) -> str | None:
    """Expose only a bounded inferred-field name from a model-level error."""

    if error_type not in _INFERRED_FIELD_DIAGNOSTIC_CODES:
        return None
    context = entry.get("ctx")
    if not isinstance(context, Mapping):
        return None
    field = context.get("field")
    if not isinstance(field, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field):
        return None
    return f"inferred_fields.{field}"


def _normalize_interpretation_wire_value(result: object) -> object:
    """Normalize one provider quirk without widening the domain contract.

    Some OpenAI-compatible providers serialize the nested command object as a
    JSON string even though the outer tool arguments are an object.  Decode
    exactly that field once; the resulting value still goes through the
    unchanged Interpretation and ConversationCommand validators.
    Plain text, malformed JSON and non-object JSON remain invalid.
    """

    if not isinstance(result, Mapping):
        return result
    command = result.get("conversation_command")
    if not isinstance(command, str):
        return result
    try:
        decoded = json.loads(command)
    except json.JSONDecodeError as error:
        raise _StructuredOutputValidationError(
            StructuredOutputDiagnostic(
                code="invalid_json_arguments",
                paths=("conversation_command",),
                error_types=("json_invalid",),
            )
        ) from error
    if decoded is not None and not isinstance(decoded, Mapping):
        return result
    normalized = dict(result)
    normalized["conversation_command"] = decoded
    return normalized


def _find_validation_error(error: BaseException | None) -> ValidationError | None:
    """Find only chained Pydantic errors; never inspect provider payloads."""

    seen: set[int] = set()
    current = error
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _raw_response(result: object) -> object:
    if isinstance(result, Mapping) and "raw" in result:
        return result.get("raw")
    return result


def _has_tool_calls(raw: object) -> bool:
    value = _safe_mapping_or_attr(raw, "tool_calls")
    if isinstance(value, (list, tuple)) and value:
        return True
    additional = _safe_mapping_or_attr(raw, "additional_kwargs")
    if isinstance(additional, Mapping):
        value = additional.get("tool_calls")
        return isinstance(value, (list, tuple)) and bool(value)
    return False


def _has_invalid_tool_calls(raw: object) -> bool:
    value = _safe_mapping_or_attr(raw, "invalid_tool_calls")
    if isinstance(value, (list, tuple)) and value:
        return True
    additional = _safe_mapping_or_attr(raw, "additional_kwargs")
    if isinstance(additional, Mapping):
        value = additional.get("invalid_tool_calls")
        return isinstance(value, (list, tuple)) and bool(value)
    return False


def _safe_mapping_or_attr(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _looks_like_json_argument_error(error: BaseException | None) -> bool:
    if error is None:
        return False
    name = type(error).__name__.casefold()
    try:
        text = str(error).casefold()[:256]
    except Exception:
        text = ""
    return any(
        marker in name or marker in text
        for marker in ("jsondecode", "json decode", "invalid json", "tool argument")
    )


def _safe_error_path(loc: object) -> str:
    if not isinstance(loc, (list, tuple)) or not loc:
        return "$"
    parts: list[str] = []
    for item in loc[:8]:
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            token = str(item)
        else:
            token = "field"
        token = re.sub(r"[^A-Za-z0-9_.-]", "_", token)[:48] or "field"
        parts.append(token)
    return ".".join(parts)[:160]


def _safe_diagnostic_token(value: object, fallback: str) -> str:
    token = value if isinstance(value, str) else fallback
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", token).strip("._")
    return (token or fallback)[:80]


def _repair_hint(diagnostic: StructuredOutputDiagnostic) -> str:
    """Build a repair hint from a fixed code and safe field paths only."""

    parts = [f"诊断码={_safe_diagnostic_token(diagnostic.code, 'invalid_output')}"]
    if diagnostic.paths:
        parts.append("字段路径=" + ",".join(diagnostic.paths[:8]))
    return "；".join(parts)


RouterExtractor = TurnInterpreter


def build_default_turn_interpreter() -> TurnInterpreter:
    """根据环境变量创建 OpenAI 兼容的生产 TurnInterpreter。"""
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    llm = ChatOpenAI(
        model=model_name,
        api_key=os.getenv("LLM_API"),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        timeout=_model_timeout_seconds(),
        max_retries=0,
        # Function Calling 会把 Pydantic Schema 作为工具参数契约传给
        # OpenAI-compatible provider；本地 Pydantic 仍然是最终校验入口。
        max_tokens=2048,
        extra_body=thinking_extra_body(model_name),
    )
    return TurnInterpreter(
        llm.with_structured_output(
            structured_output_schema(model_name, Interpretation),
            method="function_calling",
            include_raw=True,
        ),
        model_name=model_name,
    )


build_default_router_extractor = build_default_turn_interpreter


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _model_timeout_seconds() -> float:
    raw_value = os.getenv(
        "HFT_ROUTER_TIMEOUT_SECONDS",
        str(DEFAULT_MODEL_TIMEOUT_SECONDS),
    )
    try:
        timeout = float(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "HFT_ROUTER_TIMEOUT_SECONDS must be a positive number"
        ) from error
    if timeout <= 0 or timeout != timeout or timeout in (float("inf"), float("-inf")):
        raise RuntimeError(
            "HFT_ROUTER_TIMEOUT_SECONDS must be a positive number"
        )
    return timeout


_model_failure_reason = model_failure_reason

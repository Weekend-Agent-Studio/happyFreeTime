"""使用真实 LLM 的语义入口。

TurnInterpreter 只把自然语言转换成受限的 ``TurnProposal``，再由确定性的
``TurnCompiler`` 投影为领域 Interpretation 和 CompiledNextAction；它不负责查询
天气、解析日期、补默认值或生成方案。这个职责限制让一次 LLM 调用更稳定，也使
后续确定性逻辑可以脱离模型单独测试。
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
    UserActKind,
)
from app.services.llm_compat import thinking_extra_body, structured_output_schema
from app.domain.runtime import RuntimeDecision
from app.domain.decision_context import DecisionContext
from app.domain.turn import (
    CompiledNextAction,
    NeedsClarification,
    NoAction,
    TurnCompilation,
    TurnCompiler,
    TurnProposal,
)
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
    "action_not_allowed",
) + STRUCTURED_OUTPUT_RULE_CODES

_INFERRED_FIELD_DIAGNOSTIC_CODES = frozenset(
    {
        "inferred_value_missing",
        "inferred_evidence_missing",
        "inferred_confidence_missing",
    }
)

WIRE_SCHEMA_VERSION = "turn-proposal.v2"
PROMPT_VERSION = "turn-interpreter.v4"


@dataclass(frozen=True)
class TurnInterpreterResult:
    """One interpreted turn plus its compiled action and safe diagnostics.

    ``__iter__`` keeps the existing two-value observation seam usable by
    offline evaluators while the Graph consumes the explicit ``action`` field.
    """

    interpretation: Interpretation
    action: CompiledNextAction
    runtime: RuntimeDecision

    def __iter__(self):
        yield self.interpretation
        yield self.runtime


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
    previous_user_act: UserActKind | None = None
    # Bounded session projection for semantic reference resolution. Existing
    # adapters may omit it while they use the older boolean context fields.
    decision_context: DecisionContext | None = None


DEFAULT_MODEL_TIMEOUT_SECONDS = 15


SYSTEM_PROMPT = """你是本地生活规划系统的语义入口。

只输出一个 TurnProposal，必须包含 act.kind，kind 只能是：create_plan、
patch_constraints、replace_stop、check_weather、query_plan、chitchat。kind 是本轮
唯一动作判别字段；不要输出 primary_intent、refine_plan、operation、conversation_command、
intent_scores、confidence、reply、方案/版本/资源 ID。

create_plan 填写 raw_constraints、time_proposals、evidence_map；patch_constraints 只
填写本轮新增或清除的 constraint_patch；replace_stop 填写 target、可选 locked_targets、
replacement_criteria 和 evidence；check_weather 保留天气及可能触发规划的条件；query_plan
填写原文 query；chitchat 不添加业务事实。

未明确表达的信息保持为空，不填默认值，不调用工具，不生成地点、价格、库存或路线事实。
日期和时间必须保留原文证据：date_reference 只在原文支持时填写；时间只写入
带 kind 的 time_proposals 判别结构：trip_range 只能有 start/end；event_clock 只能有
event（departure/return）和 clock；period 只能有 event（trip/departure/return/activity）和
period（morning/afternoon/evening/all_day）。不要混用这些结构，也不要输出无关字段。
“早上出去玩”是 period(event=trip, period=morning)；“早上出发”是
period(event=departure, period=morning)；“早上九点出发”是
event_clock(event=departure, clock=09:00)；“晚上八点前回来”是
event_clock(event=return, clock=20:00)；“今晚/明晚”分别对应 today/tomorrow 加
period(event=trip, period=evening)；“一整天/全天”对应 period(event=trip, period=all_day)。
“10:00–16:00”使用 trip_range(start=10:00, end=16:00)。同一段 evidence 不得同时
解释为 trip 和 departure 两个作用域。

“下午去公园/下午安排一个活动”使用 period(event=activity, period=afternoon)，不要把它
写成 trip 时间窗或 departure；只有“下午出去玩”才是 trip。已有方案的结构更新写入
constraint_patch.exact_stop_count、required_stop_roles 或 add_required_stop_roles；“不用限制
站数了”使用 clear_structure=true；“多安排几个地方”没有具体数字时保留
structure_hint_text，系统会反问具体站数，不要猜一个数字。

保留用户明确的站数、角色、距离、预算、同行人、偏好、饮食、场景和避开条件，并用
evidence_map 记录需要追溯的字段。地点必须区分作用域：从某处出发填写
raw_constraints.origin_text；希望在某区域活动填写 planning_area_text；“就在北京安排”
不能静默变成精确出发点。普通“约会”不推断成人数量；严格预算不编造金额。
距离表达保留原文，不把“附近/别太远”改写成用户未说过的精确路线事实。
已有方案时，预算、返程时间、少辣、安静等补充使用 patch_constraints；只有明确替换某站
时使用 replace_stop。模糊目标可以只填 raw_text，系统会安全反问。
"""


class TurnInterpreter:
    """将一轮用户输入转换成校验过的 TurnProposal 和执行动作。

    首次结构化输出失败时只重试一次；第二次仍失败则返回显式澄清意图，避免
    未校验的字典继续流入 Graph。这里重试的是“输出格式”，不是业务规划。
    """

    def __init__(self, model: StructuredModel, *, model_name: str | None = None) -> None:
        self._model = model
        self._model_name = model_name

    def interpret_with_runtime(
        self,
        user_input: str,
        context: RouterContext,
    ) -> TurnInterpreterResult:
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
            compilation, compile_diagnostic = self._validate_with_diagnostic(
                raw_result,
                decision_context=context.decision_context,
            )
            if compile_diagnostic is not None and compile_diagnostic.code == "action_not_allowed":
                return self._repair_disallowed_action(
                    context=context,
                    messages=messages,
                    initial=compilation,
                    started_at=started_at,
                    token_usage=token_usage,
                )
        except Exception as error:
            diagnostic = _diagnostic_from_exception(error, raw_result)
            # Only a response that was received but failed local decoding gets
            # one format-repair attempt.  Network/auth/rate-limit failures are
            # handled above and are never retried here.
            retry_messages = [
                *messages,
                HumanMessage(
                    content=(
                        "上一次输出未通过结构校验。请严格返回合法 JSON 格式的 TurnProposal 结构，"
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
                    request_rephrase=True,
                )
            try:
                compilation, compile_diagnostic = self._validate_with_diagnostic(
                    raw_retry,
                    decision_context=context.decision_context,
                )
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
                    request_rephrase=True,
                )

        runtime = self._runtime_decision(
            adapter="llm",
            model_invoked=True,
            attempts=attempts,
            diagnostic=compile_diagnostic,
            latency_ms=_elapsed_ms(started_at),
            token_usage=token_usage.total,
        )
        return TurnInterpreterResult(
            interpretation=compilation.interpretation,
            action=compilation.action,
            runtime=runtime,
        )

    def _repair_disallowed_action(
        self,
        *,
        context: RouterContext,
        messages: list[object],
        initial: TurnCompilation,
        started_at: float,
        token_usage: TokenUsageAccumulator,
    ) -> TurnInterpreterResult:
        """Give an empty session one bounded chance to change patch to create.

        This is an action repair, not a silent conversion: the second model
        output must explicitly carry ``create_plan``.  If it does not, the
        compiled result remains an explicit action clarification and never
        enters the patch workflow.
        """

        retry_messages = [
            *messages,
            HumanMessage(
                content=(
                    "当前会话还没有已建立的规划请求，patch_constraints 在这里不允许。"
                    "请只根据用户原话重新输出 TurnProposal：如果用户是在提出新的规划需求，"
                    "必须改用 kind=create_plan；不要把 patch_constraints 静默转换成 create_plan。"
                )
            ),
        ]
        try:
            raw_retry = self._model.invoke(retry_messages)
            token_usage.record(raw_retry)
        except Exception as error:
            if token_usage.attempt_count < 2:
                token_usage.record_unknown()
            return self._action_clarification_with_runtime(
                initial=initial,
                started_at=started_at,
                attempts=2,
                fallback_reason=model_failure_reason(error),
                token_usage=token_usage.total,
            )

        try:
            compilation, diagnostic = self._validate_with_diagnostic(
                raw_retry,
                decision_context=context.decision_context,
            )
        except Exception:
            return self._action_clarification_with_runtime(
                initial=initial,
                started_at=started_at,
                attempts=2,
                fallback_reason="action_not_allowed",
                token_usage=token_usage.total,
            )
        if diagnostic is not None and diagnostic.code == "action_not_allowed":
            return self._action_clarification_with_runtime(
                initial=compilation,
                started_at=started_at,
                attempts=2,
                fallback_reason="action_not_allowed",
                token_usage=token_usage.total,
            )

        return TurnInterpreterResult(
            interpretation=compilation.interpretation,
            action=compilation.action,
            runtime=self._runtime_decision(
                adapter="llm",
                model_invoked=True,
                attempts=2,
                diagnostic=diagnostic,
                latency_ms=_elapsed_ms(started_at),
                token_usage=token_usage.total,
            ),
        )

    def _action_clarification_with_runtime(
        self,
        *,
        initial: TurnCompilation,
        started_at: float,
        attempts: int,
        fallback_reason: str,
        token_usage: ModelTokenUsage,
    ) -> TurnInterpreterResult:
        interpretation = initial.interpretation.model_copy(
            update={
                "primary_intent": Intent.CLARIFY,
                "intent_scores": {Intent.CLARIFY: 1.0},
                "requires_clarification": True,
                "reply": "当前还没有可修改的规划请求，请直接描述你想规划的活动。",
            }
        )
        action = NeedsClarification(
            field="request_lifecycle",
            issue_kind="action",
            raw_text="patch_constraints",
        )
        diagnostic = StructuredOutputDiagnostic(
            code="action_not_allowed",
            paths=("act.kind",),
            error_types=("request_lifecycle_empty",),
        )
        return TurnInterpreterResult(
            interpretation=interpretation,
            action=action,
            runtime=self._runtime_decision(
                adapter="fallback",
                model_invoked=True,
                attempts=attempts,
                fallback_reason=fallback_reason,
                latency_ms=_elapsed_ms(started_at),
                token_usage=token_usage,
                diagnostic=diagnostic,
            ),
        )

    def _clarification_with_runtime(
        self,
        *,
        started_at: float,
        attempts: int,
        fallback_reason: str,
        token_usage: ModelTokenUsage,
        diagnostic: StructuredOutputDiagnostic | None = None,
        request_rephrase: bool = False,
    ) -> TurnInterpreterResult:
        action: CompiledNextAction = (
            NeedsClarification(
                field="request_rephrase",
                issue_kind="constraint",
                raw_text="structured_output",
            )
            if request_rephrase
            else NoAction(reason="unsupported")
        )
        interpretation = Interpretation(
            primary_intent=Intent.CLARIFY,
            intent_scores={Intent.CLARIFY: 1.0},
            requires_clarification=True,
            reply=(
                "我没能可靠识别你的时间或规划条件，请换一种方式描述，例如“周六 10:00 到 16:00”。"
                if request_rephrase
                else "我还不能可靠理解这个需求，请换一种方式重新描述一下。"
            ),
        )
        return TurnInterpreterResult(
            interpretation=interpretation,
            action=action,
            runtime=self._runtime_decision(
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
            wire_schema_version=(WIRE_SCHEMA_VERSION if model_invoked else None),
            prompt_version=(PROMPT_VERSION if model_invoked else None),
            latency_ms=latency_ms,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
        )

    @staticmethod
    def _validate(result: object) -> Interpretation:
        compilation, _ = TurnInterpreter._validate_with_diagnostic(result)
        return compilation.interpretation

    @staticmethod
    def _validate_with_diagnostic(
        result: object,
        *,
        decision_context: DecisionContext | None = None,
    ) -> tuple[TurnCompilation, StructuredOutputDiagnostic | None]:
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
            return TurnInterpreter._validate_with_diagnostic(
                parsed,
                decision_context=decision_context,
            )
        if isinstance(result, TurnProposal):
            compilation = TurnCompiler.compile(
                result,
                context=decision_context,
            )
            return compilation, _compilation_diagnostic(compilation)
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
        result = _normalize_turn_wire_value(result)
        try:
            proposal = TurnProposal.model_validate(result)
        except ValidationError as error:
            raise _StructuredOutputValidationError(
                _diagnose_pydantic_validation(error)
            ) from error
        compilation = TurnCompiler.compile(
            proposal,
            context=decision_context,
        )
        return compilation, _compilation_diagnostic(compilation)

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
        if context.previous_user_act is not None:
            lines.append(f"上一轮用户动作：{context.previous_user_act}")
        if context.decision_context is not None:
            lines.extend(context.decision_context.prompt_lines())
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


def _compilation_diagnostic(
    compilation: TurnCompilation,
) -> StructuredOutputDiagnostic | None:
    action = compilation.action
    if not isinstance(action, NeedsClarification):
        return None
    if action.issue_kind == "action":
        return StructuredOutputDiagnostic(
            code="action_not_allowed",
            paths=("act.kind",),
            error_types=("request_lifecycle_empty",),
        )
    if action.field == "target_reference":
        return StructuredOutputDiagnostic(
            code="target_resolution_required",
            paths=("act.target",),
            error_types=("target_not_unique",),
        )
    return None


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


def _normalize_turn_wire_value(result: object) -> object:
    """Normalize one provider quirk without widening the domain contract."""

    if not isinstance(result, Mapping):
        return result
    act = result.get("act")
    if not isinstance(act, str):
        return result
    try:
        decoded = json.loads(act)
    except json.JSONDecodeError as error:
        raise _StructuredOutputValidationError(
            StructuredOutputDiagnostic(
                code="invalid_json_arguments",
                paths=("act",),
                error_types=("json_invalid",),
            )
        ) from error
    if decoded is not None and not isinstance(decoded, Mapping):
        return result
    normalized = dict(result)
    normalized["act"] = decoded
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
            structured_output_schema(model_name, TurnProposal),
            method="function_calling",
            include_raw=True,
        ),
        model_name=model_name,
    )


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

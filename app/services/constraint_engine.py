"""Pure, atomic updates for the active planning request."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.fields import FieldInfo

from app.domain.constraints import (
    ClarificationIssue,
    ConstraintValue,
    PlanRequest,
    RequestPatch,
)
from app.domain.planning import ConstraintConflict


class ResolvedRequest(BaseModel):
    """A validated request snapshot produced by applying one complete patch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["resolved"] = "resolved"
    request: PlanRequest
    changed_fields: tuple[str, ...] = ()


class NeedsClarification(BaseModel):
    """The patch cannot be committed until the user supplies one required value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["needs_clarification"] = "needs_clarification"
    issue: ClarificationIssue


class ConflictedRequest(BaseModel):
    """The patch is stale, invalid, or contradicts another hard constraint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["conflict"] = "conflict"
    conflict: ConstraintConflict


ConstraintEngineResult = Annotated[
    ResolvedRequest | NeedsClarification | ConflictedRequest,
    Field(discriminator="status"),
]


class ConstraintEngine:
    """Apply normalized ``RequestPatch`` values without partial mutation or I/O.

    The engine owns revision checks, set/clear semantics, provenance projection
    for the existing ``ConstraintValue`` fields, schema validation, and the
    cross-field rules that are already unambiguous in the current request
    contract. Natural-language parsing and question wording belong elsewhere.
    """

    def apply(
        self,
        request: PlanRequest,
        patch: RequestPatch,
        *,
        issues: tuple[ClarificationIssue, ...] = (),
    ) -> ConstraintEngineResult:
        if patch.base_revision != request.revision:
            return ConflictedRequest(
                conflict=ConstraintConflict(
                    code="STALE_REQUEST_REVISION",
                    message="请求已更新，请基于最新条件重新提交修改。",
                    fields=["revision"],
                )
            )

        request_fields = PlanRequest.model_fields
        changed: dict[str, object] = {}
        unknown_fields = sorted(
            (set(patch.set_fields) | set(patch.clear_fields)) - (set(request_fields) - {"revision"})
        )
        if unknown_fields:
            return ConflictedRequest(
                conflict=ConstraintConflict(
                    code="UNKNOWN_REQUEST_FIELD",
                    message="更新包含不支持的请求字段。",
                    fields=unknown_fields,
                )
            )

        unsupported_evidence = sorted(
            name
            for name in patch.evidence
            if not _is_constraint_value_field(request_fields[name].annotation)
        )
        if unsupported_evidence:
            return ConflictedRequest(
                conflict=ConstraintConflict(
                    code="FIELD_PROVENANCE_UNSUPPORTED",
                    message="这些字段尚无可保存字段来源和原文证据的领域表示。",
                    fields=unsupported_evidence,
                )
            )

        if issues:
            return NeedsClarification(
                issue=issues[0].model_copy(
                    update={"request_revision": request.revision}
                )
            )

        for name, proposed_value in patch.set_fields.items():
            field = request_fields[name]
            wrapped = _is_constraint_value_field(field.annotation)
            if wrapped:
                proposed_wrapper = (
                    proposed_value if isinstance(proposed_value, ConstraintValue) else None
                )
                value = (
                    proposed_wrapper.value
                    if proposed_wrapper is not None
                    else proposed_value
                )
                value = ConstraintValue(
                    value=value,
                    source=patch.field_sources.get(
                        name,
                        proposed_wrapper.source if proposed_wrapper is not None else patch.source,
                    ),
                    raw_text=(
                        patch.evidence.get(name)
                        or (proposed_wrapper.raw_text if proposed_wrapper is not None else None)
                    ),
                    confidence=(
                        proposed_wrapper.confidence
                        if proposed_wrapper is not None
                        else None
                    ),
                    rule_id=(
                        proposed_wrapper.rule_id
                        if proposed_wrapper is not None and proposed_wrapper.rule_id
                        else f"request_patch.{name}.v1"
                    ),
                )
            else:
                value = proposed_value
            current = getattr(request, name)
            if not _same_request_value(current, value, wrapped=wrapped):
                changed[name] = value

        for name in patch.clear_fields:
            field = request_fields[name]
            default = _field_default(field)
            if default is _MISSING:
                return ConflictedRequest(
                    conflict=ConstraintConflict(
                        code="REQUEST_FIELD_NOT_CLEARABLE",
                        message="请求字段不能清除。",
                        fields=[name],
                    )
                )
            if getattr(request, name) != default:
                changed[name] = default

        # A patch that repeats the current values is a true no-op: neither its
        # revision nor downstream plan state should change. It still passes
        # through invariant checks below so an already-incomplete request can
        # never be reported as executable merely because the patch was empty.
        if changed:
            candidate_data = request.model_dump(mode="python")
            candidate_data.update(changed)
            candidate_data["revision"] = request.revision + 1
            try:
                candidate = PlanRequest.model_validate(candidate_data)
            except ValidationError as error:
                paths = _validation_paths(error)
                return ConflictedRequest(
                    conflict=ConstraintConflict(
                        code="INVALID_REQUEST_PATCH",
                        message="更新值不符合请求字段的类型或取值范围。",
                        fields=paths,
                    )
                )
        else:
            candidate = request

        cross_field_result = self._validate_cross_fields(candidate)
        if cross_field_result is not None:
            return cross_field_result

        if candidate.strict_budget and candidate.budget_per_person is None:
            return NeedsClarification(
                issue=ClarificationIssue(
                    field="budget_per_person",
                    code="STRICT_BUDGET_AMOUNT_REQUIRED",
                    reason="strict_budget_requires_amount",
                    expected_value_type="integer",
                    allow_free_text=True,
                    request_revision=request.revision,
                )
            )

        return ResolvedRequest(
            request=candidate,
            changed_fields=tuple(sorted(changed)),
        )

    @staticmethod
    def _validate_cross_fields(request: PlanRequest) -> ConflictedRequest | None:
        window = request.planning_window
        start_at = window.start_at.value if window.start_at else None
        end_at = window.end_at.value if window.end_at else None
        if start_at is not None and end_at is not None and start_at >= end_at:
            departure_return_conflict = (
                window.explicit_departure is not None
                and window.explicit_return_deadline is not None
            )
            return ConflictedRequest(
                conflict=ConstraintConflict(
                    code=(
                        "DEPARTURE_NOT_BEFORE_RETURN_BY"
                        if departure_return_conflict
                        else "INVALID_TIME_WINDOW_ORDER"
                    ),
                    message=(
                        "出发时间必须早于返程时间。"
                        if departure_return_conflict
                        else "行程开始时间必须早于结束时间。"
                    ),
                    fields=(
                        ["departure_at", "return_by"]
                        if departure_return_conflict
                        else ["planning_window.start_at", "planning_window.end_at"]
                    ),
                    relaxation_options=(
                        ["提前出发", "延后最晚到家时间"]
                        if departure_return_conflict
                        else ["调整行程开始或结束时间"]
                    ),
                )
            )
        return None


_MISSING = object()


def _field_default(field: FieldInfo) -> object:
    if field.default_factory is not None:
        return field.default_factory()
    if not field.is_required():
        return deepcopy(field.default)
    if _allows_none(field.annotation):
        return None
    return _MISSING


def _is_constraint_value_field(annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin is ConstraintValue:
        return True
    if isinstance(annotation, type) and issubclass(annotation, ConstraintValue):
        return True
    return any(
        argument is not type(None) and _is_constraint_value_field(argument)
        for argument in get_args(annotation)
    )


def _allows_none(annotation: object) -> bool:
    return type(None) in get_args(annotation)


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _same_request_value(current: object, proposed: object, *, wrapped: bool) -> bool:
    if not wrapped:
        return current == proposed
    if not isinstance(current, ConstraintValue) or not isinstance(proposed, ConstraintValue):
        return current == proposed
    # Confidence and rule IDs are implementation metadata. Repeating the same
    # user-visible value with the same source/evidence is a no-op even if the
    # previous compiler assigned a different rule identifier.
    return (
        current.value == proposed.value
        and current.source == proposed.source
        and current.raw_text == proposed.raw_text
    )


def _validation_paths(error: ValidationError) -> list[str]:
    paths = {
        ".".join(str(part) for part in item["loc"] if part is not None) or "$"
        for item in error.errors()
    }
    return sorted(paths)

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

    def apply(self, request: PlanRequest, patch: RequestPatch) -> ConstraintEngineResult:
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

        for name, proposed_value in patch.set_fields.items():
            field = request_fields[name]
            value = proposed_value.value if isinstance(proposed_value, ConstraintValue) else proposed_value
            wrapped = _is_constraint_value_field(field.annotation)
            if wrapped:
                value = ConstraintValue(
                    value=value,
                    source=patch.source,
                    raw_text=patch.evidence.get(name),
                    rule_id=f"request_patch.{name}.v1",
                )
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
        departure = request.departure_at.value if request.departure_at else None
        return_by = request.return_by.value if request.return_by else None
        if departure is not None and return_by is not None and departure >= return_by:
            return ConflictedRequest(
                conflict=ConstraintConflict(
                    code="DEPARTURE_NOT_BEFORE_RETURN_BY",
                    message="准点出发时间必须早于最晚到家时间。",
                    fields=["departure_at", "return_by"],
                    relaxation_options=["提前出发", "延后最晚到家时间"],
                )
            )

        time_window = request.time_window.value if request.time_window else None
        if (
            time_window is not None
            and _clock_minutes(time_window.start) >= _clock_minutes(time_window.end)
        ):
            return ConflictedRequest(
                conflict=ConstraintConflict(
                    code="INVALID_TIME_WINDOW_ORDER",
                    message="规划时间范围的结束时间必须晚于开始时间。",
                    fields=["time_window.start", "time_window.end"],
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

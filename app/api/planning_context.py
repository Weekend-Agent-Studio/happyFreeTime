"""Application seam for the five-field planning context editor.

The HTTP composition root should not know how a Where/When/Who/Budget/
Preferences edit becomes a domain ``RequestPatch``.  This module owns that
projection and the closed UI DTO normalization while keeping the mutation
itself in ``ConstraintEngine``.

Natural-language proposals and clarification answers have separate adapters;
they may reuse the same domain normalizers, but the UI never goes through the
Router and this module never mutates a ``PlanRequest``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.api.schemas import (
    PlanningContextField,
    PlanningContextPatch,
    PlanningContextSummary,
    PlanningContextWhen,
)
from app.domain.constraints import (
    ClarificationIssue,
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    PartyProfile,
    PlanRequest,
    RequestPatch,
)
from app.domain.providers import GeocodeRequest, GeocodeResolution
from app.providers.geocoding import GeocodingProvider
from app.services.enrichment import EnvironmentContext
from app.services.request_readiness import RequestReadinessPolicy


@dataclass(frozen=True)
class PlanningContextEditResult:
    """Normalized UI edit plus issues that ConstraintEngine must surface."""

    patch: RequestPatch
    issues: tuple[ClarificationIssue, ...] = ()


class PlanningContextApplication:
    """Project and normalize the typed planning-context editor contract.

    Interface:
      ``project`` reads a canonical request and returns a UI summary.
      ``compile_edit`` turns one typed UI edit into an atomic ``RequestPatch``
      and deterministic field issues.  Neither method mutates the request or
      invokes the Router; the caller passes the result to ConstraintEngine.
    """

    def __init__(self, *, geocoding_provider: GeocodingProvider | None = None) -> None:
        self._geocoding_provider = geocoding_provider
        self._readiness = RequestReadinessPolicy()

    def project(
        self,
        request: PlanRequest | None,
        *,
        pending_interaction: object | None = None,
        planned_request_revision: int | None = None,
        has_active_plan: bool = False,
    ) -> PlanningContextSummary:
        """Build a display projection from the canonical request only."""

        active = request or PlanRequest()
        window = active.planning_window
        location = active.location
        party = active.party
        budget = active.budget_per_person
        pending_field = self._pending_field(pending_interaction)
        pending_where = pending_field == "location"
        pending_start = pending_field in {"time_window", "departure_at"}
        pending_end = pending_field in {"time_window", "return_by"}
        pending_date = pending_field == "date"

        date_value = window.date.value if window.date else None
        start_value = window.start_at.value if window.start_at else None
        end_value = window.end_at.value if window.end_at else None
        party_value = party.value if party else PartyProfile()
        preferences = {
            "preferences": list(active.preferences),
            "diet_tags": list(active.diet_tags),
            "scene_tags": list(active.scene_tags),
            "avoid": list(active.avoid),
        }
        preference_values = list(
            dict.fromkeys(
                value
                for values in preferences.values()
                for value in values
            )
        )
        return PlanningContextSummary(
            request_revision=active.revision,
            where=self._context_field(
                location.value.model_dump(mode="json") if location else None,
                source=location.source if location else None,
                display=location.value.address if location else None,
                pending=pending_where,
            ),
            when=PlanningContextWhen(
                date=self._context_field(
                    date_value.isoformat() if date_value else None,
                    source=window.date.source if window.date else None,
                    display=date_value.isoformat() if date_value else None,
                    pending=pending_date,
                ),
                start_at=self._context_field(
                    start_value,
                    source=window.start_at.source if window.start_at else None,
                    display=start_value,
                    pending=pending_start,
                ),
                end_at=self._context_field(
                    end_value,
                    source=window.end_at.source if window.end_at else None,
                    display=end_value,
                    pending=pending_end,
                ),
                start_kind=window.start_kind,
                end_kind=window.end_kind,
            ),
            who=self._context_field(
                party_value.model_dump(mode="json"),
                source=party.source if party else None,
                display=(
                    f"{party_value.adults} 位成人 · {party_value.children} 位儿童"
                    + (
                        f"（{party_value.child_age} 岁）"
                        if party_value.child_age is not None
                        else ""
                    )
                ),
                pending=pending_field in {"party", "child_age"},
            ),
            budget=self._context_field(
                {
                    "mode": "per_person" if budget else "unlimited",
                    "amount": budget.value if budget else None,
                    "strict": active.strict_budget,
                },
                source=budget.source if budget else None,
                display=(f"人均 ¥{budget.value}" if budget else "不限")
                + (" · 严格" if budget and active.strict_budget else ""),
                pending=pending_field == "budget_per_person",
            ),
            preferences=self._context_field(
                preferences,
                source=(ConstraintSource.USER_EXPLICIT if preference_values else None),
                display="、".join(preference_values) if preference_values else "未设置",
                pending=pending_field in {"preferences", "diet_tags", "scene_tags", "avoid"},
            ),
            pending_field=pending_field,
            planned_request_revision=planned_request_revision,
            has_active_plan=has_active_plan,
            plan_stale=has_active_plan and planned_request_revision != active.revision,
            ready_for_planning=(
                self._readiness.first_issue(active) is None
                and not (active.strict_budget and active.budget_per_person is None)
            ),
        )

    def compile_edit(
        self,
        patch: PlanningContextPatch,
        *,
        request: PlanRequest,
        environment: EnvironmentContext,
        pending_field: str | None = None,
    ) -> PlanningContextEditResult:
        """Normalize a typed editor patch without applying it to ``request``."""

        set_fields: dict[str, object] = {}
        clear_fields: list[str] = []
        add_to_fields: dict[str, tuple[str, ...]] = {}
        remove_from_fields: dict[str, tuple[str, ...]] = {}
        issues: list[ClarificationIssue] = []

        if patch.where is not None:
            self._compile_where(
                patch,
                request=request,
                environment=environment,
                set_fields=set_fields,
                clear_fields=clear_fields,
                issues=issues,
            )

        if patch.when is not None:
            self._compile_when(
                patch,
                request=request,
                pending_field=pending_field,
                set_fields=set_fields,
                clear_fields=clear_fields,
            )

        if patch.who is not None:
            current = request.party.value if request.party else PartyProfile()
            adults = patch.who.adults if patch.who.adults is not None else current.adults
            children = patch.who.children if patch.who.children is not None else current.children
            child_age = current.child_age
            members = list(current.members)
            if patch.who.child_age is not None:
                child_age = (
                    patch.who.child_age.value
                    if patch.who.child_age.operation == "set"
                    else None
                )
            if patch.who.members is not None:
                members = (
                    list(patch.who.members.value or [])
                    if patch.who.members.operation == "set"
                    else []
                )
            set_fields["party"] = ConstraintValue(
                value=PartyProfile(
                    adults=adults,
                    children=children,
                    child_age=child_age,
                    members=members,
                ),
                source=ConstraintSource.USER_EXPLICIT,
                raw_text="顶部同行人设置",
                rule_id="planning_context.who.v1",
            )

        if patch.budget is not None:
            if patch.budget.mode == "unlimited":
                clear_fields.append("budget_per_person")
                set_fields["strict_budget"] = False
            else:
                set_fields["budget_per_person"] = ConstraintValue(
                    value=patch.budget.amount,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text=f"人均预算 {patch.budget.amount}",
                    rule_id="planning_context.budget.v1",
                )
                set_fields["strict_budget"] = patch.budget.strict

        if patch.preferences is not None:
            for operation, target in (
                (patch.preferences.add, add_to_fields),
                (patch.preferences.remove, remove_from_fields),
            ):
                grouped: dict[str, list[str]] = {}
                for item in operation:
                    value = item.value.strip()
                    if value:
                        grouped.setdefault(item.category, []).append(value)
                target.update({field: tuple(values) for field, values in grouped.items()})

        return PlanningContextEditResult(
            patch=RequestPatch(
                base_revision=patch.base_revision,
                set_fields=set_fields,
                clear_fields=tuple(clear_fields),
                add_to_fields=add_to_fields,
                remove_from_fields=remove_from_fields,
                source=ConstraintSource.USER_EXPLICIT,
            ),
            issues=tuple(issues),
        )

    def _compile_where(
        self,
        patch: PlanningContextPatch,
        *,
        request: PlanRequest,
        environment: EnvironmentContext,
        set_fields: dict[str, object],
        clear_fields: list[str],
        issues: list[ClarificationIssue],
    ) -> None:
        edit = patch.where
        assert edit is not None
        if edit.operation == "clear":
            clear_fields.append("location")
            return
        location_text = (edit.value or "").strip()
        if not location_text:
            issues.append(
                self._issue(
                    "location",
                    code="LOCATION_TEXT_REQUIRED",
                    reason="location_text_is_empty",
                    expected_value_type="location",
                    revision=patch.base_revision,
                )
            )
            return
        if self._geocoding_provider is None:
            issues.append(
                self._issue(
                    "location",
                    code="LOCATION_GEOCODER_UNAVAILABLE",
                    reason="location_provider_unavailable",
                    expected_value_type="location",
                    revision=patch.base_revision,
                )
            )
            return
        city = (
            request.location.value.city
            if request.location is not None
            else environment.default_location.city
        )
        try:
            fact = self._geocoding_provider.geocode(
                GeocodeRequest(location_text=location_text, city=city)
            )
        except Exception:
            fact = None
        if fact is None or fact.resolution != GeocodeResolution.RESOLVED or fact.point is None:
            resolution = fact.resolution.value if fact is not None else "provider_error"
            issues.append(
                self._issue(
                    "location",
                    code=f"LOCATION_{resolution.upper()}",
                    reason="location_must_resolve_to_one_place",
                    expected_value_type="location",
                    revision=patch.base_revision,
                )
            )
            return
        set_fields["location"] = ConstraintValue(
            value=GeoLocation(
                city=fact.city or city or "",
                district=fact.district or "",
                address=fact.address or location_text,
                latitude=fact.point.latitude,
                longitude=fact.point.longitude,
                adcode=fact.adcode,
            ),
            source=ConstraintSource.USER_EXPLICIT,
            raw_text=location_text,
            rule_id="planning_context.where.v1",
        )

    @staticmethod
    def _compile_when(
        patch: PlanningContextPatch,
        *,
        request: PlanRequest,
        pending_field: str | None,
        set_fields: dict[str, object],
        clear_fields: list[str],
    ) -> None:
        assert patch.when is not None
        for field_name, edit in (
            ("planning_window.date", patch.when.date),
            ("planning_window.start_at", patch.when.start_at),
            ("planning_window.end_at", patch.when.end_at),
        ):
            if edit is None:
                continue
            if edit.operation == "clear":
                clear_fields.append(field_name)
                if field_name.endswith("start_at"):
                    set_fields["planning_window.start_kind"] = "trip_start"
                if field_name.endswith("end_at"):
                    set_fields["planning_window.end_kind"] = "trip_end"
                continue
            kind_field = None
            if field_name.endswith("start_at"):
                kind_field = "planning_window.start_kind"
                kind_value = (
                    "departure"
                    if pending_field == "departure_at"
                    else request.planning_window.start_kind
                )
            elif field_name.endswith("end_at"):
                kind_field = "planning_window.end_kind"
                kind_value = (
                    "return_deadline"
                    if pending_field == "return_by"
                    else request.planning_window.end_kind
                )
            if kind_field is not None:
                set_fields[kind_field] = kind_value
            set_fields[field_name] = ConstraintValue(
                value=edit.value,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=str(edit.value),
                rule_id=f"planning_context.{field_name}.v1",
            )

    @staticmethod
    def _context_source(source: ConstraintSource | None) -> str:
        if source in {
            ConstraintSource.USER_EXPLICIT,
            ConstraintSource.USER_INFERRED,
            ConstraintSource.SESSION_CONFIRMED,
            ConstraintSource.MEMORY,
        }:
            return "user"
        if source in {ConstraintSource.DERIVED, ConstraintSource.REAL_TOOL}:
            return "derived"
        return "default"

    @classmethod
    def _context_field(
        cls,
        value: object = None,
        *,
        source: ConstraintSource | None = None,
        display: str | None = None,
        pending: bool = False,
    ) -> PlanningContextField:
        present = value is not None
        mapped_source = cls._context_source(source)
        return PlanningContextField(
            value=value,
            display_value=display or (str(value) if present else "未设置"),
            source=mapped_source,
            status=(
                "pending"
                if pending
                else "resolved" if present and mapped_source != "default" else "assumed"
            ),
        )

    @staticmethod
    def _pending_field(pending_interaction: object | None) -> str | None:
        if pending_interaction is None:
            return None
        if isinstance(pending_interaction, str):
            return pending_interaction
        if isinstance(pending_interaction, dict):
            value = pending_interaction.get("field")
            return value if isinstance(value, str) else None
        value = getattr(pending_interaction, "field", None)
        return value if isinstance(value, str) else None

    @staticmethod
    def _issue(
        field: str,
        *,
        code: str,
        reason: str,
        expected_value_type: str,
        revision: int,
    ) -> ClarificationIssue:
        return ClarificationIssue(
            field=field,
            code=code,
            reason=reason,
            expected_value_type=expected_value_type,
            request_revision=revision,
        )

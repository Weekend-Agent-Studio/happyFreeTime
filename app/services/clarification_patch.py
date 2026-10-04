"""Compile one clarification answer into a field-scoped RequestPatch."""

from __future__ import annotations

import re
from datetime import date as Date, timedelta

from app.domain.constraints import (
    ActorContext,
    ConstraintPatch,
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    PartyProfile,
    PlanRequest,
    PlanningWindow,
    RequestPatch,
)
from app.services.enrichment import EnvironmentContext
from app.services.request_patch_update import RequestPatchUpdateCompiler


class ClarificationPatchCompiler:
    """Normalize only the field named by a pending issue.

    It deliberately has no Router dependency. For fields already represented
    by the bounded ConstraintPatch vocabulary it delegates normalization to
    the regular proposal compiler; party and child age are typed directly.
    """

    def __init__(self, proposal_compiler: RequestPatchUpdateCompiler) -> None:
        self._proposal_compiler = proposal_compiler

    def compile_answer(
        self,
        *,
        field: str,
        value: str,
        request: PlanRequest,
        actor: ActorContext,
        environment: EnvironmentContext,
    ) -> RequestPatch | None:
        text = value.strip()
        if not text:
            return None

        if field == "party":
            party = self._parse_party(text, request.party.value if request.party else None)
            if party is None:
                return None
            return self._single_field_patch(
                request,
                "party",
                ConstraintValue(
                    value=party,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text=text,
                    rule_id="party.clarification.v1",
                ),
            )

        if field == "child_age":
            age = self._parse_number(text)
            if age is None or not 0 <= age <= 17:
                return None
            current = request.party.value if request.party is not None else PartyProfile(children=1)
            party = current.model_copy(update={"child_age": age})
            return self._single_field_patch(
                request,
                "party",
                ConstraintValue(
                    value=party,
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text=text,
                    rule_id="party.child_age.clarification.v1",
                ),
            )

        proposals = {
            "date": ConstraintPatch(date_text=text),
            "time_window": ConstraintPatch(time_window_text=text),
            "departure_at": ConstraintPatch(departure_at_text=text),
            "return_by": ConstraintPatch(return_by_text=text),
            "location": ConstraintPatch(location_text=text),
            "budget_per_person": ConstraintPatch(budget_text=text, strict_budget=True),
            "strict_budget": ConstraintPatch(budget_text=text, strict_budget=True),
            "max_distance_km": ConstraintPatch(max_distance_text=text),
            "total_distance_km": ConstraintPatch(total_distance_text=text),
            "preferences": ConstraintPatch(preferences=(text,)),
            "diet_tags": ConstraintPatch(diet_tags=(text,)),
            "avoid": ConstraintPatch(avoid=(text,)),
        }
        proposal = proposals.get(field)
        if proposal is None:
            return None
        compiled = self._proposal_compiler.compile_update_proposal(
            base=request,
            proposal=proposal,
            actor=actor,
            environment=environment,
        )
        if compiled.issues:
            return None
        return compiled.patch

    def compile_default(
        self,
        *,
        field: str,
        request: PlanRequest,
        environment: EnvironmentContext,
    ) -> RequestPatch | None:
        if field == "date":
            value = self._next_saturday(environment.now.date())
            return self._single_field_patch(
                request,
                "planning_window.date",
                ConstraintValue(
                    value=value,
                    source=ConstraintSource.DEFAULT_RULE,
                    rule_id="date.default.next_saturday.v1",
                ),
                source=ConstraintSource.DEFAULT_RULE,
            )
        if field == "location":
            return self._single_field_patch(
                request,
                "location",
                ConstraintValue(
                    value=environment.default_location,
                    source=ConstraintSource.SYSTEM_CONTEXT,
                    rule_id="location.session_default.v1",
                ),
                source=ConstraintSource.SYSTEM_CONTEXT,
            )
        if field in {"time_window", "departure_at"}:
            current_window = request.planning_window
            default_window = PlanningWindow(
                date=current_window.date,
                start_at=ConstraintValue(
                    value=("09:00" if field == "departure_at" else "14:00"),
                    source=ConstraintSource.DEFAULT_RULE,
                    rule_id=("time.departure.default_morning.v1" if field == "departure_at" else "time.default.afternoon.v1"),
                ),
                end_at=ConstraintValue(
                    value=(request.planning_window.end_at.value if field == "departure_at" and request.planning_window.end_at else ("23:59" if field == "departure_at" else "18:00")),
                    source=ConstraintSource.DEFAULT_RULE,
                    rule_id=("time.departure.default_horizon.v1" if field == "departure_at" else "time.default.afternoon.v1"),
                ),
                start_kind="departure" if field == "departure_at" else "trip_start",
                end_kind=(
                    current_window.end_kind
                    if field == "departure_at" and current_window.end_at is not None
                    else "trip_end"
                ),
            )
            return self._single_field_patch(
                request, "planning_window", default_window,
                source=ConstraintSource.DEFAULT_RULE,
            )
        if field in {"max_distance_km", "total_distance_km", "budget_per_person"}:
            clear = (field,)
            if field == "budget_per_person":
                clear = ("budget_per_person", "strict_budget")
            return RequestPatch(
                base_revision=request.revision,
                clear_fields=clear,
                source=ConstraintSource.DEFAULT_RULE,
            )
        return None

    @staticmethod
    def _single_field_patch(
        request: PlanRequest,
        field: str,
        value: object,
        *,
        source: ConstraintSource = ConstraintSource.USER_EXPLICIT,
    ) -> RequestPatch:
        return RequestPatch(
            base_revision=request.revision,
            set_fields={field: value},
            source=source,
        )

    @classmethod
    def _parse_party(cls, text: str, current: PartyProfile | None = None) -> PartyProfile | None:
        adult_match = re.search(r"(?P<n>[0-9零〇一二两三四五六七八九十]+)\s*(?:位)?\s*(?:成人|大人)", text)
        child_match = re.search(r"(?P<n>[0-9零〇一二两三四五六七八九十]+)\s*(?:个)?\s*(?:儿童|孩子|小孩)", text)
        adults = cls._parse_number(adult_match.group("n")) if adult_match else None
        children = cls._parse_number(child_match.group("n")) if child_match else None
        if adults is None and children is None:
            count = cls._parse_number(text)
            if count is None or count < 1:
                return None
            adults, children = count, 0
        current = current or PartyProfile()
        return current.model_copy(update={
            "adults": adults if adults is not None else current.adults,
            "children": children if children is not None else current.children,
        })

    @staticmethod
    def _parse_number(text: str) -> int | None:
        match = re.search(r"\d+|[零〇一二两三四五六七八九十]+", text)
        if not match:
            return None
        token = match.group(0)
        if token.isdigit():
            return int(token)
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if token == "十":
            return 10
        if "十" in token:
            left, _, right = token.partition("十")
            return (digits.get(left, 1) if left else 1) * 10 + (digits.get(right, 0) if right else 0)
        if len(token) == 1:
            return digits.get(token)
        return None

    @staticmethod
    def _next_saturday(today: Date) -> Date:
        return today + timedelta(days=(5 - today.weekday()) % 7)


def merge_request_patches(first: RequestPatch, second: RequestPatch) -> RequestPatch:
    """Compose pending operations; later field-scoped answers take precedence."""
    if first.base_revision != second.base_revision:
        raise ValueError("cannot merge patches from different request revisions")

    sets = dict(first.set_fields)
    sets.update(second.set_fields)
    clears = list(first.clear_fields)
    for name in second.clear_fields:
        if name not in clears:
            clears.append(name)
    for name in second.set_fields:
        clears = [item for item in clears if item != name]
    for name in second.clear_fields:
        sets.pop(name, None)

    additions = {name: list(values) for name, values in first.add_to_fields.items()}
    removals = {name: list(values) for name, values in first.remove_from_fields.items()}
    for name in second.set_fields.keys() | set(second.clear_fields):
        additions.pop(name, None)
        removals.pop(name, None)
    for name in set(second.add_to_fields) | set(second.remove_from_fields):
        add_values = list(second.add_to_fields.get(name, ()))
        remove_values = list(second.remove_from_fields.get(name, ()))
        if name in sets:
            current = list(sets[name])
            updated = [value for value in current if value not in set(remove_values)]
            sets[name] = list(dict.fromkeys((*updated, *add_values)))
            continue
        if name in clears:
            if add_values:
                sets[name] = list(dict.fromkeys(add_values))
                clears = [item for item in clears if item != name]
            continue

        # Compose ordered list operations without losing intent: a later add
        # cancels an earlier remove for the same value, and vice versa.
        for value in remove_values:
            additions[name] = [item for item in additions.get(name, ()) if item != value]
            if value not in removals.setdefault(name, []):
                removals[name].append(value)
        for value in add_values:
            removals[name] = [item for item in removals.get(name, ()) if item != value]
            if value not in additions.setdefault(name, []):
                additions[name].append(value)

    evidence = dict(first.evidence)
    evidence.update(second.evidence)
    field_sources = dict(first.field_sources)
    field_sources.update(second.field_sources)
    touched = set(sets) | set(clears) | set(additions) | set(removals)
    return RequestPatch(
        base_revision=first.base_revision,
        set_fields=sets,
        clear_fields=tuple(clears),
        add_to_fields={name: tuple(values) for name, values in additions.items()},
        remove_from_fields={name: tuple(values) for name, values in removals.items()},
        source=second.source,
        field_sources={name: value for name, value in field_sources.items() if name in touched},
        evidence={name: value for name, value in evidence.items() if name in touched},
    )

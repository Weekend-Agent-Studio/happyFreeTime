"""Compile one CREATE proposal into the canonical request-patch contract."""

from __future__ import annotations

from datetime import date as Date, timedelta

from pydantic import BaseModel, ConfigDict, Field

from app.domain.constraints import (
    Assumption,
    ClarificationIssue,
    ConstraintSource,
    ConstraintValue,
    DateReference,
    Interpretation,
    PlanningWindow,
    RequestPatch,
    TimeProposal,
    TimeScope,
    TimeWindow,
    Weekday,
)
from app.services.enrichment import EnvironmentContext, TemporalCompiler


class RequestPatchCompilation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    patch: RequestPatch
    issues: tuple[ClarificationIssue, ...] = ()
    assumptions: tuple[Assumption, ...] = ()


class RequestPatchProposalCompiler:
    """Resolve temporal semantics and merge them into the non-temporal patch.

    The Router preserves the event targeted by each phrase. This compiler only
    normalizes that bounded proposal; it never infers scope from keywords.
    """

    DEFAULT_START = "14:00"
    DEFAULT_END = "18:00"
    DEFAULT_DEPARTURE_HORIZON_END = "23:59"

    def compile(
        self,
        interpretation: Interpretation,
        base_patch: RequestPatch,
        environment: EnvironmentContext,
    ) -> RequestPatchCompilation:
        raw = interpretation.raw_constraints
        date_value, date_issue = self._compile_date(interpretation, environment.now.date())
        temporal_issues = self._compile_issues(interpretation, base_patch.base_revision)
        issues = tuple((*temporal_issues, *([date_issue] if date_issue else [])))
        # Keep the normalized, non-blocking portions in the pending patch while
        # a single unresolved field is clarified. Nothing is applied to the
        # request until the complete patch passes ConstraintEngine.
        if date_value is None:
            date_value = ConstraintValue[Date](
                value=self._next_saturday(environment.now.date()),
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="date.default.next_saturday.v1",
            )
        window, assumptions = self._compile_window(
            interpretation.time_proposals,
            date_value,
            raw.required_stop_roles,
            raw.exact_stop_count,
        )
        updates = dict(base_patch.set_fields)
        updates["planning_window.date"] = window.date
        updates["planning_window.start_at"] = window.start_at
        updates["planning_window.end_at"] = window.end_at
        evidence = dict(base_patch.evidence)

        return RequestPatchCompilation(
            patch=base_patch.model_copy(
                update={"set_fields": updates, "evidence": evidence}
            ),
            issues=issues,
            assumptions=tuple(assumptions),
        )

    @staticmethod
    def _compile_date(
        interpretation: Interpretation,
        current_date: Date,
    ) -> tuple[ConstraintValue[Date] | None, ClarificationIssue | None]:
        raw = interpretation.raw_constraints
        resolved = TemporalCompiler.resolve_date(
            raw.date_reference,
            current_date=current_date,
            weekday=raw.weekday,
            week_offset=raw.week_offset,
            absolute_date=raw.absolute_date,
        )
        if resolved is None and raw.date_text:
            resolved = TemporalCompiler.normalize_date(raw.date_text, current_date)
        if resolved is not None:
            explicit_absolute = raw.date_reference == DateReference.ABSOLUTE
            return (
                ConstraintValue[Date](
                    value=resolved,
                    source=(
                        ConstraintSource.USER_EXPLICIT
                        if explicit_absolute
                        else ConstraintSource.DERIVED
                    ),
                    raw_text=(
                        raw.date_text
                        or interpretation.evidence_map.get("date_reference")
                        or interpretation.evidence_map.get("absolute_date")
                    ),
                    rule_id="request.date.resolve.v1",
                ),
                None,
            )
        if raw.date_text or raw.date_reference:
            return None, ClarificationIssue(
                field="date",
                code="DATE_REQUIRES_RESOLUTION",
                reason="explicit_date_could_not_be_resolved",
                expected_value_type="date",
                request_revision=0,
            )

        default_date = RequestPatchProposalCompiler._next_saturday(current_date)
        return (
            ConstraintValue[Date](
                value=default_date,
                source=ConstraintSource.DEFAULT_RULE,
                rule_id="date.default.next_saturday.v1",
            ),
            None,
        )

    @staticmethod
    def _compile_issues(
        interpretation: Interpretation,
        request_revision: int,
    ) -> list[ClarificationIssue]:
        proposals = interpretation.time_proposals
        issues: list[ClarificationIssue] = []
        for target, field, code, reason in (
            (
                "return",
                "return_by",
                "RETURN_TIME_REQUIRES_CLOCK",
                "explicit_return_period_requires_clock",
            ),
            (
                "departure",
                "departure_at",
                "DEPARTURE_TIME_REQUIRES_CLOCK",
                "explicit_departure_period_requires_clock",
            ),
        ):
            matching = [
                item
                for item in proposals
                if item.target == target and item.precision == "period"
            ]
            exact = any(
                item.target == target and item.precision == "exact"
                for item in proposals
            )
            if matching and not exact:
                issues.append(
                    ClarificationIssue(
                        field=field,
                        code=code,
                        reason=reason,
                        expected_value_type="clock",
                        request_revision=request_revision,
                    )
                )
        trip = next(
            (item for item in proposals if item.target == "trip" and item.precision == "exact"),
            None,
        )
        if trip is not None:
            for item in proposals:
                if item.target == "departure" and item.precision == "exact":
                    if not trip.clock <= item.clock < trip.end_clock:
                        issues.append(
                            ClarificationIssue(
                                field="departure_at",
                                code="DEPARTURE_OUTSIDE_EXPLICIT_WINDOW",
                                reason="explicit_departure_outside_trip_range",
                                expected_value_type="clock",
                                request_revision=request_revision,
                            )
                        )
                if item.target == "return" and item.precision == "exact":
                    if item.clock > trip.end_clock:
                        issues.append(
                            ClarificationIssue(
                                field="return_by",
                                code="RETURN_OUTSIDE_EXPLICIT_WINDOW",
                                reason="explicit_return_outside_trip_range",
                                expected_value_type="clock",
                                request_revision=request_revision,
                            )
                        )
        return issues

    def _compile_window(
        self,
        proposals: tuple[TimeProposal, ...],
        date_value: ConstraintValue[Date],
        required_roles: tuple[object, ...],
        exact_stop_count: int | None,
    ) -> tuple[PlanningWindow, list[Assumption]]:
        assumptions: list[Assumption] = []
        trip = next((item for item in proposals if item.target == "trip"), None)
        departure = next((item for item in proposals if item.target == "departure" and item.precision == "exact"), None)
        return_time = next((item for item in proposals if item.target == "return" and item.precision == "exact"), None)

        start: str | None = None
        end: str | None = None
        start_source = ConstraintSource.DEFAULT_RULE
        end_source = ConstraintSource.DEFAULT_RULE
        start_text: str | None = None
        end_text: str | None = None
        start_rule = "time.default.afternoon.v1"
        end_rule = "time.default.afternoon.v1"

        if trip is not None and trip.precision == "exact":
            start, end = trip.clock, trip.end_clock
            start_source = end_source = ConstraintSource.USER_EXPLICIT
            start_text = end_text = trip.evidence
            start_rule = end_rule = "time.trip.range.v1"
        elif trip is not None and trip.precision == "period":
            bounds = TemporalCompiler.time_window_for_scope(trip.period)
            if bounds is not None:
                start, end = bounds.start, bounds.end
                start_source = end_source = ConstraintSource.DERIVED
                start_text = end_text = trip.evidence
                start_rule = end_rule = f"time.trip.{trip.period.value}.v1"
                if trip.period == TimeScope.ALL_DAY:
                    assumptions.append(
                        Assumption(
                            field="planning_window",
                            value={"start_at": start, "end_at": end},
                            reason="“一整天”按 09:00–21:00 的可见规则安排，并作为充分利用全天的软目标",
                            rule_id="time.trip.all_day.v1",
                        )
                    )

                # A fuzzy afternoon scope is not a hard 18:00 stop when the
                # user also explicitly requested dinner. Preserve the old
                # meal-anchor behavior, but never widen an exact user range.
                role_values = {
                    str(getattr(role, "value", role)) for role in required_roles
                }
                if (
                    trip.period == TimeScope.AFTERNOON
                    and "dinner" in role_values
                    and return_time is None
                ):
                    end = "21:00"
                    end_rule = "time.trip.afternoon.dinner_horizon.v1"
                    assumptions.append(
                        Assumption(
                            field="planning_window.end_at",
                            value=end,
                            reason="用户明确要求晚饭，下午的模糊时间范围延展至晚餐排程边界",
                            rule_id=end_rule,
                        )
                    )

        if departure is not None:
            start = departure.clock
            start_source = ConstraintSource.USER_EXPLICIT
            start_text = departure.evidence
            start_rule = "time.departure.clock.v1"
            if end is None:
                end = self.DEFAULT_DEPARTURE_HORIZON_END
                end_source = ConstraintSource.DEFAULT_RULE
                end_rule = "time.departure.default_horizon.v1"
                assumptions.append(
                    Assumption(
                        field="planning_window.end_at",
                        value=end,
                        reason="只指定出发时间时，结束边界按当日 23:59 作为可见排程范围",
                        rule_id=end_rule,
                    )
                )

        if return_time is not None:
            end = return_time.clock
            end_source = ConstraintSource.USER_EXPLICIT
            end_text = return_time.evidence
            end_rule = "time.return.clock.v1"

        if start is None or end is None:
            default_start, default_end, default_rule = self._default_for_roles(
                required_roles,
                exact_stop_count,
            )
            if start is None:
                start = default_start
                start_source = ConstraintSource.DEFAULT_RULE
                start_rule = default_rule
            if end is None:
                end = default_end
                end_source = ConstraintSource.DEFAULT_RULE
                end_rule = default_rule
            assumptions.append(
                Assumption(
                    field="planning_window",
                    value={"start_at": start, "end_at": end},
                    reason=(
                        "用户未指定可执行时间边界，按演示场景默认时间范围规划"
                        if default_rule == "time.default.afternoon.v1"
                        else "根据明确的用餐角色采用可见默认时间范围"
                    ),
                    rule_id=default_rule,
                )
            )

        return (
            PlanningWindow(
                date=date_value,
                start_at=ConstraintValue[str](
                    value=start,
                    source=start_source,
                    raw_text=start_text,
                    rule_id=start_rule,
                ),
                end_at=ConstraintValue[str](
                    value=end,
                    source=end_source,
                    raw_text=end_text,
                    rule_id=end_rule,
                ),
            ),
            assumptions,
        )

    @staticmethod
    def _default_for_roles(
        roles: tuple[object, ...],
        exact_stop_count: int | None,
    ) -> tuple[str, str, str]:
        role_values = tuple(str(getattr(role, "value", role)) for role in roles)
        if role_values == ("dinner",) and exact_stop_count == 1:
            return "18:00", "22:00", "time.default.dinner.v1"
        if role_values == ("lunch",) and exact_stop_count == 1:
            return "11:30", "14:00", "time.default.lunch.v1"
        has_lunch = "lunch" in role_values
        has_dinner = "dinner" in role_values
        if has_lunch and has_dinner:
            return ("09:00" if role_values[0] == "activity" else "11:30"), "20:30", "time.default.meal_roles.v1"
        if has_dinner:
            return "14:00", "21:00", "time.default.meal_roles.v1"
        if has_lunch:
            if role_values and role_values[0] == "activity":
                return "09:00", "14:00", "time.default.meal_roles.v1"
            return "11:30", "18:00", "time.default.meal_roles.v1"
        return RequestPatchProposalCompiler.DEFAULT_START, RequestPatchProposalCompiler.DEFAULT_END, "time.default.afternoon.v1"

    @staticmethod
    def _next_saturday(current_date: Date) -> Date:
        return current_date + timedelta(days=(5 - current_date.weekday()) % 7)

"""Deterministic check that an applied request can enter planning."""

from app.domain.constraints import ClarificationIssue, PlanRequest


class RequestReadinessPolicy:
    """Return the first planner-required field that is still unresolved.

    A request may be a valid, atomically updated domain snapshot without being
    executable yet. Keep that distinction here so the Planner never has to
    discover missing input by throwing an exception.
    """

    def first_issue(self, request: PlanRequest) -> ClarificationIssue | None:
        window = request.planning_window
        required = (
            (
                request.location is None,
                "location",
                "REQUEST_LOCATION_REQUIRED",
                "planning_requires_resolved_location",
                "location",
            ),
            (
                window.date is None,
                "date",
                "REQUEST_DATE_REQUIRED",
                "planning_requires_date",
                "date",
            ),
            (
                window.start_at is None or window.end_at is None,
                "time_window",
                "REQUEST_TIME_WINDOW_REQUIRED",
                "planning_requires_complete_time_window",
                "time_window",
            ),
        )
        for missing, field, code, reason, expected_type in required:
            if missing:
                return ClarificationIssue(
                    field=field,
                    code=code,
                    reason=reason,
                    expected_value_type=expected_type,
                    request_revision=request.revision,
                )
        return None

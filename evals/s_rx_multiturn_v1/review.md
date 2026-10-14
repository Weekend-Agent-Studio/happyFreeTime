# S-RX1 multi-turn case review

Status: **reviewed**

Reviewer: user

Review date: 2026-10-11

The 24-case suite's product expectations were reviewed by the user. The first B0/B3 one-pass diagnostics have since exposed evaluator issues, so those reports are exploratory and are not the final calibrated baseline. After reviewing the stored traces, the suite now accepts the generic `meal` role for meal requests, treats time-scope changes as part of a time edit, and checks Router attempts only for the current typed-patch turn. The live reports retain the earlier suite hash and must not be presented as results for the calibrated suite.

## Approved product oracles

| Case | Reviewed acceptance criteria |
|---|---|
| `session_area_answer_no_repeat` | Ask once only when an explicitly named activity area cannot be resolved. A clarified area answer resumes. Editing top-bar Where changes the origin, not the activity area; the typed edit does not invoke Router, and other request constraints remain unchanged. |
| `planning_area_not_departure_location` | “在朝阳区玩” is an activity/search area, not a missing departure origin. Do not ask for origin; provide an activity and a meal stop. Local-character semantics are approximate, not an exact-tag hard requirement. |
| `planning_area_not_required_without_area` | A request to visit a park without specifying an activity area must not trigger a `planning_area` question. |
| `ambiguous_date_then_answer` | “等忙完那天” is unresolved date intent and must be clarified; a concrete date resumes planning. |
| `missing_date_default_policy` | With no date stated, use the next Saturday and expose the actual date in planning context/top bar. The fixed Sunday evaluation clock makes this 2026-10-17. |
| `parents_preference_not_blocking` | Parents, novelty, and low-fatigue are semantic/soft objectives; they must not block planning by asking for an unnecessary numeric distance limit. |
| `child_indoor_exploration` | Preserve the six-year-old party evidence and family-friendly objective; include an activity. One exact stop count is not required. |
| `date_lunch_activity_dinner` | Preserve the requested ordered roles: lunch → activity → dinner. |
| `all_day_prefer_fuller_plan` | “一整天” is a soft coverage target spanning morning through dinner, operationalized here as the scheduled itinerary span (not summed dwell time) being at least 600 minutes and actual stops covering morning, afternoon, and evening; it is not a hard feasibility constraint. |
| `friends_beijing_feature_multi_stop` | Include an activity and a meal. “北京特色” is a soft semantic target that may be satisfied approximately and is not asserted through one mandatory label. |
| `post_plan_add_all_day` | Merge the all-day patch with the existing activity and dinner request, replan, cover morning through dinner, and add at least one meaningful (non-break) stop compared with the most complete initial alternative. Do not require the same POI identities from unselected alternative cards. Preserve unrelated constraints. |
| `post_plan_morning_departure_clarify` | “早上出门” scopes to departure, so ask for an approximate exact time; after 09:00, replan with the all-day target and prior romantic intent retained. |
| `post_plan_afternoon_departure_patch` | “下午两点出发” sets a departure time, not an afternoon-only trip window; the planning-window start and its event kind may change, while unrelated request constraints remain unchanged. |
| `post_plan_exact_three_stops` | “改成三站” means exactly three. Generate exactly three if feasible; otherwise keep the exact-count constraint and offer a typed recovery action rather than silently returning a different count. |
| `topbar_time_patch_replan` | Top-bar When saves a typed time patch without Router, then explicit replan uses 09:00–18:00. The prior coarse trip-period label may be cleared because the explicit window supersedes it; unrelated constraints remain unchanged. |
| `strict_budget_typed_recovery` | Ask for the missing strict budget amount. After “人均200元”, retain 200 as the hard budget and plan only within it; do not invent a further recovery step. |
| `session_departure_return_conflict_recovery` | For 15:00 departure vs. 12:00 return, ask which field the user wants to change. After choosing return and entering 21:30, resume while preserving the 15:00 departure. |
| `distance_conflict_requires_consent` | Keep the explicit 0.1 km whole-route limit until consent. Reject a stale interaction ID with HTTP 409; the current edit to 30 km may then be applied and replanned. |
| `single_stop_replacement_no_candidate` | When no suitable replacement exists, show a keep-current-plan recovery option, preserve the active PlanVersion, and leave all constraints unchanged. The replay fixture restricts the catalog to one activity and one restaurant so the selected activity has no alternate activity candidate; it does not alter the product path. |
| `single_stop_replacement_success` | On successful replacement, create a new PlanVersion, change the targeted stop, preserve non-target stops, and leave all constraints unchanged. |
| `cancel_recovery_turn` | Cancel only the current recovery turn: discard the uncommitted infeasible draft, preserve any previously committed request/plan, and do not replan. This fresh-session case should therefore have no active distance constraint or plan afterward. Cancellation is a successful terminal interaction, **not** a successful planning task; it is excluded from the task-success denominator. |
| `start_new_request_after_recovery` | Starting a fresh request clears all turn-scoped constraints and old plan state; only the new Saturday-afternoon park request applies (2026-10-17 under the fixed clock). |
| `automatic_recovery_reenters_planner_trace` | The first request uses a replayed southern Beijing origin outside the default 8 km radius, forcing one eligible default-radius recovery and a second planning attempt within the same HTTP request. If that bounded retry still returns a recovery card, exercise the offered search-radius patch with explicit user consent, then merge the added departure/all-day requirements with the prior activity, dinner, and romantic request, and resume after the time answer. Do not preserve old POI identities; the trace must close without an exception. |
| `total_distance_five_km_scope` | “全程总路程不超过五公里” maps to `total_distance_km=5`; it must not be reinterpreted as search radius or per-leg distance. A feasible plan or an accurate recovery is acceptable. |

The suite uses a fixed evaluation clock (`2026-10-11T10:00:00+08:00`) so relative-date expectations are reproducible. The runner exercises public HTTP interactions, records bounded summaries, and never stores prompts or raw model output. Cases include real-session regressions, but session IDs and private transcripts are not copied into fixtures.

S-RX1.2 calibration: the new-request-after-recovery input explicitly says “下周六” to avoid ambiguous interpretation of “周六”. Recovery actions are selected by typed kind/field, not action-ID prefixes. All-day assertions measure actual scheduled-period coverage and schedule span rather than summing stop dwell durations; earlier B0/B3 reports retain their original suite hash and are not results for this calibrated suite.

`eventual_task_success` excludes the deliberate cancellation case; the report separately records success of non-planning terminal outcomes. Missing preconditions remain `not_exercised`, not synthetic request failures. Reviewed status means the expected behavior is human-approved; it does not mean any baseline run has passed or that all production behaviors are already implemented.

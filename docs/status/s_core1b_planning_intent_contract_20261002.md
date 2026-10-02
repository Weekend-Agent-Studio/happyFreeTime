# S-CORE1B: Planning Intent Contract

## Scope

The live PlanningIntent model now contains only soft guidance: `pace` and
`semantic_request`. Structural choices are represented by the single
`PlanStructureProposal` wire DTO and compiled into concrete `PlanSpec` role
sequences. Optionality exists only in the proposal; no executable PlanSpec has
optional slots or a second min/max/precedence representation.

The wire schema is `plan-structure-proposal.v3`. Legacy
`PlanningIntentProposal` parsing and structural fields on `PlanningIntent` have
been removed. The Harness remains responsible for proposal grounding,
compilation, deterministic Rule fallback, search, scheduling, and verification.

## Development checkpoint reset

This slice intentionally breaks compatibility with development checkpoints.
The application now reads and writes LangGraph state using versioned thread
keys prefixed `planner-core1b-v3:`; checkpoints written under the prior thread
keys are not migrated or read by the new graph. There is no checkpoint adapter.

Before manually testing an existing local development database/session,
recreate the development database and start a fresh session. This is a
development-only reset; no production data migration is provided or implied.

## Verification boundary

S-CORE1B verification covers the backend suite and five targeted PlanningIntent
cases. The full Frozen C0-C4 and Live B0/B3 release rerun, architecture-document
cleanup, and interview walkthrough belong to S-CORE1C.

## Verification results

- Backend tests: 450 passed, 34 subtests; `compileall app tests` passed.
- The five targeted live C1 cases used five Provider attempts, with no retries
  or fallback. The v3 proposal was accepted in 5/5 cases.
- Objective recall was 7/8 (87.5% fixed/evaluable; 7/7 conditional), matching
  the earlier 5-case diagnostic; this does not establish a quality gain.
- End-to-end task success was 3/5. `plan_rain_indoor_fallback` retained the
  known semantic-evidence gap. `plan_child_indoor_explore` returned a
  `time_window` conflict under C1, while a same-worktree C0 diagnostic passed
  that case (1/1). Its accepted proposal requested two activities plus dinner;
  after preferred specs failed, the Rule `activity-meal-v1` fallback was also
  attempted but produced no route candidates. This is a real C1-vs-C0 case
difference, but this single live sample cannot distinguish model-output
variance from an interaction between accepted semantics and candidate
ranking. Do not present 3/5 as a release metric; include the case in S-CORE1C
regression review.

## Follow-up: complete Rule fallback before S-CORE1C

Review of the child-case discrepancy found that the old fallback changed the
PlanSpec but reused the accepted LLM PlanningIntent, its retrieval scores, and
the already-consumed route budget. That was not an isolated Rule baseline.
The bounded fallback now repeats role-scoped retrieval and local ranking with
the deterministic Rule PlanningIntent, then sends the result through the same
Route/Availability/Verifier seam. It does not call the LLM again, and the
preferred pass leaves a bounded Route/Availability reserve for recovery.
Trace metadata distinguishes fallback attempted/used, stage, reason, and
preferred versus fallback failure fields.

Verification after this follow-up:

- Backend tests: 451 passed, 34 subtests.
- `compileall app tests` passed.
- Regression tests cover Rule-semantic retrieval after a preferred route
  failure and confirm the preferred route pass cannot consume the entire
  request budget before fallback.
- The five-case live C1 diagnostic and the full C0-C4 / B0-B3 release suite
  have not been rerun after this fix; those remain in S-CORE1C.

The S-CORE1B task card intentionally limited live validation to five targeted
C1 cases and explicitly deferred full C0-C4/B0-B3 to S-CORE1C. The full
ablation was therefore not silently skipped; the remaining gap is the
unreported intermediate discrepancy documented above, now fixed at the
deterministic code/test level and awaiting the 1C release rerun.

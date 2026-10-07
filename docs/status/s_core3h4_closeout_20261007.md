# S-CORE3H4 closeout — action-path cleanup

Date: 2026-10-07
Branch: `codex/s-core3h-semantic-closeout`
Base: `2e9657f` (H4 changes were evaluated in the worktree before commit)

## Scope

H4 removes the old action-routing path without changing Planner, Retrieval,
Provider, Verifier, or Advisor behavior. The production path is now:

```text
TurnInterpreterResult
        ↓
TurnProposal / DecisionContext
        ↓
TurnCompiler
        ↓
CompiledNextAction
        ↓
Graph workflow route
```

## Changes

- Graph runtime accepts only `interpret_with_runtime()` returning a
  `TurnInterpreterResult`; tuple/`Interpretation` fallbacks are rejected.
- `TurnCompiler.from_interpretation()` and the compiler's old
  `has_selected_plan` escape hatch were removed. Action legality comes from
  `DecisionContext`.
- `TurnInterpreter.interpret()` is no longer a public production action entry
  point. The Router validates and returns the current proposal/result contract.
- The deterministic Demo Router and historical Frozen Adapter translate their
  internal fixture/projection into the current proposal at an explicit
  boundary. They do not route through the deleted compiler adapter.
- The evaluation recorder requires the current runtime result contract.
- Test-only `tests/router_support.py` keeps old hand-authored `Interpretation`
  fixtures readable without restoring a production compatibility path.

`Interpretation` and the deterministic Demo/Frozen projection remain only as
bounded semantic/evaluation internals. `RouterContext.has_selected_plan` also
remains as prompt/parser context; it is not used as a compiler action bypass.

## Validation

Deterministic and build checks:

- Seven H4 release cases: **7/7** task success, **34/34** required assertions,
  and modification **1/1**.
- Backend collection: **456 tests**; execution reached 100% with no failure
  output. The Windows test process did not terminate cleanly after completion,
  so no exit-code summary was emitted.
- In-memory AST compilation: **137 Python files** parsed successfully.
- `git diff --check`: no content errors (only normal line-ending warnings).
- Frontend, using the existing project dependency tree: **32/32** tests,
  TypeScript app/node checks, and Vite production build all passed.

Frozen evaluation outputs were written to the local temporary directories
below because the isolated worktree cannot create `artifacts/evals`:

- C0: `C:\Users\UpAndUp\AppData\Local\Temp\S-CORE3H4_C0_post_20261007`
  — 33/36; the two known Rule semantic-profile gaps and one C0 modification
  setup gap remain. Hard constraints 7/7, conflicts 4/4, modification
  execution 9/9.
- C2: `...\\S-CORE3H4_C2_post_20261007` — **36/36**, 208/208 required
  assertions, hard constraints 7/7, conflicts 4/4, modifications 10/10.
- C3: `...\\S-CORE3H4_C3_20261007` — **36/36** and all safety assertions;
  all 17 live PlanningIntent calls were network fallbacks, so no LLM-quality
  metric is valid.
- C4: `...\\S-CORE3H4_C4_20261007` — **36/36** and all safety assertions;
  all PlanningIntent/Advisor calls were network fallbacks, so Advisor
  acceptance is not a quality result.
- B0/B3: `...\\S-CORE3H4_B0_20261007` and
  `...\\S-CORE3H4_B3_20261007` — all live Router calls were blocked by the
  network environment. Their 0/36 task results are environment diagnostics,
  not product-quality measurements.

The evaluation reports identify the run as `dirty=True` because they were
generated before this H4 commit. They are regression evidence for the current
code, not a new frozen Resume metric set.

## Network-enabled rerun on the clean H4 commit

After the commit, the same four variants were rerun with the configured model
endpoint and corrected dotenv-style parsing. All reports recorded
`git_dirty=false` and commit `2e9f0f0`:

| Variant | Task success | Hard safety | Conflict diagnosis | Modification setup/execution | Model result |
|---|---:|---:|---:|---:|---|
| C3 Frozen LLM Intent + Hybrid | 36/36 | 7/7 | 4/4 | 10/10, 10/10 | PlanningIntent 0/17 fallback |
| C4 Frozen LLM Intent + Hybrid + Advisor | 36/36 | 7/7 | 4/4 | 10/10, 10/10 | Advisor accepted 22/27; 5 safe fallbacks |
| B0 Live Router + Rule + Rule | 30/36 | 7/7 | 4/4 | 7/10, 7/7 | Router 36 decisions, 37 attempts |
| B3 Live Router + LLM + Hybrid + Advisor | 33/36 | 7/7 | 4/4 | 8/10, 8/8 | Advisor accepted 23/25; 2 safe fallbacks |

Latency and cost observations:

- C3 end-to-end P50/P95: **780/3361 ms**; 48 provider attempts across
  44 model decisions.
- C4 end-to-end P50/P95: **3203/11973 ms**; Advisor P50/P95
  **2656/3196 ms**, with 100,011 input and 18,549 output tokens.
- B0 end-to-end P50/P95: **1598/2473 ms**.
- B3 end-to-end P50/P95: **4764/12172 ms**; Advisor P50/P95
  **2686/3587 ms**, with 86,734 input and 16,607 output tokens.

The B0/B3 task failures are concentrated in the known live-LLM boundary:
`clarify_unknown_date` was planned instead of asking for a date, and several
modification cases did not obtain an initial plan. Hard-constraint safety and
conflict diagnosis remained 100%. Advisor fallbacks were rejected safely for
unsupported claims and did not affect task safety.

Rerun report directories:

- `C:\Users\UpAndUp\AppData\Local\Temp\S-CORE3H4_C3_rerun2_20261007`
- `C:\Users\UpAndUp\AppData\Local\Temp\S-CORE3H4_C4_rerun_20261007`
- `C:\Users\UpAndUp\AppData\Local\Temp\S-CORE3H4_B0_rerun_20261007`
- `C:\Users\UpAndUp\AppData\Local\Temp\S-CORE3H4_B3_rerun_20261007`

## Residual boundaries

- No new compatibility layer was added. Historical fixture conversion is
  confined to Demo/Frozen/evaluation boundaries.
- No Planner/Search/Provider/Verifier behavior was changed in H4.
- The rerun above is the valid network-enabled Live result; earlier blocked
  runs remain diagnostics only.

## Post-H4 follow-up checks

After the clean H4 evaluation, the branch received three small correctness
follow-ups: temporal-scope validation now covers mixed range/clock proposals,
replace actions check lifecycle permissions before target resolution, and the
latest editable `PlanRequest` is persisted even before a `PlanVersion` exists.
These changes do not alter the Planner, Retrieval, Provider, Verifier, or
Advisor algorithms.

- Backend deterministic regression: **457 tests passed**, including the new
  lifecycle, temporal-scope, and draft-persistence cases.
- AST compilation: **128 Python files parsed successfully**; the isolated
  worktree's `compileall` write step was blocked only by its local
  `__pycache__` ACL, not by a syntax error.
- `git diff --check`: no content errors.
- The expensive C0–C4/B0/B3 evaluations were **not rerun** after these
  follow-ups. The clean-commit results in the preceding section remain the
  applicable H4 evaluation evidence.

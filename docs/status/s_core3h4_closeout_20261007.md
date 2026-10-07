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

## Residual boundaries

- No new compatibility layer was added. Historical fixture conversion is
  confined to Demo/Frozen/evaluation boundaries.
- No Planner/Search/Provider/Verifier behavior was changed in H4.
- Live-model quality and latency need a network-enabled rerun before being
  used in a release report.

# S-AB1.4 Legacy V1 Completed vs Resume V2

## Frozen setup

- Common tasks: 16 reviewed cases; dataset SHA256 `4108030641c3b681d867b1add2cc1750cb675a216dac4d67063b9ec16e453970`.
- Completed V1: `legacy-v1-completed-baseline` (`5662a94`).
- Resume V2: `planner-v2-eval-baseline` (`bef5fa3`).
- V2 configuration: Live Router + LLM PlanningIntent + Hybrid Retrieval + Beam + Grounded Advisor.
- Each case ran twice; Original V1 ran once as historical appendix.

## Main product-level comparison

| System | Eventual executions | Stable task success | Flaky cases | P50/P95 latency |
|---|---:|---:|---:|---:|
| Legacy V1 Completed | 26/32 (81.25%) | 13/16 (81.25%) | 0 | 10.0–10.4s / 23.9–25.0s |
| Resume V2 | 32/32 (100%) | 16/16 (100%) | 0 | 5.5–5.6s / 7.1–7.3s |

Completed V1 failed the same three hard-conflict cases in both runs; it returned plans instead of the expected conflict outcomes. Resume V2 passed all 16 cases in both runs.

## Failure-stage distribution

- Completed V1: 6/6 executions failed at conflict classification (three cases repeated twice); no SlotAgent/adapter failure occurred in the formal repeats.
- Resume V2: no task failures in the formal repeats; no failure stage was observed.
- Original V1: failures were distributed across clarification/slot handling, conflict classification, and compound semantic planning; this appendix is descriptive only.

## Clarification and cost observations

- Both systems completed the five incomplete-input cases without asking a question, so clarification recovery is `0/0`; this is not evidence that either system has solved arbitrary multi-turn recovery.
- V2 recorded 38 model decisions / 38 Provider attempts per run, with 137,642–138,465 input tokens and 16,152–16,178 output tokens.
- Completed V1 recorded 48 agent-stage calls per run (Intent/Slot/Planner stage counters); token and external tool-call counts were not exposed by the historical adapter and are not compared as if equivalent.

## Planner-core proxy

The five complete cases passed 5/5 in every run for both systems. This is only a complete-input proxy; it does not bypass the Legacy SlotAgent or V2 QuestionGate and must not be presented as a pure Planner isolation experiment.

## Historical Original V1 appendix

Original V1 completed 4/16 in its single pass. This is an historical reference, not the primary baseline; its failures include clarification/slot handling, conflict classification, and compound semantic planning.

## Scope and limitations

- The first V2 attempt is retained as an invalid pre-run because the fixture defaulted explicit locations to `geocoding=not_found`; it is excluded from all formal counts.
- The pilot adapter did not expose per-response V2 fallback, hard-constraint assertion traces, or comparable V1 token/tool counters. Use the separate Resume V2 C/B reports for those metrics; do not infer them here.
- The main defensible Resume claim is: on 16 reviewed common tasks, stable task success was 13/16 for Completed V1 versus 16/16 for Resume V2, with stable zero observed flakiness; the clarification advantage remains untested by this particular set.

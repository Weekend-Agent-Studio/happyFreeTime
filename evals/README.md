# Resume Release evaluation data

This directory keeps development fixtures separate from holdout candidates.

- `retrieval_cases.json` is the existing retrieval development set. It may be
  used while debugging or tuning retrieval, so its metrics are not holdout
  evidence.
- `retrieval_holdout_cases.json` contains paraphrased retrieval cases that were
  not used for the S3 implementation. It is compatible with
  `python -m evals.run_retrieval_eval --cases evals/retrieval_holdout_cases.json`.
- `resume_release_cases.json` describes end-to-end planning, clarification,
  conflict, advice and multi-turn replacement tasks. The typed loader is
  `evals.resume_release_dataset`.

All newly generated labels start as `draft`. Before publishing any metric, a
human should review each case without looking at model output, correct the
expected outcome and relevant POI set, and change its status to `reviewed`.
The current 35-case E2E set was reviewed on 2026-09-14; per-case rationale is
recorded in `docs/status/resume_release_case_review_2026-09-14.md`. The report
runner publishes reviewed and draft results separately and must never call
draft-only results a production success rate.

The next evaluation slice should compare frozen configurations, not tune on
the holdout data:

1. Rule PlanningIntent + Rule retrieval + Rule advice.
2. LLM PlanningIntent + Rule retrieval + LLM advice.
3. LLM PlanningIntent + Hybrid retrieval + LLM advice.
4. Ablations that replace one component at a time.

Report task success and hard-constraint pass rate first, then semantic need
coverage, replacement-target accuracy, grounded-advice validity, fallback
rate, provider-reported tokens, and cold/warm latency. Missing provider token
usage stays `null`; it is not estimated.

## Resume Release MVP runner

The first end-to-end pilot is intentionally one variant at a time. It uses a
fresh HTTP/FastAPI/SQLite app and deterministic route, weather, geocoding and
availability fixtures for every case:

```powershell
python -m evals.run_resume_release_eval `
  --variant offline_sanity `
  --case-id plan_all_day_date_relaxed `
  --case-id modify_activity_shorter
```

`offline_sanity` never calls an LLM. `B0_DOWNSTREAM_RULE` through
`B3_GROUNDED_ADVICE` require both `--allow-llm` and `LLM_API`; this keeps paid
model calls explicit. Reports are written under `artifacts/evals/`. Future
draft additions remain exploratory and require `--allow-draft`; the frozen
35-case E2E set can now be selected as reviewed. The MVP still does not
automatically promote labels or hide failures.

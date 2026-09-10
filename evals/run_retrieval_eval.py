"""Run a reproducible Rule-vs-Hybrid retrieval comparison.

The evaluator never calls an LLM or a network service.  If optional local BGE
dependencies or a built local index are unavailable, Hybrid reports its real
Rule fallback instead of substituting fabricated dense scores.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Sequence

from app.domain.catalog import ResourceType, StopCandidate
from app.domain.constraints import StopRole
from app.domain.semantics import EvidenceRef, SemanticQuery, SemanticRequest, SoftObjective
from app.services.catalog import SnapshotCatalog
from app.services.candidate_retriever import (
    HybridRagCandidateRetriever,
    RetrievalRequest,
    RuleBasedCandidateRetriever,
)


ROLE_RESOURCE_TYPES = {
    StopRole.ACTIVITY: {ResourceType.ACTIVITY},
    StopRole.MEAL: {ResourceType.RESTAURANT, ResourceType.CAFE, ResourceType.DESSERT},
    StopRole.LUNCH: {ResourceType.RESTAURANT},
    StopRole.DINNER: {ResourceType.RESTAURANT},
    StopRole.BREAK: {ResourceType.CAFE, ResourceType.DESSERT},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("retrieval_cases.json"))
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    candidates = SnapshotCatalog().load_candidates()
    rule = RuleBasedCandidateRetriever()
    hybrid = HybridRagCandidateRetriever()
    reports = {
        "rule": _run_adapter(rule, cases, candidates),
        "hybrid": _run_adapter(hybrid, cases, candidates),
    }
    reports["counterexample_cases"] = [
        case_id
        for case_id in reports["hybrid"]["case_ids"]
        if reports["hybrid"]["hits_at_5"].get(case_id, False)
        and not reports["rule"]["hits_at_5"].get(case_id, False)
    ]
    output = {
        "case_count": len(cases),
        "candidate_count": len(candidates),
        "metrics": reports,
        "counterexample_cases": reports["counterexample_cases"],
        "interpretation": (
            "counterexample_cases is empty when the local BGE/index is unavailable or "
            "when Hybrid does not beat Rule on this fixed fixture set; no uplift is implied."
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2) if args.json else _pretty(output))
    return 0


def _run_adapter(adapter: Any, cases: Sequence[dict[str, Any]], candidates: Sequence[StopCandidate]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        role = StopRole(case["target_role"])
        eligible = [
            candidate
            for candidate in candidates
            if candidate.resource_type in ROLE_RESOURCE_TYPES[role]
        ]
        request = _request(case, eligible, role)
        started_at = perf_counter()
        result = adapter.retrieve(request)
        measured_latency = max(0.0, (perf_counter() - started_at) * 1000.0)
        relevance = {key: float(value) for key, value in case.get("relevance", {}).items()}
        # Keep the report inspectable while retaining enough ranks for MRR and
        # all requested top-k metrics.
        ranked_ids = [item.candidate.resource_id for item in result.items[:10]]
        rows.append(
            {
                "case_id": case["case_id"],
                "ranked_ids": ranked_ids,
                "relevance": relevance,
                "latency_ms": measured_latency,
                "reported_latency_ms": result.latency_ms,
                "mode": result.mode,
                "fallback_reason": result.fallback_reason,
                "hits_at_5": any(relevance.get(item, 0) > 0 for item in ranked_ids[:5]),
                "wrong_evidence": _wrong_evidence(result.items),
            }
        )
    return _aggregate(rows)


def _request(case: dict[str, Any], candidates: Sequence[StopCandidate], role: StopRole) -> RetrievalRequest:
    evidence_id = f"eval.{case['case_id']}"
    evidence = EvidenceRef(
        evidence_id=evidence_id,
        source_type="user_message",
        source_field="query",
        summary=case["query"],
        confidence=1.0,
    )
    query = SemanticQuery(
        query_id=f"query.{case['case_id']}",
        text=case["query"],
        target_role=role,
        evidence_refs=(evidence_id,),
    )
    objectives = tuple(
        SoftObjective(
            kind=kind,
            target_role=role,
            evidence_refs=(evidence_id,),
        )
        for kind in case.get("objectives", ())
    )
    return RetrievalRequest(
        candidates=tuple(candidates),
        semantic_request=SemanticRequest(
            evidence=(evidence,),
            queries=(query,),
            objectives=objectives,
        ),
        target_role=role,
    )


def _aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if row["relevance"]]
    metrics = {
        "recall_at_3": _mean_metric(evaluated, lambda row: _recall(row, 3)),
        "recall_at_5": _mean_metric(evaluated, lambda row: _recall(row, 5)),
        "ndcg_at_3": _mean_metric(evaluated, lambda row: _ndcg(row, 3)),
        "ndcg_at_5": _mean_metric(evaluated, lambda row: _ndcg(row, 5)),
        "mrr": _mean_metric(evaluated, _mrr),
        "zero_hit_rate": _mean_metric(evaluated, lambda row: 0.0 if row["hits_at_5"] else 1.0),
        "wrong_evidence_rate": _mean_metric(evaluated, lambda row: row["wrong_evidence"]),
        "latency_p50_ms": _percentile([row["latency_ms"] for row in rows], 0.50),
        "latency_p95_ms": _percentile([row["latency_ms"] for row in rows], 0.95),
        "fallback_count": sum(1 for row in rows if row["fallback_reason"]),
        "evaluated_case_count": len(evaluated),
        "no_ground_truth_case_count": len(rows) - len(evaluated),
    }
    return {
        **metrics,
        "case_ids": [row["case_id"] for row in rows],
        "hits_at_5": {row["case_id"]: row["hits_at_5"] for row in rows},
        "rows": list(rows),
    }


def _recall(row: dict[str, Any], k: int) -> float:
    relevant = {key for key, value in row["relevance"].items() if value > 0}
    if not relevant:
        return 1.0 if not any(row["relevance"].get(item, 0) > 0 for item in row["ranked_ids"][:k]) else 0.0
    return len(relevant & set(row["ranked_ids"][:k])) / len(relevant)


def _ndcg(row: dict[str, Any], k: int) -> float:
    values = row["relevance"]
    ranked = [values.get(item, 0.0) for item in row["ranked_ids"][:k]]
    ideal = sorted(values.values(), reverse=True)[:k]
    if not ideal or ideal[0] <= 0:
        return 1.0 if not any(ranked) else 0.0
    dcg = sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(ranked))
    ideal_dcg = sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(ideal))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def _mrr(row: dict[str, Any]) -> float:
    for index, item in enumerate(row["ranked_ids"], start=1):
        if row["relevance"].get(item, 0) > 0:
            return 1.0 / index
    return 0.0


def _wrong_evidence(items: Sequence[Any]) -> float:
    total = 0
    wrong = 0
    for item in items:
        for evidence in item.matched_profile_evidence:
            total += 1
            if evidence.source_type not in {"poi_profile", "fixture_aspect"} or not evidence.source_ref:
                wrong += 1
    return wrong / total if total else 0.0


def _mean_metric(rows: Sequence[dict[str, Any]], function) -> float:
    return round(mean(function(row) for row in rows), 6) if rows else 0.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _pretty(output: dict[str, Any]) -> str:
    lines = [f"retrieval cases: {output['case_count']}, candidates: {output['candidate_count']}"]
    for name in ("rule", "hybrid"):
        metrics = output["metrics"][name]
        lines.append(
            f"{name}: Recall@3={metrics['recall_at_3']:.3f} "
            f"Recall@5={metrics['recall_at_5']:.3f} "
            f"nDCG@5={metrics['ndcg_at_5']:.3f} "
            f"MRR={metrics['mrr']:.3f} "
            f"P50/P95={metrics['latency_p50_ms']:.1f}/{metrics['latency_p95_ms']:.1f}ms "
            f"fallbacks={metrics['fallback_count']}"
        )
    lines.append("counterexample_cases=" + json.dumps(output["counterexample_cases"], ensure_ascii=False))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

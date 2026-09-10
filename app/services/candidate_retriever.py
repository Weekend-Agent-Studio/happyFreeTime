"""Bounded, evidence-bearing candidate retrieval.

Catalog recall remains the hard boundary for this module: a retriever may only
rank the candidates it receives and can never re-introduce a catalog rejection.
The canonical API uses :class:`RetrievalRequest`.  A small keyword-compatible
adapter is retained for callers from the earlier S3.5 implementation so the
retrieval seam can be migrated without changing the Planner's public API.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.domain.catalog import StopCandidate
from app.domain.constraints import StopRole
from app.domain.semantics import EvidenceRef, SemanticRequest, SoftObjectiveKind
from app.services.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
    LocalBgeEmbeddingProvider,
)
from app.services.poi_semantic_profiles import PoiSemanticProfileAssembler
from app.services.retrieval_index import (
    RetrievalIndexError,
    RetrievalIndexManifest,
    default_retrieval_cache_dir,
    load_and_validate_retrieval_index,
)


HYBRID_FUSION_VERSION = "hybrid-bge-rank-fusion.v1"
HYBRID_LEXICAL_WEIGHT = 0.35
HYBRID_DENSE_WEIGHT = 0.55
HYBRID_OBJECTIVE_WEIGHT = 0.10
RULE_INDEX_VERSION = "rule-alias.v2"


class RetrievalRequest(BaseModel):
    """One role-scoped retrieval request shared by create and replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[StopCandidate, ...]
    semantic_request: SemanticRequest = SemanticRequest()
    target_role: StopRole
    excluded_resource_ids: frozenset[str] = frozenset()
    limit: int | None = Field(default=None, ge=0)


class RetrievalScore(BaseModel):
    """Normalized, auditable retrieval signals (all values are in ``[0, 1]``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lexical_score: float = Field(default=0.0, ge=0, le=1)
    dense_score: float = Field(default=0.0, ge=0, le=1)
    objective_score: float = Field(default=0.0, ge=0, le=1)
    final_score: float = Field(default=0.0, ge=0, le=1)


class RetrievedCandidate(BaseModel):
    """A candidate and only the evidence used by this retrieval decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: StopCandidate
    score: RetrievalScore = RetrievalScore()
    request_evidence_ids: tuple[str, ...] = ()
    matched_profile_evidence: tuple[EvidenceRef, ...] = ()

    # Compatibility views for S3.5 callers.  New code should use ``score`` and
    # the two explicitly separated evidence collections.
    @property
    def lexical_score(self) -> float:
        return self.score.lexical_score

    @property
    def embedding_score(self) -> float:
        return self.score.dense_score

    @property
    def evidence_refs(self) -> tuple[EvidenceRef, ...]:
        return self.matched_profile_evidence


class RetrievedCandidateSet(BaseModel):
    """Result plus adapter/fallback metadata safe to expose in a trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RetrievedCandidate, ...] = ()
    # ``mode`` is the adapter that actually produced the ordering.  A hybrid
    # failure therefore reports ``rule`` rather than falsely claiming success.
    mode: str = Field(min_length=1)
    index_version: str = Field(min_length=1)
    requested_mode: str = "rule"
    actual_adapter: str = "rule_based"
    model_id: str | None = None
    query_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    fallback_reason: str | None = None
    fusion_version: str | None = None


class CandidateRetriever(Protocol):
    def retrieve(
        self,
        request_or_candidates: RetrievalRequest | Sequence[StopCandidate],
        *,
        semantic_request: SemanticRequest | None = None,
        target_role: StopRole | None = None,
        exclusions: set[str] | None = None,
        limit: int | None = None,
    ) -> RetrievedCandidateSet:
        ...


_OBJECTIVE_TERMS: dict[SoftObjectiveKind, tuple[str, ...]] = {
    SoftObjectiveKind.LOW_FATIGUE: ("轻松", "不累", "松弛", "休闲", "少走", "可休息"),
    SoftObjectiveKind.SHORTER_TRAVEL: ("近", "附近", "少走", "步行可达"),
    SoftObjectiveKind.FEWER_STOPS: ("少站", "不赶", "轻松"),
    SoftObjectiveKind.NOVELTY: ("新鲜", "新奇", "新意", "有意思", "展览", "主题"),
    SoftObjectiveKind.QUIET: ("安静", "清静", "安静聊天", "茶", "书店"),
    SoftObjectiveKind.CONVERSATION_FRIENDLY: ("聊天", "适合聊天", "能聊天", "茶", "咖啡"),
    SoftObjectiveKind.ROMANTIC: ("约会", "浪漫", "夜景", "景观"),
    SoftObjectiveKind.FAMILY_FRIENDLY: ("亲子", "儿童", "家庭", "带孩子"),
    SoftObjectiveKind.LOW_SPICE: ("少辣", "不辣", "微辣", "清淡"),
}


@dataclass(frozen=True)
class _RuleComponents:
    lexical_raw: float
    objective_raw: float
    request_evidence_ids: tuple[str, ...]
    matched_terms: frozenset[str]


class RuleBasedCandidateRetriever:
    """Deterministic lexical/objective baseline; no embeddings or network."""

    mode = "rule"
    index_version = RULE_INDEX_VERSION
    adapter_name = "rule_based"

    def __init__(
        self,
        *,
        profile_assembler: PoiSemanticProfileAssembler | None = None,
    ) -> None:
        self._profile_assembler = profile_assembler or PoiSemanticProfileAssembler()

    def _aliases_for(self, candidate: StopCandidate) -> tuple[str, ...]:
        profile = self._profile_assembler.profiles.get(candidate.resource_id)
        return profile.aliases if profile is not None else ()

    def retrieve(
        self,
        request_or_candidates: RetrievalRequest | Sequence[StopCandidate],
        *,
        semantic_request: SemanticRequest | None = None,
        target_role: StopRole | None = None,
        exclusions: set[str] | None = None,
        limit: int | None = None,
    ) -> RetrievedCandidateSet:
        request = _coerce_request(
            request_or_candidates,
            semantic_request=semantic_request,
            target_role=target_role,
            exclusions=exclusions,
            limit=limit,
        )
        started_at = perf_counter()
        kept = [
            candidate
            for candidate in request.candidates
            if candidate.resource_id not in request.excluded_resource_ids
        ]
        components = [
            _score_components(
                candidate,
                request.semantic_request,
                request.target_role,
        aliases=self._aliases_for(candidate),
            )
            for candidate in kept
        ]
        # No semantic request is intentionally an identity operation.  With a
        # request, scores determine ranking; original order is the first tie
        # breaker and resource_id is only a final deterministic guard.
        indexed = list(zip(kept, components, range(len(kept)), strict=True))
        if not request.semantic_request.is_empty:
            indexed.sort(
                key=lambda item: (
                    -item[1].lexical_raw,
                    -item[1].objective_raw,
                    item[2],
                    item[0].resource_id,
                )
            )
        lexical_max = max((item[1].lexical_raw for item in indexed), default=0.0)
        objective_max = max((item[1].objective_raw for item in indexed), default=0.0)
        items = [
            _to_retrieved_candidate(
                candidate,
                component,
                lexical_score=_relative(component.lexical_raw, lexical_max),
                dense_score=0.0,
                objective_score=_relative(component.objective_raw, objective_max),
                profile_aliases=self._aliases_for(candidate),
                final_score=(
                    max(
                        _relative(component.lexical_raw, lexical_max),
                        _relative(component.objective_raw, objective_max),
                    )
                    if not request.semantic_request.is_empty
                    else 0.0
                ),
            )
            for candidate, component, _ in indexed
        ]
        if request.limit is not None:
            items = items[: request.limit]
        elapsed = _elapsed_ms(started_at)
        return RetrievedCandidateSet(
            items=tuple(items),
            mode=self.mode,
            index_version=self.index_version,
            requested_mode=self.mode,
            actual_adapter=self.adapter_name,
            candidate_count=len(kept),
            latency_ms=elapsed,
        )


class HybridRagCandidateRetriever(RuleBasedCandidateRetriever):
    """BGE dense retrieval fused with lexical and structured objective ranks.

    The index and embedding provider are injected in tests and loaded lazily in
    production.  Missing optional dependencies, a stale artifact, or a model
    failure returns the Rule result with explicit fallback metadata.
    """

    mode = "hybrid"
    index_version = HYBRID_FUSION_VERSION
    adapter_name = "bge_hybrid"

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        profile_assembler: PoiSemanticProfileAssembler | None = None,
        index_dir: Path | None = None,
        index_manifest: RetrievalIndexManifest | None = None,
        index_embeddings: Sequence[Sequence[float]] | None = None,
    ) -> None:
        super().__init__(profile_assembler=profile_assembler)
        self._embedding_provider = embedding_provider or LocalBgeEmbeddingProvider()
        self._index_dir = Path(index_dir) if index_dir is not None else default_retrieval_cache_dir()
        self._index_manifest = index_manifest
        self._index_embeddings = (
            tuple(tuple(float(value) for value in row) for row in index_embeddings)
            if index_embeddings is not None
            else None
        )
        self._dense_index_loaded = index_manifest is not None and index_embeddings is not None

    @property
    def model_id(self) -> str:
        return self._embedding_provider.model_id

    def retrieve(
        self,
        request_or_candidates: RetrievalRequest | Sequence[StopCandidate],
        *,
        semantic_request: SemanticRequest | None = None,
        target_role: StopRole | None = None,
        exclusions: set[str] | None = None,
        limit: int | None = None,
    ) -> RetrievedCandidateSet:
        request = _coerce_request(
            request_or_candidates,
            semantic_request=semantic_request,
            target_role=target_role,
            exclusions=exclusions,
            limit=limit,
        )
        started_at = perf_counter()
        kept = [
            candidate
            for candidate in request.candidates
            if candidate.resource_id not in request.excluded_resource_ids
        ]
        if request.semantic_request.is_empty:
            # Empty semantic requests should not load a model or require an
            # index.  Keep the caller's Catalog order but retain the explicit
            # requested/actual adapter in the trace.
            result = super().retrieve(request)
            return result.model_copy(
                update={
                    "mode": self.mode,
                    "index_version": self.index_version,
                    "requested_mode": self.mode,
                    "actual_adapter": self.adapter_name,
                    "model_id": self.model_id,
                    "fusion_version": HYBRID_FUSION_VERSION,
                    "latency_ms": _elapsed_ms(started_at),
                }
            )

        try:
            manifest, embeddings = self._load_dense_index()
            query_text = _semantic_query_text(request.semantic_request, request.target_role)
            query_vectors = self._embedding_provider.embed_queries([query_text])
            if len(query_vectors) != 1:
                raise EmbeddingProviderError(
                    "invalid_embedding_output",
                    "dense provider returned an unexpected query count",
                )
            query_vector = query_vectors[0]
            dense_values, dense_evidence = _dense_candidate_scores(
                kept,
                manifest,
                embeddings,
                query_vector,
            )
            components = [
                _score_components(
                    candidate,
                    request.semantic_request,
                    request.target_role,
                    aliases=self._aliases_for(candidate),
                )
                for candidate in kept
            ]
            semantic_request_ids = _semantic_request_evidence_ids(
                request.semantic_request,
                request.target_role,
            )
            lexical_ranks = _rank_signals(
                {
                    candidate.resource_id: item.lexical_raw
                    for candidate, item in zip(kept, components, strict=True)
                }
            )
            objective_ranks = _rank_signals(
                {
                    candidate.resource_id: item.objective_raw
                    for candidate, item in zip(kept, components, strict=True)
                }
            )
            dense_ranks = _rank_signals(dense_values)
            ranked: list[tuple[StopCandidate, _RuleComponents, float, float, float, float]] = []
            for candidate, component in zip(kept, components, strict=True):
                if semantic_request_ids:
                    component = _RuleComponents(
                        lexical_raw=component.lexical_raw,
                        objective_raw=component.objective_raw,
                        request_evidence_ids=semantic_request_ids,
                        matched_terms=component.matched_terms,
                    )
                lexical_score = lexical_ranks.get(candidate.resource_id, 0.0)
                dense_score = dense_ranks.get(candidate.resource_id, 0.0)
                objective_score = objective_ranks.get(candidate.resource_id, 0.0)
                final_score = (
                    HYBRID_LEXICAL_WEIGHT * lexical_score
                    + HYBRID_DENSE_WEIGHT * dense_score
                    + HYBRID_OBJECTIVE_WEIGHT * objective_score
                )
                ranked.append(
                    (
                        candidate,
                        component,
                        lexical_score,
                        dense_score,
                        objective_score,
                        final_score,
                    )
                )
            ranked.sort(key=lambda item: (-item[5], item[0].resource_id))
            items = [
                _to_retrieved_candidate(
                    candidate,
                    component,
                    lexical_score=lexical_score,
                    dense_score=dense_score,
                    objective_score=objective_score,
                    final_score=final_score,
                    extra_profile_evidence=dense_evidence.get(candidate.resource_id, ()),
                    profile_aliases=self._aliases_for(candidate),
                )
                for (
                    candidate,
                    component,
                    lexical_score,
                    dense_score,
                    objective_score,
                    final_score,
                ) in ranked
            ]
            if request.limit is not None:
                items = items[: request.limit]
            return RetrievedCandidateSet(
                items=tuple(items),
                mode=self.mode,
                index_version=manifest.schema_version,
                requested_mode=self.mode,
                actual_adapter=self.adapter_name,
                model_id=self.model_id,
                query_count=1,
                candidate_count=len(kept),
                latency_ms=_elapsed_ms(started_at),
                fusion_version=HYBRID_FUSION_VERSION,
            )
        except Exception as exc:
            # The fallback intentionally catches only the retrieval operation;
            # catalog/verifier failures remain visible to their own callers.
            reason = getattr(exc, "code", None) or _exception_code(exc)
            rule_result = RuleBasedCandidateRetriever(
                profile_assembler=self._profile_assembler,
            ).retrieve(request)
            return rule_result.model_copy(
                update={
                    "requested_mode": self.mode,
                    "actual_adapter": "rule_based",
                    "model_id": self.model_id,
                    "query_count": 0,
                    "candidate_count": len(kept),
                    "latency_ms": _elapsed_ms(started_at),
                    "fallback_reason": reason,
                    "fusion_version": HYBRID_FUSION_VERSION,
                }
            )

    def _load_dense_index(self) -> tuple[RetrievalIndexManifest, Sequence[Sequence[float]]]:
        if self._dense_index_loaded:
            if self._index_manifest is None or self._index_embeddings is None:
                raise RetrievalIndexError("in-memory dense index is incomplete")
            if (
                self._index_manifest.model_id != self._embedding_provider.model_id
                or self._index_manifest.dimension != self._embedding_provider.dimension
            ):
                raise RetrievalIndexError("in-memory dense index model metadata does not match provider")
            if self._index_manifest.profile_source_hash != self._profile_assembler.source_hash:
                raise RetrievalIndexError("in-memory dense index profiles are stale")
            if len(self._index_embeddings) != self._index_manifest.chunk_count or any(
                len(row) != self._index_manifest.dimension
                for row in self._index_embeddings
            ):
                raise RetrievalIndexError("in-memory dense index shape does not match manifest")
            return self._index_manifest, self._index_embeddings
        manifest, embeddings = load_and_validate_retrieval_index(
            self._index_dir,
            expected_model_id=self._embedding_provider.model_id,
            expected_dimension=self._embedding_provider.dimension,
        )
        if manifest.profile_source_hash != self._profile_assembler.source_hash:
            raise RetrievalIndexError(
                "retrieval index profile_source_hash does not match current profiles"
            )
        # Convert the optional numpy matrix to plain rows at the adapter
        # boundary.  This keeps the rest of the app independent of numpy.
        rows = tuple(tuple(float(value) for value in row) for row in embeddings)
        self._index_manifest = manifest
        self._index_embeddings = rows
        self._dense_index_loaded = True
        return manifest, rows


def build_default_candidate_retriever(mode: str | None = None) -> CandidateRetriever:
    """Build the safe rule default or explicit local-BGE hybrid adapter."""

    selected_mode = (mode or os.getenv("HFT_CANDIDATE_RETRIEVER_MODE", "rule")).lower()
    if selected_mode == "rule":
        return RuleBasedCandidateRetriever()
    if selected_mode == "hybrid":
        return HybridRagCandidateRetriever()
    raise RuntimeError("HFT_CANDIDATE_RETRIEVER_MODE must be one of: rule, hybrid")


def _coerce_request(
    request_or_candidates: RetrievalRequest | Sequence[StopCandidate],
    *,
    semantic_request: SemanticRequest | None,
    target_role: StopRole | None,
    exclusions: set[str] | None,
    limit: int | None,
) -> RetrievalRequest:
    if isinstance(request_or_candidates, RetrievalRequest):
        if any(value is not None for value in (semantic_request, target_role, exclusions, limit)):
            raise TypeError("legacy retrieval keyword arguments cannot accompany RetrievalRequest")
        return request_or_candidates
    # Compatibility bridge.  New Planner paths always provide a role.  The
    # activity default only exists for old callers that mixed resource roles.
    return RetrievalRequest(
        candidates=tuple(request_or_candidates),
        semantic_request=semantic_request or SemanticRequest(),
        target_role=target_role or StopRole.ACTIVITY,
        excluded_resource_ids=frozenset(exclusions or set()),
        limit=limit,
    )


def _score_components(
    candidate: StopCandidate,
    request: SemanticRequest,
    target_role: StopRole,
    *,
    aliases: Sequence[str] = (),
) -> _RuleComponents:
    profile = _profile_text(candidate, aliases=aliases)
    profile_terms = _profile_terms(candidate, aliases=aliases)
    lexical_score = 0.0
    objective_score = 0.0
    matched_request_ids: list[str] = []
    matched_profile_terms: set[str] = set()
    for query in request.queries:
        if set(query.evidence_refs) & _route_evidence_ids(request):
            continue
        if query.target_role is not None and query.target_role != target_role:
            continue
        query_text = query.text.strip().casefold()
        if not query_text:
            continue
        query_terms = _text_terms(query_text)
        overlap = len(query_terms & profile_terms) / max(1, len(query_terms))
        exact = 1.0 if query_text in profile else 0.0
        if overlap or exact:
            lexical_score += exact + overlap
            matched_profile_terms.update(query_terms & profile_terms)
            if exact:
                matched_profile_terms.add(query_text)
            matched_request_ids.extend(query.evidence_refs)
    for objective in request.objectives:
        # Proximity is a route-level fact, not a semantic POI attribute.  The
        # Planner/Verifier compare complete route distance separately.
        if objective.kind == SoftObjectiveKind.SHORTER_TRAVEL:
            continue
        if objective.target_role is not None and objective.target_role != target_role:
            continue
        aliases = _OBJECTIVE_TERMS[objective.kind]
        matches = [alias for alias in aliases if alias.casefold() in profile]
        if matches:
            objective_score += 0.5 * len(matches)
            matched_profile_terms.update(matches)
            matched_request_ids.extend(objective.evidence_refs)
    known_ids = {item.evidence_id for item in request.evidence}
    request_ids = tuple(
        dict.fromkeys(item for item in matched_request_ids if item in known_ids)
    )
    return _RuleComponents(
        lexical_raw=lexical_score,
        objective_raw=objective_score,
        request_evidence_ids=request_ids,
        matched_terms=frozenset(matched_profile_terms),
    )


def _to_retrieved_candidate(
    candidate: StopCandidate,
    component: _RuleComponents,
    *,
    lexical_score: float,
    dense_score: float,
    objective_score: float,
    final_score: float,
    extra_profile_evidence: Sequence[EvidenceRef] = (),
    profile_aliases: Sequence[str] = (),
) -> RetrievedCandidate:
    profile_evidence = _candidate_evidence(
        candidate,
        component.matched_terms,
        aliases=profile_aliases,
    )
    evidence_by_id = {
        item.evidence_id: item
        for item in [*profile_evidence, *extra_profile_evidence]
    }
    return RetrievedCandidate(
        candidate=candidate,
        score=RetrievalScore(
            lexical_score=round(_clip(lexical_score), 6),
            dense_score=round(_clip(dense_score), 6),
            objective_score=round(_clip(objective_score), 6),
            final_score=round(_clip(final_score), 6),
        ),
        request_evidence_ids=component.request_evidence_ids,
        matched_profile_evidence=tuple(evidence_by_id.values()),
    )


def _profile_text(candidate: StopCandidate, *, aliases: Sequence[str] = ()) -> str:
    return " ".join(
        [
            candidate.name,
            *aliases,
            candidate.district,
            candidate.address,
            *candidate.category_tags,
            *candidate.preference_tags,
            *candidate.diet_tags,
            *candidate.scene_tags,
        ]
    ).strip().casefold()


def _profile_terms(candidate: StopCandidate, *, aliases: Sequence[str] = ()) -> set[str]:
    return _text_terms(_profile_text(candidate, aliases=aliases))


def _text_terms(value: str) -> set[str]:
    pieces = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.casefold())
    terms = set(pieces)
    for index in range(len(pieces) - 1):
        terms.add(f"{pieces[index]}{pieces[index + 1]}")
    return terms


def _candidate_evidence(
    candidate: StopCandidate,
    matched_terms: set[str],
    *,
    aliases: Sequence[str] = (),
) -> list[EvidenceRef]:
    if not matched_terms:
        return []

    def value_matches(value: str) -> bool:
        normalized = value.strip().casefold()
        if not normalized:
            return False
        value_terms = _text_terms(normalized)
        return bool(value_terms & matched_terms) or any(
            term in normalized or normalized in term
            for term in matched_terms
            if term
        )

    refs: list[EvidenceRef] = []
    for source_field, values in (
        ("name", (candidate.name,)),
        ("aliases", tuple(aliases)),
        ("category_tags", tuple(candidate.category_tags)),
        ("preference_tags", tuple(candidate.preference_tags)),
        ("diet_tags", tuple(candidate.diet_tags)),
        ("scene_tags", tuple(candidate.scene_tags)),
    ):
        for index, value in enumerate(values, start=1):
            text = value.strip()
            if not text or not value_matches(text):
                continue
            refs.append(
                EvidenceRef(
                    evidence_id=f"poi.{candidate.resource_id}.{source_field}.{index}",
                    source_type="poi_profile",
                    source_field=source_field,
                    summary=text,
                    source_ref=candidate.resource_id,
                    confidence=0.8,
                )
            )
    return refs


def _semantic_query_text(request: SemanticRequest, target_role: StopRole) -> str:
    route_evidence_ids = _route_evidence_ids(request)
    parts = [
        query.text.strip()
        for query in request.queries
        if not set(query.evidence_refs) & route_evidence_ids
        if query.text.strip() and (query.target_role is None or query.target_role == target_role)
    ]
    parts.extend(
        alias
        for objective in request.objectives
        if objective.kind != SoftObjectiveKind.SHORTER_TRAVEL
        if objective.target_role is None or objective.target_role == target_role
        for alias in _OBJECTIVE_TERMS[objective.kind]
    )
    return "；".join(dict.fromkeys(parts)).strip()


def _semantic_request_evidence_ids(
    request: SemanticRequest,
    target_role: StopRole,
) -> tuple[str, ...]:
    """Return only user evidence referenced by this role's query."""

    ids: list[str] = []
    route_evidence_ids = _route_evidence_ids(request)
    for query in request.queries:
        if (
            not set(query.evidence_refs) & route_evidence_ids
            and (query.target_role is None or query.target_role == target_role)
        ):
            ids.extend(query.evidence_refs)
    for objective in request.objectives:
        if objective.kind == SoftObjectiveKind.SHORTER_TRAVEL:
            continue
        if objective.target_role is None or objective.target_role == target_role:
            ids.extend(objective.evidence_refs)
    known = {item.evidence_id for item in request.evidence}
    return tuple(dict.fromkeys(item for item in ids if item in known))


def _route_evidence_ids(request: SemanticRequest) -> set[str]:
    return {
        evidence_id
        for objective in request.objectives
        if objective.kind == SoftObjectiveKind.SHORTER_TRAVEL
        for evidence_id in objective.evidence_refs
    }


def _dense_candidate_scores(
    candidates: Sequence[StopCandidate],
    manifest: RetrievalIndexManifest,
    embeddings: Sequence[Sequence[float]],
    query_vector: Sequence[float],
) -> tuple[dict[str, float], dict[str, tuple[EvidenceRef, ...]]]:
    rows_by_resource: dict[str, list[tuple[object, Sequence[float]]]] = {}
    for chunk, vector in zip(manifest.chunks, embeddings, strict=True):
        rows_by_resource.setdefault(chunk.resource_id, []).append((chunk, vector))
    scores: dict[str, float] = {}
    evidence: dict[str, tuple[EvidenceRef, ...]] = {}
    for candidate in candidates:
        best_score = 0.0
        best_chunk = None
        for chunk, vector in rows_by_resource.get(candidate.resource_id, ()):
            score = _cosine_vectors(query_vector, vector)
            if score > best_score:
                best_score = score
                best_chunk = chunk
        # BGE vectors are normalized but cosine can still be negative for an
        # unrelated passage.  Dense rank treats those as no positive signal.
        scores[candidate.resource_id] = max(0.0, min(1.0, best_score))
        if best_chunk is not None and best_score > 0:
            source_type = (
                "fixture_aspect" if best_chunk.source_type == "fixture" else "poi_profile"
            )
            evidence[candidate.resource_id] = (
                EvidenceRef(
                    evidence_id=best_chunk.evidence_id,
                    source_type=source_type,
                    source_field=best_chunk.aspect_id,
                    summary=best_chunk.text,
                    source_ref=best_chunk.source_ref,
                    confidence=0.8,
                ),
            )
    return scores, evidence


def _rank_signals(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    result: dict[str, float] = {}
    previous: float | None = None
    rank = 0
    for index, (resource_id, value) in enumerate(ordered, start=1):
        if value <= 0:
            result[resource_id] = 0.0
            continue
        if previous is None or abs(value - previous) > 1e-12:
            rank = index
            previous = value
        result[resource_id] = 1.0 / rank
    return result


def _relative(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return _clip(value / maximum)


def _cosine_vectors(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((perf_counter() - started_at) * 1000)))


def _exception_code(exc: Exception) -> str:
    if isinstance(exc, RetrievalIndexError):
        return "index_unavailable"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "retrieval_error"

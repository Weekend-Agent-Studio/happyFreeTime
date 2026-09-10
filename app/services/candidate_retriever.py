"""Bounded candidate retrieval shared by creation and single-stop replacement.

The catalog remains responsible for hard pruning.  This module only ranks the
already-safe candidates using semantic requests and returns provenance for the
soft relevance decision.  The default rule adapter preserves catalog order;
the opt-in hybrid adapter adds a deterministic local character n-gram index so
the demo does not need a vector database or a network embedding service.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.domain.catalog import StopCandidate
from app.domain.constraints import StopRole
from app.domain.semantics import EvidenceRef, SemanticRequest, SoftObjectiveKind


class RetrievedCandidate(BaseModel):
    """A candidate plus auditable soft-relevance information."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: StopCandidate
    score: float
    lexical_score: float = 0.0
    embedding_score: float = 0.0
    evidence_refs: tuple[EvidenceRef, ...] = ()


class RetrievedCandidateSet(BaseModel):
    """Top-k result returned by a retrieval adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RetrievedCandidate, ...] = ()
    mode: str = Field(min_length=1)
    index_version: str = Field(min_length=1)


class CandidateRetriever(Protocol):
    def retrieve(
        self,
        candidates: Sequence[StopCandidate],
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
    SoftObjectiveKind.LOW_SPICE: ("少辣", "不辣", "微辣"),
}


class RuleBasedCandidateRetriever:
    """Deterministic baseline; no embeddings and no external calls."""

    mode = "rule"
    index_version = "rule-alias.v1"

    def retrieve(
        self,
        candidates: Sequence[StopCandidate],
        *,
        semantic_request: SemanticRequest | None = None,
        target_role: StopRole | None = None,
        exclusions: set[str] | None = None,
        limit: int | None = None,
    ) -> RetrievedCandidateSet:
        excluded = exclusions or set()
        kept = [item for item in candidates if item.resource_id not in excluded]
        request = semantic_request or SemanticRequest()
        ranked = [
            self._score(item, request, target_role=target_role)
            for item in kept
        ]
        # The rule adapter is a compatibility baseline: it reports scores and
        # evidence, but leaves the Catalog's historical ordering untouched.
        if limit is not None:
            ranked = ranked[: max(0, limit)]
        return RetrievedCandidateSet(
            items=tuple(ranked),
            mode=self.mode,
            index_version=self.index_version,
        )

    @classmethod
    def _score(
        cls,
        candidate: StopCandidate,
        request: SemanticRequest,
        *,
        target_role: StopRole | None,
    ) -> RetrievedCandidate:
        profile = _profile_text(candidate)
        profile_terms = _profile_terms(candidate)
        lexical_score = 0.0
        matched_request_refs: list[EvidenceRef] = []
        matched_profile_terms: set[str] = set()
        for query in request.queries:
            if query.target_role is not None and target_role not in {None, query.target_role}:
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
                matched_request_refs.extend(
                    item for item in request.evidence if item.evidence_id in query.evidence_refs
                )
        for objective in request.objectives:
            if objective.target_role is not None and target_role not in {None, objective.target_role}:
                continue
            aliases = _OBJECTIVE_TERMS[objective.kind]
            matches = [alias for alias in aliases if alias.casefold() in profile]
            if matches:
                lexical_score += 0.5 * len(matches)
                matched_profile_terms.update(matches)
                matched_request_refs.extend(
                    item for item in request.evidence if item.evidence_id in objective.evidence_refs
                )
        evidence_by_id = {
            item.evidence_id: item
            for item in [
                *matched_request_refs,
                *_candidate_evidence(candidate, matched_profile_terms),
            ]
        }
        evidence_refs = tuple(evidence_by_id.values())
        return RetrievedCandidate(
            candidate=candidate,
            score=round(lexical_score, 6),
            lexical_score=round(lexical_score, 6),
            evidence_refs=evidence_refs,
        )


class HybridRagCandidateRetriever(RuleBasedCandidateRetriever):
    """Rule score + deterministic local vector-like character n-gram score."""

    mode = "hybrid"
    index_version = "char-ngram-hybrid.v1"

    def retrieve(
        self,
        candidates: Sequence[StopCandidate],
        *,
        semantic_request: SemanticRequest | None = None,
        target_role: StopRole | None = None,
        exclusions: set[str] | None = None,
        limit: int | None = None,
    ) -> RetrievedCandidateSet:
        excluded = exclusions or set()
        request = semantic_request or SemanticRequest()
        if request.is_empty:
            return super().retrieve(
                candidates,
                semantic_request=request,
                target_role=target_role,
                exclusions=exclusions,
                limit=limit,
            ).model_copy(update={"mode": self.mode, "index_version": self.index_version})
        query_text = " ".join(
            [item.text for item in request.queries]
            + [term for objective in request.objectives for term in _OBJECTIVE_TERMS[objective.kind]]
        ).strip()
        query_vector = _vectorize(query_text)
        ranked: list[RetrievedCandidate] = []
        for candidate in candidates:
            if candidate.resource_id in excluded:
                continue
            base = self._score(candidate, request, target_role=target_role)
            embedding_score = _cosine(query_vector, _vectorize(_profile_text(candidate)))
            combined = 0.7 * base.lexical_score + 0.3 * embedding_score
            ranked.append(
                base.model_copy(
                    update={
                        "embedding_score": round(embedding_score, 6),
                        "score": round(combined, 6),
                    }
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.candidate.resource_id))
        if limit is not None:
            ranked = ranked[: max(0, limit)]
        return RetrievedCandidateSet(
            items=tuple(ranked),
            mode=self.mode,
            index_version=self.index_version,
        )


def build_default_candidate_retriever(mode: str | None = None) -> CandidateRetriever:
    """Build the opt-in hybrid adapter without changing the safe default."""

    selected_mode = (mode or os.getenv("HFT_CANDIDATE_RETRIEVER_MODE", "rule")).lower()
    if selected_mode == "rule":
        return RuleBasedCandidateRetriever()
    if selected_mode == "hybrid":
        return HybridRagCandidateRetriever()
    raise RuntimeError("HFT_CANDIDATE_RETRIEVER_MODE must be one of: rule, hybrid")


def _profile_text(candidate: StopCandidate) -> str:
    return " ".join(
        [
            candidate.name,
            candidate.district,
            candidate.address,
            *candidate.category_tags,
            *candidate.preference_tags,
            *candidate.diet_tags,
            *candidate.scene_tags,
        ]
    ).strip().casefold()


def _profile_terms(candidate: StopCandidate) -> set[str]:
    return _text_terms(_profile_text(candidate))


def _text_terms(value: str) -> set[str]:
    pieces = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.casefold())
    terms = set(pieces)
    for index in range(len(pieces) - 1):
        terms.add(f"{pieces[index]}{pieces[index + 1]}")
    return terms


def _candidate_evidence(
    candidate: StopCandidate,
    matched_terms: set[str],
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


def _vectorize(value: str) -> Counter[str]:
    normalized = value.casefold().strip()
    if not normalized:
        return Counter()
    grams = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized)
    features: Counter[str] = Counter(grams)
    for index in range(len(grams) - 1):
        features[f"{grams[index]}{grams[index + 1]}"] += 1
    return features


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)

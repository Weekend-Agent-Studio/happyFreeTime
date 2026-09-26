"""Build and validate the small local dense retrieval index artifact."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.catalog import StopCandidate
from app.services.embedding import EmbeddingProvider
from app.services.poi_semantic_profiles import (
    PoiSemanticProfileAssembler,
    profile_retrieval_text,
    semantic_profile_source_hash,
)


RETRIEVAL_INDEX_SCHEMA_VERSION = "retrieval-index.v1"
QUERY_INSTRUCTION_VERSION = "bge-zh-retrieval.v1"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "retrieval" / "cache"
_SOURCE_TYPES = Literal["catalog", "official", "open_data", "fixture"]


class RetrievalIndexError(ValueError):
    """The local index is missing, stale or internally inconsistent."""


class RetrievalIndexStaleError(RetrievalIndexError):
    """The index was built from a different semantic profile source."""


def default_retrieval_cache_dir() -> Path:
    """Resolve the ignored cache directory from the optional environment setting."""

    configured = os.getenv("HFT_RETRIEVAL_CACHE_DIR")
    return Path(configured) if configured else DEFAULT_CACHE_DIR


class RetrievalChunk(BaseModel):
    """One independently embedded profile passage and its citation metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    aspect_id: str = Field(min_length=1)
    source_type: _SOURCE_TYPES
    source_ref: str = Field(min_length=1)
    text: str = Field(min_length=1)


class RetrievalIndexManifest(BaseModel):
    """Manifest required to safely reuse a local embedding artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["retrieval-index.v1"]
    model_id: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    profile_schema_version: Literal["poi-semantic-profile.v1"]
    profile_source_hash: str = Field(min_length=64, max_length=64)
    assembled_profile_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    candidate_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    query_instruction_version: str = Field(min_length=1)
    created_at: datetime
    chunks: tuple[RetrievalChunk, ...] = ()

    @model_validator(mode="after")
    def validate_chunk_manifest(self) -> "RetrievalIndexManifest":
        if self.chunk_count != len(self.chunks):
            raise ValueError("retrieval manifest chunk_count does not match chunks")
        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        evidence_ids = [chunk.evidence_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("retrieval manifest chunk ids must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("retrieval manifest evidence ids must be unique")
        return self


def build_retrieval_index(
    candidates: Sequence[StopCandidate],
    *,
    assembler: PoiSemanticProfileAssembler,
    embedding_provider: EmbeddingProvider,
    output_dir: Path,
    created_at: datetime | None = None,
) -> RetrievalIndexManifest:
    """Assemble profiles, embed summary/aspect chunks and write a rebuildable index."""

    profiles = assembler.assemble_many(candidates)
    chunks = tuple(_profile_chunks(profile) for profile in profiles)
    flattened_chunks = tuple(chunk for group in chunks for chunk in group)
    vectors = embedding_provider.embed_passages(
        [chunk.text for chunk in flattened_chunks]
    )
    _validate_vectors(vectors, len(flattened_chunks), embedding_provider.dimension)

    try:
        import numpy as np
    except ImportError as exc:
        raise RetrievalIndexError(
            "numpy is required to write the local retrieval index; "
            "install requirements-rag.txt"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    embedding_path = output_dir / "poi_embeddings.npz"
    np.savez_compressed(
        embedding_path,
        embeddings=np.asarray(vectors, dtype="float32"),
    )
    manifest = RetrievalIndexManifest(
        schema_version=RETRIEVAL_INDEX_SCHEMA_VERSION,
        model_id=embedding_provider.model_id,
        dimension=embedding_provider.dimension,
        profile_schema_version="poi-semantic-profile.v1",
        profile_source_hash=assembler.source_hash,
        assembled_profile_hash=semantic_profile_source_hash(profiles),
        candidate_count=len(profiles),
        chunk_count=len(flattened_chunks),
        query_instruction_version=QUERY_INSTRUCTION_VERSION,
        created_at=created_at or datetime.now(timezone.utc).replace(microsecond=0),
        chunks=flattened_chunks,
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return manifest


def load_and_validate_retrieval_index(
    cache_dir: Path,
    *,
    expected_profiles: Sequence[Any] | None = None,
    expected_model_id: str | None = None,
    expected_dimension: int | None = None,
) -> tuple[RetrievalIndexManifest, Any]:
    """Load the artifact and reject stale or shape-inconsistent embeddings."""

    manifest_path = cache_dir / "manifest.json"
    embedding_path = cache_dir / "poi_embeddings.npz"
    try:
        manifest = RetrievalIndexManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise RetrievalIndexError(
            f"cannot read retrieval index manifest {manifest_path}: {exc}"
        ) from exc

    if expected_model_id is not None and manifest.model_id != expected_model_id:
        raise RetrievalIndexError(
            f"retrieval index model mismatch: {manifest.model_id} != {expected_model_id}"
        )
    if expected_dimension is not None and manifest.dimension != expected_dimension:
        raise RetrievalIndexError(
            f"retrieval index dimension mismatch: {manifest.dimension} != {expected_dimension}"
        )
    if expected_profiles is not None:
        profiles = tuple(expected_profiles)
        expected_hash = semantic_profile_source_hash(profiles)
        if expected_hash not in {
            manifest.profile_source_hash,
            manifest.assembled_profile_hash,
        }:
            raise RetrievalIndexStaleError(
                "retrieval index profile_source_hash does not match current profiles"
            )

    try:
        import numpy as np

        with np.load(embedding_path, allow_pickle=False) as artifact:
            embeddings = artifact["embeddings"]
    except (OSError, KeyError, ValueError, ImportError) as exc:
        raise RetrievalIndexError(
            f"cannot read retrieval embeddings {embedding_path}: {exc}"
        ) from exc

    if getattr(embeddings, "ndim", None) != 2:
        raise RetrievalIndexError("retrieval embeddings must be a 2-D matrix")
    if embeddings.shape != (manifest.chunk_count, manifest.dimension):
        raise RetrievalIndexError(
            "retrieval embedding shape does not match manifest: "
            f"{tuple(embeddings.shape)} != "
            f"({manifest.chunk_count}, {manifest.dimension})"
        )
    if not bool(np.isfinite(embeddings).all()):
        raise RetrievalIndexError("retrieval embeddings contain non-finite values")
    return manifest, embeddings


def _profile_chunks(profile: Any) -> tuple[RetrievalChunk, ...]:
    summary_source_type = (
        "fixture"
        if any(aspect.source_type == "fixture" for aspect in profile.aspects)
        else "catalog"
    )
    summary_source_ref = f"profile:{profile.resource_id}"
    chunks = [
        RetrievalChunk(
            chunk_id=f"{profile.resource_id}:summary",
            resource_id=profile.resource_id,
            evidence_id=f"poi.{profile.resource_id}.summary",
            aspect_id="summary",
            source_type=summary_source_type,
            source_ref=summary_source_ref,
            text=profile_retrieval_text(profile),
        )
    ]
    for aspect in profile.aspects:
        chunks.append(
            RetrievalChunk(
                chunk_id=f"{profile.resource_id}:{aspect.aspect_id}",
                resource_id=profile.resource_id,
                evidence_id=f"poi.{profile.resource_id}.{aspect.aspect_id}",
                aspect_id=aspect.aspect_id,
                source_type=aspect.source_type,
                source_ref=aspect.source_ref,
                text=aspect.text,
            )
        )
    return tuple(chunks)


def _validate_vectors(
    vectors: Sequence[Sequence[float]],
    expected_count: int,
    expected_dimension: int,
) -> None:
    if len(vectors) != expected_count:
        raise RetrievalIndexError(
            f"embedding provider returned {len(vectors)} vectors for {expected_count} chunks"
        )
    for vector in vectors:
        if len(vector) != expected_dimension:
            raise RetrievalIndexError(
                "embedding provider returned a vector with the wrong dimension"
            )

"""Optional local embedding providers for the retrieval bridge.

The normal application path never imports ``sentence_transformers``.  The BGE
adapter loads it only on the first non-empty embedding request, so the existing
rule mode remains usable in a minimal environment.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Protocol, Sequence


BGE_MODEL_ID = "BAAI/bge-small-zh-v1.5"
BGE_DIMENSION = 512
BGE_QUERY_INSTRUCTION_VERSION = "bge-zh-retrieval.v1"
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
DEFAULT_MODEL_PATH = "data/models/bge-small-zh-v1.5"


class EmbeddingProviderError(RuntimeError):
    """A dense embedding request could not be fulfilled safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str:
        ...

    @property
    def dimension(self) -> int:
        ...

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        ...


ModelLoader = Callable[[str, str], Any]


class LocalBgeEmbeddingProvider:
    """Lazy ``sentence-transformers`` adapter for local BGE weights."""

    model_id = BGE_MODEL_ID

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        device: str | None = None,
        expected_dimension: int = BGE_DIMENSION,
        model_loader: ModelLoader | None = None,
    ) -> None:
        configured_path = model_path or os.getenv(
            "HFT_EMBEDDING_MODEL_PATH",
            DEFAULT_MODEL_PATH,
        )
        self._model_path = Path(configured_path)
        self._device = device or os.getenv("HFT_EMBEDDING_DEVICE", "cpu")
        self._dimension = expected_dimension
        self._model_loader = model_loader
        self._model: Any | None = None
        self._load_error: EmbeddingProviderError | None = None
        self._load_lock = Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_path(self) -> Path:
        return self._model_path

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        cleaned = _clean_texts(texts)
        if not cleaned:
            return []
        payload = [f"{BGE_QUERY_INSTRUCTION}{text}" for text in cleaned]
        return self._encode(payload, expected_count=len(cleaned))

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        cleaned = _clean_texts(texts)
        if not cleaned:
            return []
        return self._encode(cleaned, expected_count=len(cleaned))

    def _encode(self, texts: list[str], *, expected_count: int) -> list[list[float]]:
        model = self._get_model()
        try:
            raw = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingProviderError(
                "encode_failed",
                f"BGE embedding encode failed: {exc}",
            ) from exc
        rows = raw.tolist() if hasattr(raw, "tolist") else raw
        if expected_count == 1 and _looks_like_vector(rows):
            rows = [rows]
        try:
            vectors = [
                [float(value) for value in row]
                for row in rows
            ]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError(
                "invalid_embedding_output",
                "BGE returned a non-numeric embedding matrix",
            ) from exc
        if len(vectors) != expected_count:
            raise EmbeddingProviderError(
                "invalid_embedding_output",
                f"BGE returned {len(vectors)} vectors for {expected_count} texts",
            )
        for vector in vectors:
            if len(vector) != self._dimension or not all(
                math.isfinite(value) for value in vector
            ):
                raise EmbeddingProviderError(
                    "invalid_embedding_output",
                    "BGE returned a vector with invalid dimension or value",
                )
        return vectors

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            raise self._load_error
        with self._load_lock:
            if self._model is not None:
                return self._model
            if self._load_error is not None:
                raise self._load_error
            if not self._model_path.exists():
                self._load_error = EmbeddingProviderError(
                    "model_missing",
                    f"BGE model path does not exist: {self._model_path}",
                )
                raise self._load_error
            try:
                if self._model_loader is not None:
                    model = self._model_loader(str(self._model_path), self._device)
                else:
                    from sentence_transformers import SentenceTransformer

                    model = SentenceTransformer(
                        str(self._model_path),
                        device=self._device,
                    )
                actual_dimension = int(model.get_sentence_embedding_dimension())
            except ImportError as exc:
                self._load_error = EmbeddingProviderError(
                    "optional_dependency_missing",
                    "sentence-transformers is required for local BGE embeddings; "
                    "install requirements-rag.txt",
                )
                raise self._load_error from exc
            except EmbeddingProviderError:
                raise
            except Exception as exc:
                self._load_error = EmbeddingProviderError(
                    "model_load_failed",
                    f"BGE model could not be loaded from {self._model_path}: {exc}",
                )
                raise self._load_error from exc
            if actual_dimension != self._dimension:
                self._load_error = EmbeddingProviderError(
                    "dimension_mismatch",
                    f"expected BGE dimension {self._dimension}, got {actual_dimension}",
                )
                raise self._load_error
            self._model = model
            return model


class FakeEmbeddingProvider:
    """Deterministic, dependency-free provider for contract and index tests."""

    model_id = "fake-embedding.v1"

    def __init__(self, *, dimension: int = 8) -> None:
        if dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        self._dimension = dimension
        self.query_inputs: list[str] = []
        self.passage_inputs: list[str] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        values = [str(text) for text in texts]
        self.query_inputs.extend(values)
        return [self._vector(f"query::{text}") for text in values]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        values = [str(text) for text in texts]
        self.passage_inputs.extend(values)
        return [self._vector(f"passage::{text}") for text in values]

    def _vector(self, value: str) -> list[float]:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        raw = [
            (digest[index % len(digest)] / 255.0) * 2.0 - 1.0
            for index in range(self._dimension)
        ]
        norm = math.sqrt(sum(item * item for item in raw)) or 1.0
        return [round(item / norm, 8) for item in raw]


def _clean_texts(texts: Sequence[str]) -> list[str]:
    return [str(text).strip() for text in texts if str(text).strip()]


def _looks_like_vector(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or not value:
        return False
    return isinstance(value[0], (int, float))


import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.domain.catalog import ResourceType
from app.domain.semantics import POI_SEMANTIC_PROFILE_SCHEMA_VERSION, PoiSemanticProfile
from app.services.embedding import FakeEmbeddingProvider
from app.services.poi_semantic_profiles import PoiSemanticProfileAssembler
from app.services.retrieval_index import (
    RETRIEVAL_INDEX_SCHEMA_VERSION,
    RetrievalIndexError,
    RetrievalIndexManifest,
    RetrievalIndexStaleError,
    build_retrieval_index,
    load_and_validate_retrieval_index,
)
from tests.test_native_planning import candidate


class RetrievalIndexTest(unittest.TestCase):
    @staticmethod
    def _candidate():
        return candidate("poi-1", ResourceType.ACTIVITY, "安静展馆", ["展览"])

    @staticmethod
    def _profiles():
        return [
            PoiSemanticProfile(
                schema_version=POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
                resource_id="poi-1",
                name="安静展馆",
                summary="室内展馆",
            )
        ]

    def test_manifest_rejects_count_and_duplicate_chunk_inconsistency(self) -> None:
        base = {
            "schema_version": RETRIEVAL_INDEX_SCHEMA_VERSION,
            "model_id": "fake",
            "dimension": 2,
            "profile_schema_version": POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
            "profile_source_hash": "a" * 64,
            "candidate_count": 1,
            "chunk_count": 1,
            "query_instruction_version": "test.v1",
            "created_at": datetime.now(timezone.utc),
            "chunks": [],
        }
        with self.assertRaises(ValueError):
            RetrievalIndexManifest.model_validate(base)

    def test_index_build_and_validation_round_trip_when_numpy_is_available(self) -> None:
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy is an optional S3-H1 dependency")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            assembler = PoiSemanticProfileAssembler(self._profiles())
            provider = FakeEmbeddingProvider(dimension=4)
            manifest = build_retrieval_index(
                [self._candidate()],
                assembler=assembler,
                embedding_provider=provider,
                output_dir=output,
                created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(manifest.schema_version, RETRIEVAL_INDEX_SCHEMA_VERSION)
            self.assertEqual(manifest.model_id, provider.model_id)
            loaded, embeddings = load_and_validate_retrieval_index(
                output,
                expected_profiles=assembler.profiles.values(),
                expected_model_id=provider.model_id,
                expected_dimension=provider.dimension,
            )
            self.assertEqual(loaded.chunk_count, embeddings.shape[0])
            self.assertEqual(embeddings.shape[1], provider.dimension)

            changed = assembler.profiles["poi-1"].model_copy(
                update={"summary": "另一份语义资料"}
            )
            with self.assertRaises(RetrievalIndexStaleError):
                load_and_validate_retrieval_index(
                    output,
                    expected_profiles=[changed],
                    expected_model_id=provider.model_id,
                    expected_dimension=provider.dimension,
                )

    def test_build_reports_optional_numpy_failure_instead_of_silent_success(self) -> None:
        try:
            import numpy  # noqa: F401
        except ImportError:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(RetrievalIndexError) as context:
                    build_retrieval_index(
                        [self._candidate()],
                        assembler=PoiSemanticProfileAssembler(self._profiles()),
                        embedding_provider=FakeEmbeddingProvider(dimension=4),
                        output_dir=Path(directory),
                    )
                self.assertIn("numpy", str(context.exception))


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timezone

from app.domain.catalog import ResourceType
from app.domain.semantics import EvidenceRef, SemanticQuery, SemanticRequest
from app.domain.constraints import StopRole
from app.services.candidate_retriever import (
    HYBRID_DENSE_WEIGHT,
    HYBRID_FUSION_VERSION,
    HybridRagCandidateRetriever,
    RetrievalRequest,
)
from app.services.poi_semantic_profiles import PoiSemanticProfileAssembler
from app.services.retrieval_index import RetrievalChunk, RetrievalIndexManifest
from tests.test_native_planning import candidate


class _StaticEmbeddingProvider:
    model_id = "test-bge.v1"
    dimension = 2

    def embed_queries(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_passages(self, texts):
        return [[1.0, 0.0] if "semantic" in text else [0.0, 1.0] for text in texts]


class _BrokenEmbeddingProvider(_StaticEmbeddingProvider):
    def embed_queries(self, texts):
        raise RuntimeError("dense unavailable")


class HybridRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            candidate("lexical", ResourceType.ACTIVITY, "普通活动", ["活动"]),
            candidate("dense", ResourceType.ACTIVITY, "另一个地点", ["活动"]),
        ]
        self.assembler = PoiSemanticProfileAssembler()
        self.manifest = RetrievalIndexManifest(
            schema_version="retrieval-index.v1",
            model_id="test-bge.v1",
            dimension=2,
            profile_schema_version="poi-semantic-profile.v1",
            profile_source_hash=self.assembler.source_hash,
            candidate_count=2,
            chunk_count=2,
            query_instruction_version="test.v1",
            created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
            chunks=(
                RetrievalChunk(
                    chunk_id="lexical:summary",
                    resource_id="lexical",
                    evidence_id="poi.lexical.summary",
                    aspect_id="summary",
                    source_type="fixture",
                    source_ref="fixture://lexical",
                    text="ordinary",
                ),
                RetrievalChunk(
                    chunk_id="dense:summary",
                    resource_id="dense",
                    evidence_id="poi.dense.summary",
                    aspect_id="summary",
                    source_type="fixture",
                    source_ref="fixture://dense",
                    text="semantic profile",
                ),
            ),
        )
        self.request = RetrievalRequest(
            candidates=tuple(self.candidates),
            target_role=StopRole.ACTIVITY,
            semantic_request=SemanticRequest(
                evidence=(
                    EvidenceRef(
                        evidence_id="user.1",
                        source_type="user_message",
                        source_field="preferences",
                        summary="找个语义相关的地方",
                        confidence=1,
                    ),
                ),
                queries=(
                    SemanticQuery(
                        query_id="q1",
                        text="语义相关",
                        target_role=StopRole.ACTIVITY,
                        evidence_refs=("user.1",),
                    ),
                ),
            ),
        )

    def test_bge_fusion_uses_injected_index_and_profile_evidence(self):
        retriever = HybridRagCandidateRetriever(
            embedding_provider=_StaticEmbeddingProvider(),
            profile_assembler=self.assembler,
            index_manifest=self.manifest,
            index_embeddings=((0.0, 1.0), (1.0, 0.0)),
        )
        result = retriever.retrieve(self.request)

        self.assertEqual(result.mode, "hybrid")
        self.assertEqual(result.actual_adapter, "bge_hybrid")
        self.assertEqual(result.fusion_version, HYBRID_FUSION_VERSION)
        self.assertEqual(result.query_count, 1)
        self.assertEqual(result.items[0].candidate.resource_id, "dense")
        self.assertAlmostEqual(result.items[0].score.dense_score, 1.0)
        self.assertEqual(result.items[0].request_evidence_ids, ("user.1",))
        self.assertEqual(result.items[0].matched_profile_evidence[0].source_type, "fixture_aspect")
        self.assertGreater(result.items[0].score.final_score, HYBRID_DENSE_WEIGHT - 0.01)

    def test_dense_failure_returns_explicit_rule_fallback(self):
        retriever = HybridRagCandidateRetriever(
            embedding_provider=_BrokenEmbeddingProvider(),
            profile_assembler=self.assembler,
            index_manifest=self.manifest,
            index_embeddings=((0.0, 1.0), (1.0, 0.0)),
        )
        result = retriever.retrieve(self.request)

        self.assertEqual(result.mode, "rule")
        self.assertEqual(result.requested_mode, "hybrid")
        self.assertEqual(result.actual_adapter, "rule_based")
        self.assertEqual(result.fallback_reason, "retrieval_error")

    def test_canonical_request_requires_role(self):
        with self.assertRaises(ValueError):
            RetrievalRequest(candidates=tuple(self.candidates))


if __name__ == "__main__":
    unittest.main()

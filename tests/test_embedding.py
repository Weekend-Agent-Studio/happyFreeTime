import tempfile
import unittest
from pathlib import Path

from app.services.embedding import (
    BGE_QUERY_INSTRUCTION,
    EmbeddingProviderError,
    FakeEmbeddingProvider,
    LocalBgeEmbeddingProvider,
)


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[1.0, 0.0, 0.0] for _ in texts]


class EmbeddingProviderTest(unittest.TestCase):
    def test_fake_provider_separates_query_and_passage_calls(self) -> None:
        provider = FakeEmbeddingProvider(dimension=4)

        query = provider.embed_queries(["安静"])
        passage = provider.embed_passages(["安静"])

        self.assertEqual(len(query), 1)
        self.assertEqual(len(query[0]), 4)
        self.assertNotEqual(query, passage)
        self.assertEqual(provider.query_inputs, ["安静"])
        self.assertEqual(provider.passage_inputs, ["安静"])

    def test_local_provider_is_lazy_and_applies_bge_query_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory)
            model = _FakeModel()
            load_count = 0

            def loader(path: str, device: str):
                nonlocal load_count
                self.assertEqual(path, str(model_path))
                self.assertEqual(device, "cpu")
                load_count += 1
                return model

            provider = LocalBgeEmbeddingProvider(
                model_path=model_path,
                device="cpu",
                expected_dimension=3,
                model_loader=loader,
            )
            self.assertEqual(load_count, 0)
            provider.embed_queries(["安静"])
            provider.embed_passages(["展览"])
            self.assertEqual(load_count, 1)
            self.assertEqual(
                model.calls[0][0],
                [f"{BGE_QUERY_INSTRUCTION}安静"],
            )
            self.assertEqual(model.calls[1][0], ["展览"])
            self.assertTrue(all(call[1]["normalize_embeddings"] for call in model.calls))
            self.assertTrue(all(call[1]["convert_to_numpy"] for call in model.calls))

    def test_missing_model_path_is_an_explicit_failure(self) -> None:
        provider = LocalBgeEmbeddingProvider(
            model_path=Path(tempfile.gettempdir()) / "hft-model-does-not-exist"
        )
        with self.assertRaises(EmbeddingProviderError) as context:
            provider.embed_passages(["测试"])
        self.assertEqual(context.exception.code, "model_missing")

    def test_empty_request_does_not_load_model(self) -> None:
        provider = LocalBgeEmbeddingProvider(
            model_path=Path(tempfile.gettempdir()) / "hft-model-does-not-exist"
        )
        self.assertEqual(provider.embed_queries([]), [])


if __name__ == "__main__":
    unittest.main()


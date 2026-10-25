import json
import io
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.responses import HTMLResponse, JSONResponse
from deploy.smoke import run, SmokeFailure

from app.api.application import create_app
from app.api.public_demo import PublicDemo
from app.orchestration.entry_graph import default_environment_provider
from app.services.candidate_retriever import HybridRagCandidateRetriever
from app.services.demo_router import DemoRouter
from app.services.demo_world import DemoCatalog
from app.services.embedding import FakeEmbeddingProvider, LocalBgeEmbeddingProvider
from app.services.poi_semantic_profiles import PoiSemanticProfileAssembler
from app.services.retrieval_index import build_retrieval_index, RetrievalIndexError


def make_retriever(path):
    catalog, embedding = DemoCatalog(), FakeEmbeddingProvider()
    assembler = PoiSemanticProfileAssembler()
    build_retrieval_index(catalog.load_candidates(), assembler=assembler, embedding_provider=embedding, output_dir=path)
    return catalog, embedding, HybridRagCandidateRetriever(embedding_provider=embedding, profile_assembler=assembler, index_dir=path)


def app_for(path, catalog, retriever):
    return create_app(database_path=path, router=DemoRouter(), environment_provider=default_environment_provider,
                      catalog=catalog, candidate_retriever=retriever, prewarm_retrieval=True,
                      poi_presentation_provider=catalog.presentation_provider, public_demo=PublicDemo())


def test_asset_preparation_excludes_credentials_and_refuses_overwrite(tmp_path, monkeypatch):
    from deploy import prepare_retrieval
    model = tmp_path / "source-model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"test-fixture")
    (model / ".env").write_text("SECRET=must-not-copy")
    monkeypatch.setattr(prepare_retrieval, "LocalBgeEmbeddingProvider", lambda **kwargs: FakeEmbeddingProvider())
    output = tmp_path / "assets"
    report = prepare_retrieval.prepare(model, output)
    assert report["prewarmed"] and report["candidate_count"] == 198
    assert not (output / "model/.env").exists()
    assert set(report["files"]) == {"model/config.json", "model/model.safetensors", "index/manifest.json", "index/poi_embeddings.npz"}
    with pytest.raises(FileExistsError):
        prepare_retrieval.prepare(model, output)


def test_startup_warms_shared_retriever_before_ready(tmp_path):
    catalog, embedding, retriever = make_retriever(tmp_path / "index")
    with TestClient(app_for(tmp_path / "app.db", catalog, retriever)) as client:
        runtime = client.get("/api/config/runtime").json()["data"]["retrieval"]
        assert runtime["prewarmed"] and runtime["mode"] == "hybrid"
        assert runtime["candidate_count"] == 198
        assert len(embedding.query_inputs) == 1
        assert client.get("/api/sessions").status_code == 401


def test_startup_rejects_missing_or_catalog_mismatched_index(tmp_path):
    catalog, embedding, retriever = make_retriever(tmp_path / "index")
    manifest = tmp_path / "index/manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["assembled_profile_hash"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RetrievalIndexError, match="active catalog"):
        with TestClient(app_for(tmp_path / "stale.db", catalog, retriever)):
            pytest.fail("Stale index must never become ready")
    missing = HybridRagCandidateRetriever(embedding_provider=embedding, index_dir=tmp_path / "missing")
    with pytest.raises(RetrievalIndexError):
        with TestClient(app_for(tmp_path / "missing.db", catalog, missing)):
            pytest.fail("Missing index must never become ready")


def test_startup_rejects_encoder_fallback(tmp_path, monkeypatch):
    catalog, embedding, retriever = make_retriever(tmp_path / "index")
    from app.services.embedding import EmbeddingProviderError
    def broken(texts):
        raise EmbeddingProviderError("test_encoder_failure", "test")
    monkeypatch.setattr(embedding, "embed_queries", broken)
    with pytest.raises(RetrievalIndexError, match="refusing silent rule fallback"):
        with TestClient(app_for(tmp_path / "broken.db", catalog, retriever)):
            pytest.fail("Encoder failure must never become ready")


def test_hybrid_smoke_requires_both_ready_and_dense_turn_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT_RELEASE", "hybrid-smoke")
    catalog, _, retriever = make_retriever(tmp_path / "index")
    class Response(io.BytesIO):
        def __init__(self, response):
            super().__init__(response.content)
            self.status, self.headers = response.status_code, response.headers
    with TestClient(app_for(tmp_path / "smoke.db", catalog, retriever)) as client:
        def opener(request, timeout):
            parsed = urlsplit(request.full_url)
            if parsed.path in ("/", "/version.json"):
                static = HTMLResponse("<html>fixture</html>") if parsed.path == "/" else JSONResponse({"release": "hybrid-smoke"})
                static.content = static.body
                return Response(static)
            return Response(client.request(request.get_method(), parsed.path + ("?" + parsed.query if parsed.query else ""), content=request.data, headers=dict(request.header_items())))
        run("http://127.0.0.1:8080", require_hybrid=True, opener=opener)
        # A configured dense deployment still cannot claim live Amap when its
        # map/search providers are absent.
        with pytest.raises(SmokeFailure):
            run("http://127.0.0.1:8080", require_amap=True, opener=opener)


@pytest.mark.skipif(os.getenv("HFT_TEST_REAL_BGE") != "1", reason="Explicit local BGE asset verification")
def test_real_deployment_model_prewarm_and_two_public_planning_turns(tmp_path):
    root = Path(__file__).resolve().parents[1] / "deploy/retrieval"
    catalog = DemoCatalog()
    embedding = LocalBgeEmbeddingProvider(model_path=root / "model", device="cpu")
    retriever = HybridRagCandidateRetriever(embedding_provider=embedding, index_dir=root / "index")
    with TestClient(app_for(tmp_path / "real.db", catalog, retriever)) as client:
        runtime = client.get("/api/config/runtime").json()["data"]["retrieval"]
        assert runtime["prewarmed"] and runtime["dimension"] == 512
        loaded_model = embedding._model
        headers = {"X-Demo-Token": client.post("/api/demo/visitors").json()["data"]["token"]}
        for index in range(2):
            session = client.post("/api/sessions", headers=headers).json()["data"]["session_id"]
            response = client.post(f"/api/sessions/{session}/messages", headers=headers,
                                   json={"request_id": f"real-bge-turn-{index}", "content": "明天下午出去玩，想安静一点"})
            assert response.status_code == 200, response.text
            result = response.json()["data"]
            assert result["plans"]
            decision = next(item for item in result["runtime_decisions"] if item["stage"] == "candidate_retrieval")
            assert decision["model_invoked"] and decision["query_count"] > 0, decision
            assert decision["adapter"] in {"bge_hybrid", "mixed"}, decision
            assert not decision["fallback_reason"], decision
            assert embedding._model is loaded_model

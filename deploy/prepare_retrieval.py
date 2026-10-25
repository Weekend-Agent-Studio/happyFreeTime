"""Prepare offline, deployment-only BGE assets from an existing local model.

No .env, API credentials, user databases or remote downloads are read. The
output directory must be new so an interrupted run cannot overwrite good assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

from app.services.demo_world import DemoCatalog
from app.services.embedding import DEFAULT_MODEL_PATH, LocalBgeEmbeddingProvider
from app.services.poi_semantic_profiles import PoiSemanticProfileAssembler
from app.services.retrieval_index import build_retrieval_index
from app.services.candidate_retriever import HybridRagCandidateRetriever


def prepare(model_path: Path, output: Path) -> dict:
    started = time.perf_counter()
    # Include only model data, tokenizer settings and license/model card.
    names = ("config.json", "config_sentence_transformers.json", "modules.json",
             "sentence_bert_config.json", "tokenizer.json", "tokenizer_config.json",
             "special_tokens_map.json", "vocab.txt", "model.safetensors",
             "README.md", "LICENSE", "LICENSE.txt", "1_Pooling/config.json")
    if not (model_path / "model.safetensors").is_file() or not (model_path / "config.json").is_file():
        raise ValueError("Expected a local BGE model with config.json and model.safetensors")
    output.mkdir(parents=True, exist_ok=False)
    for name in names:
        source = model_path / name
        if source.is_file():
            if not source.resolve().is_relative_to(model_path.resolve()):
                raise ValueError("Model files must remain inside the model directory")
            destination = output / "model" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    candidates = DemoCatalog().load_candidates()
    assembler = PoiSemanticProfileAssembler()
    embedding = LocalBgeEmbeddingProvider(model_path=output / "model", device="cpu")
    build_retrieval_index(candidates, assembler=assembler, embedding_provider=embedding, output_dir=output / "index")
    retriever = HybridRagCandidateRetriever(embedding_provider=embedding, profile_assembler=assembler, index_dir=output / "index")
    report = retriever.warmup(candidates)
    report["files"] = {str(path.relative_to(output)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted(output.rglob("*")) if path.is_file()}
    report["prepare_elapsed_seconds"] = round(time.perf_counter() - started, 2)
    (output / "asset_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=ROOT / DEFAULT_MODEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "deploy/retrieval")
    args = parser.parse_args()
    report = prepare(args.model_path, args.output_dir)
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

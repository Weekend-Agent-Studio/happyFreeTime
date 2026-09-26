"""One-shot local BGE smoke check; never contacts a remote service."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.embedding import BGE_DIMENSION, LocalBgeEmbeddingProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()
    provider = LocalBgeEmbeddingProvider(
        model_path=args.model_path,
        device=args.device,
    )
    started = time.perf_counter()
    queries = provider.embed_queries(["适合父母且不太累", "雨天室内活动"])
    passages = provider.embed_passages(["室内展馆，适合慢慢参观", "湖边公园"])
    elapsed_ms = (time.perf_counter() - started) * 1000
    if provider.dimension != BGE_DIMENSION:
        raise RuntimeError(
            f"unexpected BGE dimension {provider.dimension}; expected {BGE_DIMENSION}"
        )
    print(
        f"local BGE smoke passed: model={provider.model_id} "
        f"dimension={provider.dimension} queries={len(queries)} "
        f"passages={len(passages)} elapsed_ms={elapsed_ms:.1f} network=disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


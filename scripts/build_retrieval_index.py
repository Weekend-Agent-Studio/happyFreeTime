"""Build the optional local BGE POI passage index.

This command is intentionally separate from the web application.  It may load
the optional sentence-transformers dependency and writes only ignored cache
artifacts under ``data/retrieval/cache``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.catalog import SnapshotCatalog
from app.services.embedding import LocalBgeEmbeddingProvider
from app.services.poi_semantic_profiles import DEFAULT_PROFILE_PATH, PoiSemanticProfileAssembler
from app.services.retrieval_index import (
    build_retrieval_index,
    default_retrieval_cache_dir,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles",
        type=Path,
        default=DEFAULT_PROFILE_PATH,
        help="versioned POI semantic profile JSON",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_retrieval_cache_dir(),
        help="ignored directory for npz and manifest artifacts",
    )
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--device", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    candidates = SnapshotCatalog().load_candidates()
    assembler = PoiSemanticProfileAssembler(profile_path=args.profiles)
    provider = LocalBgeEmbeddingProvider(
        model_path=args.model_path,
        device=args.device,
    )
    manifest = build_retrieval_index(
        candidates,
        assembler=assembler,
        embedding_provider=provider,
        output_dir=args.output_dir,
    )
    print(
        "built retrieval index: "
        f"model={manifest.model_id} dimension={manifest.dimension} "
        f"candidates={manifest.candidate_count} chunks={manifest.chunk_count} "
        f"output={args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

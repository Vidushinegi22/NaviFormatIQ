"""Seed domain corpora into Qdrant.

    python -m app.scripts.seed_domains [slug ...]   # default: pharma

Skips gracefully (BM25 fallback stays active) if no embeddings deployment.
"""
from __future__ import annotations

import sys

from app.rag.embedder import embeddings_available
from app.rag.indexer import index_domain_profile


def main(argv: list[str]) -> int:
    slugs = argv[1:] or ["pharma"]
    if not embeddings_available():
        print(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT not set — skipping Qdrant indexing; "
            "BM25 fallback remains active."
        )
        return 0
    for slug in slugs:
        n = index_domain_profile(slug)
        print(f"{slug}: indexed {n} chunks into Qdrant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

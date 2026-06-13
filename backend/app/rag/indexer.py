"""Index domain corpora / document text into Qdrant (skips when no embeddings)."""
from __future__ import annotations

import os
from typing import Iterable

from app.core.config import get_settings, settings
from app.core.logging import get_logger
from app.rag.chunker import chunk_text
from app.rag.embedder import embed_sync, embeddings_available
from app.vectorstore.base import VectorRecord
from app.vectorstore.factory import domain_collection, get_sync_vectorstore

log = get_logger(__name__)


def index_texts(domain_id: str, items: Iterable[tuple[str, str]]) -> int:
    """``items`` = iterable of (doc_id, text). Returns #chunks indexed."""
    if not embeddings_available():
        log.warning("Embeddings unavailable; skipping Qdrant indexing for %s", domain_id)
        return 0
    s = get_settings()
    triples: list[tuple[str, str, str]] = []  # (doc_id, chunk_id, text)
    for doc_id, text in items:
        for i, ch in enumerate(chunk_text(text)):
            triples.append((doc_id, f"{doc_id}#{i}", ch))
    if not triples:
        return 0
    vectors = embed_sync([t[2] for t in triples])
    records = [
        VectorRecord(
            id=cid,
            vector=vec,
            text=ch,
            metadata={"doc_id": did, "chunk_id": cid, "domain": domain_id},
        )
        for (did, cid, ch), vec in zip(triples, vectors)
    ]
    store = get_sync_vectorstore()
    store.ensure_collection(domain_collection(domain_id), s.embedding_dim)
    store.upsert_sync(collection=domain_collection(domain_id), records=records)
    log.info("Indexed %d chunks into %s", len(records), domain_collection(domain_id))
    return len(records)


def index_domain_profile(domain_id: str) -> int:
    """Read a domain profile's corpus dir of .txt files and index them."""
    from app.rag.bm25_client import load_domain_profile

    prof = load_domain_profile(domain_id)
    if not prof.corpus_path:
        log.info("Domain %s has no corpus_path; nothing to index.", domain_id)
        return 0
    base = settings.resolved_domain_profiles_dir()
    corpus = (
        prof.corpus_path
        if os.path.isabs(prof.corpus_path)
        else os.path.join(base, prof.corpus_path)
    )
    items: list[tuple[str, str]] = []
    for root, _, files in os.walk(corpus):
        for name in files:
            if name.lower().endswith(".txt"):
                path = os.path.join(root, name)
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        items.append((os.path.relpath(path, corpus), fh.read()))
                except Exception as e:  # noqa: BLE001
                    log.warning("Skip %s: %s", path, e)
    return index_texts(domain_id, items)

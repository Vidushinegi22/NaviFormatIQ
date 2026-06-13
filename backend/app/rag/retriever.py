"""Unified RAG retrieval: Qdrant (vector) with BM25 fallback.

Re-exports the BM25 ``DomainProfile`` / ``RagChunk`` / ``load_domain_profile``
contract so the ported ``rewriter``/``pipeline_steps`` imports resolve here.
``retrieve()`` keeps the same signature the ported code expects:
``retrieve(query, domain, top_k) -> list[RagChunk]``.

This module is import-safe before the Qdrant layer (Phase 3) exists: the
vector path is lazy-imported and any failure degrades to BM25.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.rag.bm25_client import (  # noqa: F401  (re-exported)
    DomainProfile,
    RagChunk,
    load_domain_profile,
)
from app.rag.bm25_client import retrieve as _bm25_retrieve

log = get_logger(__name__)


def _qdrant_retrieve(query: str, domain: DomainProfile, top_k: int) -> list[RagChunk]:
    """Vector retrieval over the domain's Qdrant collection (sync path)."""
    from app.rag.embedder import embed_sync
    from app.vectorstore.factory import domain_collection, get_sync_vectorstore

    vector = embed_sync([query])[0]
    store = get_sync_vectorstore()
    hits = store.search_sync(
        collection=domain_collection(domain.profile_id),
        query_vector=vector,
        k=top_k,
    )
    return [
        RagChunk(
            doc_id=str(h.metadata.get("doc_id", "")),
            chunk_id=str(h.metadata.get("chunk_id", h.id)),
            text=h.text,
            score=float(h.score),
        )
        for h in hits
    ]


def retrieve(query: str, domain: DomainProfile, top_k: int = 4) -> list[RagChunk]:
    from app.core.config import get_settings

    s = get_settings()
    if s.qdrant_configured() and s.azure_embeddings_configured():
        try:
            hits = _qdrant_retrieve(query, domain, top_k)
            if hits:
                return hits
        except Exception as e:  # pragma: no cover - falls back to BM25
            log.warning("Qdrant retrieve failed (%s); falling back to BM25.", e)
    return _bm25_retrieve(query, domain, top_k)

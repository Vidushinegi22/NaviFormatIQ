"""Embed + index a guideline's text into its own Qdrant collection, and search it.

One collection per guideline (``guideline_<code>``) keeps retrieval naturally
scoped to the selected standard — no metadata filter needed. Chunks carry their
``section_no``/``title`` so retrieved passages can be cited precisely.
"""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.chunker import chunk_text
from app.rag.embedder import embed_sync, embeddings_available
from app.vectorstore.base import SearchHit, VectorRecord
from app.vectorstore.factory import get_sync_vectorstore, guideline_collection

log = get_logger(__name__)


def index_guideline(code: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
    """Chunk + embed + upsert section text. Returns {collection, point_ids_by_section}."""
    collection = guideline_collection(code)
    if not embeddings_available():
        log.warning("Embeddings unavailable; guideline %s not indexed to Qdrant.", code)
        return {"collection": collection, "point_ids_by_section": {}, "indexed": 0}

    records: list[VectorRecord] = []
    ids_by_section: dict[str, list[str]] = {}
    for sec in sections:
        text = (sec.get("text") or "").strip()
        if not text:
            continue
        for i, ch in enumerate(chunk_text(text, chunk_size=700, overlap=80)):
            cid = f"{code}:{sec['section_no']}#{i}"
            ids_by_section.setdefault(sec["section_no"], []).append(cid)
            records.append(
                VectorRecord(
                    id=cid,
                    vector=[],  # filled after batch embed
                    text=ch,
                    metadata={
                        "code": code,
                        "section_no": sec["section_no"],
                        "title": sec.get("title", ""),
                        "chunk_id": cid,
                    },
                )
            )
    if not records:
        return {"collection": collection, "point_ids_by_section": {}, "indexed": 0}

    vectors = embed_sync([r.text for r in records])
    for r, v in zip(records, vectors):
        r.vector = v

    store = get_sync_vectorstore()
    s = get_settings()
    try:
        store.ensure_collection(collection, s.embedding_dim)
        store.upsert_sync(collection=collection, records=records)
    except Exception as e:  # noqa: BLE001
        # Vector store unreachable (e.g. suspended Qdrant cluster). The
        # requirement tree still persists to Postgres — the guideline is usable,
        # just with degraded retrieval until it's reindexed against a live Qdrant.
        log.warning(
            "Vector store unavailable; guideline %s saved without a Qdrant index "
            "(retrieval degraded until reindexed): %s",
            code,
            e,
        )
        return {"collection": collection, "point_ids_by_section": {}, "indexed": 0}
    log.info("Indexed %d chunks for guideline %s into %s", len(records), code, collection)
    return {
        "collection": collection,
        "point_ids_by_section": ids_by_section,
        "indexed": len(records),
    }


def drop_guideline_index(code: str) -> None:
    store = get_sync_vectorstore()
    drop = getattr(store, "drop_collection", None)
    if callable(drop):
        drop(guideline_collection(code))


def search_guideline(code: str, query: str, k: int = 4) -> list[SearchHit]:
    """Retrieve the most relevant guideline passages for a query (with citations)."""
    if not query or not query.strip() or not embeddings_available():
        return []
    try:
        vec = embed_sync([query])[0]
    except Exception as e:  # noqa: BLE001
        log.warning("search_guideline embed failed: %s", e)
        return []
    store = get_sync_vectorstore()
    return store.search_sync(collection=guideline_collection(code), query_vector=vec, k=k)

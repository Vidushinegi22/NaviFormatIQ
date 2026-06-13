"""Qdrant Cloud vector store (collection-per-domain, COSINE, dim from settings).

Point IDs must be uint/UUID, so string chunk-ids are mapped through uuid5 and
the original id is preserved in the payload as ``chunk_id``.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.vectorstore.base import SearchHit, VectorRecord

log = get_logger(__name__)


def _pid(raw: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


class QdrantVectorStore:
    def __init__(self) -> None:
        from qdrant_client import QdrantClient

        s = get_settings()
        self.client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key, timeout=30)
        self._ensured: set[str] = set()

    def _collections(self) -> set[str]:
        try:
            return {c.name for c in self.client.get_collections().collections}
        except Exception as e:  # noqa: BLE001
            # A suspended/deleted Qdrant cluster (or wrong URL) shouldn't crash
            # callers — treat as "no collections" so search degrades to empty
            # and ingest can skip indexing gracefully.
            log.warning("Qdrant unavailable (get_collections failed): %s", e)
            return set()

    def ensure_collection(self, collection: str, dim: int) -> None:
        if collection in self._ensured:
            return
        from qdrant_client.models import Distance, VectorParams

        if collection not in self._collections():
            self.client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            log.info("Created Qdrant collection %s (dim=%d)", collection, dim)
        self._ensured.add(collection)

    def drop_collection(self, collection: str) -> None:
        """Delete a collection (used when a guideline is reindexed/removed)."""
        try:
            if collection in self._collections():
                self.client.delete_collection(collection_name=collection)
                log.info("Dropped Qdrant collection %s", collection)
        except Exception as e:  # noqa: BLE001
            log.warning("drop_collection %s failed: %s", collection, e)
        finally:
            self._ensured.discard(collection)

    def upsert_sync(self, *, collection: str, records: list[VectorRecord]) -> None:
        from qdrant_client.models import PointStruct

        s = get_settings()
        self.ensure_collection(collection, s.embedding_dim)
        points = [
            PointStruct(
                id=_pid(r.id),
                vector=r.vector,
                payload={"text": r.text, **r.metadata},
            )
            for r in records
        ]
        self.client.upsert(collection_name=collection, points=points)

    def search_sync(
        self,
        *,
        collection: str,
        query_vector: list[float],
        k: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchHit]:
        if collection not in self._collections():
            return []
        # qdrant-client >=1.12 uses query_points (search() was removed in 1.18).
        res = self.client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=k,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for p in res.points:
            payload = dict(p.payload or {})
            text = payload.pop("text", "")
            hits.append(SearchHit(id=str(p.id), text=text, score=float(p.score), metadata=payload))
        return hits

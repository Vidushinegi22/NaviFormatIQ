"""In-memory cosine vector store — dev fallback when Qdrant isn't configured."""
from __future__ import annotations

import math
from typing import Any, Optional

from app.vectorstore.base import SearchHit, VectorRecord


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryVectorStore:
    def __init__(self) -> None:
        self._data: dict[str, list[VectorRecord]] = {}

    def ensure_collection(self, collection: str, dim: int) -> None:
        self._data.setdefault(collection, [])

    def drop_collection(self, collection: str) -> None:
        self._data.pop(collection, None)

    def upsert_sync(self, *, collection: str, records: list[VectorRecord]) -> None:
        bucket = self._data.setdefault(collection, [])
        by_id = {r.id: r for r in bucket}
        for r in records:
            by_id[r.id] = r
        self._data[collection] = list(by_id.values())

    def search_sync(
        self,
        *,
        collection: str,
        query_vector: list[float],
        k: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchHit]:
        bucket = self._data.get(collection, [])
        scored = sorted(
            ((_cosine(query_vector, r.vector), r) for r in bucket),
            key=lambda t: t[0],
            reverse=True,
        )
        return [
            SearchHit(id=r.id, text=r.text, score=float(score), metadata=dict(r.metadata))
            for score, r in scored[:k]
        ]

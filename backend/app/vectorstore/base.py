"""VectorStore protocol + payloads (sync-first; Qdrant in prod, memory in dev).

Sync-first because the unified retriever is called from ported sync code inside
``run_sync`` worker threads; async callers wrap these via ``run_sync``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class VectorRecord:
    id: str
    vector: list[float]
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    id: str
    text: str
    score: float
    metadata: dict[str, Any]


class SyncVectorStore(Protocol):
    def ensure_collection(self, collection: str, dim: int) -> None: ...
    def upsert_sync(self, *, collection: str, records: list[VectorRecord]) -> None: ...
    def search_sync(
        self,
        *,
        collection: str,
        query_vector: list[float],
        k: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchHit]: ...

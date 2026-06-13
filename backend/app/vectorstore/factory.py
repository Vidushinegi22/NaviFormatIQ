"""get_sync_vectorstore() + domain_collection() naming."""
from __future__ import annotations

import re
from functools import lru_cache

from app.core.config import get_settings
from app.vectorstore.base import SyncVectorStore


def domain_collection(domain_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (domain_id or "generic").lower()).strip("_")
    return f"domain_{slug or 'generic'}"


def guideline_collection(code: str) -> str:
    """Qdrant collection holding one guideline's text chunks (e.g. ICH-E3)."""
    slug = re.sub(r"[^a-z0-9]+", "_", (code or "guideline").lower()).strip("_")
    return f"guideline_{slug or 'guideline'}"


@lru_cache(maxsize=1)
def get_sync_vectorstore() -> SyncVectorStore:
    s = get_settings()
    if s.qdrant_configured():
        from app.vectorstore.qdrant_store import QdrantVectorStore

        return QdrantVectorStore()
    from app.vectorstore.memory_store import MemoryVectorStore

    return MemoryVectorStore()

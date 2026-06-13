"""Embeddings via Azure OpenAI (sync, batched) — text-embedding-3-large.

Sync because indexing/retrieval run inside ``run_sync`` worker threads. Async
callers should wrap with ``run_sync`` or use ``app.llm.router.get_llm().embed``.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_BATCH = 64


def embeddings_available() -> bool:
    return get_settings().azure_embeddings_configured()


def embed_sync(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if not s.azure_embeddings_configured():
        raise RuntimeError(
            "Azure embeddings not configured (set AZURE_OPENAI_EMBEDDING_DEPLOYMENT)."
        )
    from openai import AzureOpenAI

    client = AzureOpenAI(
        api_key=s.azure_openai_key,
        api_version=s.azure_openai_api_version,
        azure_endpoint=s.azure_openai_endpoint,
    )
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        batch = texts[i : i + _BATCH]
        resp = client.embeddings.create(model=s.azure_openai_embedding_deployment, input=batch)
        out.extend(d.embedding for d in resp.data)
    return out

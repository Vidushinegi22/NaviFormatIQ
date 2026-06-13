"""Domain profiles: list + (re)index corpus into Qdrant."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter

from app.core.concurrency import run_sync
from app.core.config import settings
from app.rag.embedder import embeddings_available
from app.schemas.api import DomainRead, IndexResult
from app.vectorstore.factory import domain_collection

router = APIRouter(prefix="/api/v1/domains", tags=["Domains"])


@router.get("", response_model=list[DomainRead])
async def list_domains():
    base = settings.resolved_domain_profiles_dir()
    out: list[DomainRead] = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if not name.endswith(".json"):
                continue
            slug = name[:-5]
            data = {}
            try:
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:  # noqa: BLE001
                pass
            out.append(
                DomainRead(
                    slug=slug,
                    name=data.get("name", slug),
                    has_corpus=bool(data.get("corpus_path")),
                    qdrant_collection=domain_collection(slug),
                )
            )
    return out


@router.post("/{slug}/index", response_model=IndexResult)
async def index_domain(slug: str):
    from app.rag.indexer import index_domain_profile

    n = await run_sync(index_domain_profile, slug)
    return IndexResult(slug=slug, indexed_chunks=n, embeddings_available=embeddings_available())

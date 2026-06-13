"""DocuMorph FastAPI application factory."""
from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.errors import register_error_handlers
from app.api.v1.routes import (
    chat,
    documents,
    domains,
    exports,
    flows,
    guidelines,
    projects,
    utils,
)
from app.core.config import get_settings
from app.core.db import create_all
from app.core.logging import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    try:
        await create_all()
    except Exception as e:  # noqa: BLE001
        log.warning("create_all failed (continuing): %s", e)
    if s.r2_configured():
        try:
            from app.storage import get_storage

            store = get_storage()
            if hasattr(store, "ensure_bucket"):
                store.ensure_bucket()
        except Exception as e:  # noqa: BLE001
            log.warning("R2 ensure_bucket failed: %s", e)
    log.info("DocuMorph API ready (storage=%s).", s.effective_storage_backend())
    yield


async def _health() -> dict:
    s = get_settings()
    try:
        from app.services.office.office_pipeline import available

        libre = bool(available())
    except Exception:  # noqa: BLE001
        libre = False
    return {
        "status": "ok",
        "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checks": {
            "azure_openai": s.azure_openai_configured(),
            "azure_embeddings": s.azure_embeddings_configured(),
            "qdrant": s.qdrant_configured(),
            "r2": s.r2_configured(),
            "storage_backend": s.effective_storage_backend(),
            "postgres": s.has_postgres(),
            "libreoffice": libre,
        },
    }


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Backend API", version="2.0.0", lifespan=lifespan)

    origins = s.cors_origins_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    for module in (projects, documents, flows, exports, chat, domains, guidelines, utils):
        app.include_router(module.router)

    @app.get("/healthz", tags=["System"])
    async def healthz():
        return await _health()

    return app


app = create_app()

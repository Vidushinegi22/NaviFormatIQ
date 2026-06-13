"""Centralised settings for the DocuMorph backend.

A single ``Settings`` object serves both the **ported sync services** (which
expect lowercase attributes like ``settings.azure_openai_key`` and helper
methods such as ``resolved_soffice()``) and the **new async infrastructure**
(R2 / Qdrant / Neon).  Import either ``settings`` (module-level singleton, for
the ported code) or ``get_settings()`` (for new code).
"""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent          # .../backend/app
BACKEND_DIR = APP_DIR.parent                               # .../backend


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        extra="ignore",
        case_sensitive=False,
    )

    # ── core ──────────────────────────────────────────────────────────────
    env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "*"

    # ── Azure OpenAI (chat + embeddings) — names match the ported code ─────
    azure_openai_endpoint: Optional[str] = None
    azure_openai_key: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_embedding_deployment: Optional[str] = None

    # ── Azure Document Intelligence ───────────────────────────────────────
    azure_di_endpoint: Optional[str] = None
    azure_di_key: Optional[str] = None

    # ── embeddings ────────────────────────────────────────────────────────
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 3072

    # ── Neon Postgres ─────────────────────────────────────────────────────
    neon_database_url: Optional[str] = None

    # ── Qdrant Cloud ──────────────────────────────────────────────────────
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None

    # ── Cloudflare R2 ─────────────────────────────────────────────────────
    storage_backend: str = "auto"        # auto | r2 | local
    storage_local_path: str = str(BACKEND_DIR / "data" / "uploads")
    r2_account_id: Optional[str] = None
    r2_endpoint: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket: Optional[str] = None

    # ── document processing knobs (ported code) ───────────────────────────
    soffice_bin: str = ""
    domain_profiles_dir: str = ""
    max_upload_mb: int = 100
    ocr_char_threshold: int = 50

    # ── derived: CORS ─────────────────────────────────────────────────────
    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*" or not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    # ── derived: database DSNs ────────────────────────────────────────────
    def async_database_url(self) -> str:
        """SQLAlchemy + asyncpg DSN (query string stripped; SSL via connect_args)."""
        url = self.neon_database_url or ""
        if not url:
            return "sqlite+aiosqlite:///" + str(BACKEND_DIR / "dev.db")
        base = url.split("?", 1)[0]
        for prefix in ("postgresql://", "postgres://"):
            if base.startswith(prefix):
                return "postgresql+asyncpg://" + base[len(prefix):]
        return base

    def checkpointer_dsn(self) -> Optional[str]:
        """psycopg (v3) DSN for the LangGraph Postgres checkpointer (keeps sslmode)."""
        url = self.neon_database_url or ""
        if not url:
            return None
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url

    def has_postgres(self) -> bool:
        return bool(self.neon_database_url)

    # ── derived: R2 ───────────────────────────────────────────────────────
    def r2_endpoint_url(self) -> Optional[str]:
        if self.r2_endpoint:
            return self.r2_endpoint
        if self.r2_account_id:
            return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        return None

    def r2_configured(self) -> bool:
        return bool(
            self.r2_endpoint_url()
            and self.r2_access_key_id
            and self.r2_secret_access_key
            and self.r2_bucket
        )

    def effective_storage_backend(self) -> str:
        if self.storage_backend == "r2":
            return "r2"
        if self.storage_backend == "local":
            return "local"
        # auto
        return "r2" if self.r2_configured() else "local"

    # ── derived: service availability ─────────────────────────────────────
    def azure_openai_configured(self) -> bool:
        return bool(
            self.azure_openai_endpoint
            and self.azure_openai_key
            and self.azure_openai_deployment
        )

    def azure_embeddings_configured(self) -> bool:
        return bool(
            self.azure_openai_endpoint
            and self.azure_openai_key
            and self.azure_openai_embedding_deployment
        )

    def azure_di_configured(self) -> bool:
        return bool(self.azure_di_endpoint and self.azure_di_key)

    def qdrant_configured(self) -> bool:
        return bool(self.qdrant_url and self.qdrant_api_key)

    # ── derived: paths (ported code) ──────────────────────────────────────
    def resolved_soffice(self) -> Optional[str]:
        if self.soffice_bin and os.path.exists(self.soffice_bin):
            return self.soffice_bin
        for candidate in ("soffice", "libreoffice"):
            found = shutil.which(candidate)
            if found:
                return found
        mac_default = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if os.path.exists(mac_default):
            return mac_default
        return None

    def resolved_domain_profiles_dir(self) -> str:
        if self.domain_profiles_dir:
            return self.domain_profiles_dir
        return str(APP_DIR / "data" / "domain_profiles")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level singleton for the ported services (`from app.core.config import settings`)
settings = get_settings()

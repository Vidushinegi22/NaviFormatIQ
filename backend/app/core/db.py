"""Async SQLAlchemy engine/session for Neon Postgres (sqlite fallback in dev).

Neon requires TLS; asyncpg takes the SSL context via ``connect_args`` rather
than a ``sslmode`` query param, and we disable the statement cache so a
PgBouncer-pooled endpoint can't trip over prepared statements.
"""
from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _make_engine() -> AsyncEngine:
    s = get_settings()
    url = s.async_database_url()
    connect_args: dict = {}
    if "asyncpg" in url:
        ctx = ssl.create_default_context()
        connect_args = {"ssl": ctx, "statement_cache_size": 0}
    log.info("Creating async DB engine (%s)", url.split("@")[-1] if "@" in url else url)
    return create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create all tables (dev convenience; production uses Alembic)."""
    from app.models.base import Base
    import app.models  # noqa: F401  (register all models on Base.metadata)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables ensured.")

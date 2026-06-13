"""get_storage() — singleton chosen by settings (R2 when configured, else local)."""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import StorageBackend, StoredObject  # noqa: F401


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    s = get_settings()
    if s.effective_storage_backend() == "r2":
        from app.storage.r2_store import R2Storage

        return R2Storage()
    from app.storage.local_store import LocalStorage

    return LocalStorage(s.storage_local_path)

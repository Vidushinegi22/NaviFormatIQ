"""Filesystem storage for dev / when R2 keys are absent."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.storage.base import StorageBackend, StoredObject


class LocalStorage(StorageBackend):
    scheme = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, key: str, content_type: Optional[str] = None) -> StoredObject:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return StoredObject(uri=f"local://{p.as_posix()}", key=key, bucket=None, size=len(data))

    def get(self, uri: str) -> bytes:
        assert uri.startswith("local://"), f"unsupported uri: {uri}"
        return Path(uri[len("local://"):]).read_bytes()

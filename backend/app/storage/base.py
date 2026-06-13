"""Object-storage abstraction (R2 in prod, local FS in dev).

URIs are canonical references: ``r2://<bucket>/<key>`` or ``local://<abspath>``.
``presign_*`` return ``None`` on backends that can't presign (caller proxies).
"""
from __future__ import annotations

import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO, Optional
from urllib.parse import quote
from uuid import uuid4

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
# Characters that would break an HTTP header / Content-Disposition value.
_HEADER_UNSAFE_RE = re.compile(r'[\r\n";\\]+')


def safe_filename(name: str) -> str:
    name = (name or "file").strip().replace(" ", "_")
    name = _SAFE_RE.sub("", name)
    return name or "file"


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Build a ``Content-Disposition`` header that preserves ``filename``.

    Emits both the legacy ``filename="..."`` (ASCII, for old clients) and the
    RFC 5987 ``filename*=UTF-8''...`` form so the browser saves the file under
    the name we intend — spaces and unicode included — instead of inventing one
    from the URL (e.g. a random object key on a presigned R2 URL).
    """
    disp = "inline" if inline else "attachment"
    raw = (filename or "download").strip() or "download"
    # ASCII fallback: drop header-breaking chars, then non-ASCII bytes.
    ascii_name = _HEADER_UNSAFE_RE.sub("", raw)
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii").strip(" .") or "download"
    encoded = quote(raw, safe="")
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


@dataclass
class StoredObject:
    uri: str
    key: str
    bucket: Optional[str] = None
    size: int = 0


class StorageBackend(ABC):
    scheme: str = "mem"

    def make_key(self, *, project_id: str, kind: str, filename: str) -> str:
        return f"{project_id}/{kind}/{uuid4().hex}-{safe_filename(filename)}"

    @abstractmethod
    def put(self, data: bytes, *, key: str, content_type: Optional[str] = None) -> StoredObject: ...

    @abstractmethod
    def get(self, uri: str) -> bytes: ...

    def open(self, uri: str) -> BinaryIO:
        return io.BytesIO(self.get(uri))

    def presign_get(
        self, uri: str, *, expires: int = 900, download_name: Optional[str] = None
    ) -> Optional[str]:
        return None

    def presign_put(
        self, key: str, *, content_type: Optional[str] = None, expires: int = 900
    ) -> Optional[str]:
        return None

"""Validation for user file uploads — type + size.

The goal is to reject bad uploads (wrong type, empty, oversized) *before* we pull
the whole file into memory and ship it to object storage. ``read_validated_upload``
checks the extension first (no bytes read for a wrong-type file) and stops reading
the moment an upload exceeds the size cap, so an oversized file is never fully
buffered.
"""
from __future__ import annotations

import os

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, PayloadTooLargeError

# Document types the pipeline can actually read.
ALLOWED_UPLOAD_EXTS: frozenset[str] = frozenset({"docx", "doc", "pdf", "txt"})

_READ_CHUNK = 1024 * 1024  # 1 MiB


def upload_ext(filename: str | None) -> str:
    """Lower-cased extension without the dot (``"Report.DOCX"`` → ``"docx"``)."""
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


async def read_validated_upload(
    file: UploadFile,
    *,
    allowed_exts: frozenset[str] = ALLOWED_UPLOAD_EXTS,
    max_mb: int | None = None,
) -> bytes:
    """Validate ``file`` then return its bytes.

    - Wrong type → 400 (rejected before a single byte is read).
    - Larger than ``max_mb`` (defaults to ``settings.max_upload_mb``) → 413,
      raised as soon as the cap is crossed so the oversized file isn't buffered.
    - Empty → 400.
    """
    limit_mb = max_mb if max_mb is not None else get_settings().max_upload_mb

    ext = upload_ext(file.filename)
    if ext not in allowed_exts:
        allowed = ", ".join(f".{e}" for e in sorted(allowed_exts))
        got = f".{ext}" if ext else "no extension"
        raise BadRequestError(f"Unsupported file type ({got}). Allowed types: {allowed}.")

    limit = limit_mb * 1024 * 1024

    # Fast path: the multipart parser usually knows the size already — reject an
    # oversized upload without reading it back out of the spool.
    if limit and file.size is not None and file.size > limit:
        raise PayloadTooLargeError(f"File is too large — the maximum upload size is {limit_mb} MB.")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if limit and total > limit:
            # Stop before buffering the offending chunk.
            raise PayloadTooLargeError(
                f"File is too large — the maximum upload size is {limit_mb} MB."
            )
        chunks.append(chunk)

    if total == 0:
        raise BadRequestError("The uploaded file is empty.")
    return b"".join(chunks)

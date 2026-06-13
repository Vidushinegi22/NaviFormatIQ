"""Shared helpers for LangGraph nodes."""
from __future__ import annotations

import os

from app.storage import get_storage

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"


def ext_of(uri: str | None) -> str:
    if not uri:
        return ""
    return os.path.splitext(uri.split("?", 1)[0])[1].lower().lstrip(".")


def filename_from_uri(uri: str | None, default: str = "file") -> str:
    if not uri:
        return default
    name = uri.split("?", 1)[0].rstrip("/").split("/")[-1]
    # storage keys are "<uuid32hex>-<original>"; recover the original
    head, sep, tail = name.partition("-")
    if sep and len(head) >= 16 and all(c in "0123456789abcdef" for c in head):
        name = tail
    return name or default


def load_bytes(uri: str) -> bytes:
    return get_storage().get(uri)

"""Lightweight text chunking for RAG indexing (~500-char windows)."""
from __future__ import annotations

import re


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 0) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    if overlap <= 0:
        return [
            text[i : i + chunk_size]
            for i in range(0, len(text), chunk_size)
            if text[i : i + chunk_size].strip()
        ]
    step = max(1, chunk_size - overlap)
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), step)]
    return [c for c in chunks if c.strip()]

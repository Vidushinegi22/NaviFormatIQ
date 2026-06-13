"""
Minimal RAG retriever for the hackathon MVP.

Backed by BM25 over a directory of plain-text source files. Each Domain
Profile (JSON) names a ``corpus_path`` relative to ``domain_profiles_dir``;
this module reads every ``.txt`` file under that path, splits into ~500-char
chunks, and serves top-k matches per query.

Swap-in points for production: Azure AI Search, pgvector, etc. — keep the
``retrieve()`` signature stable.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from config import settings


@dataclass
class RagChunk:
    doc_id: str
    chunk_id: str
    text: str
    score: float = 0.0


@dataclass
class DomainProfile:
    profile_id: str
    name: str
    glossary: dict[str, str]
    corpus_path: Optional[str]
    format_rules: list[str]


def load_domain_profile(profile_id: str) -> DomainProfile:
    base = settings.resolved_domain_profiles_dir()
    path = os.path.join(base, f"{profile_id}.json")
    if not os.path.exists(path):
        return DomainProfile(
            profile_id=profile_id,
            name=profile_id,
            glossary={},
            corpus_path=None,
            format_rules=[],
        )
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return DomainProfile(
        profile_id=profile_id,
        name=data.get("name", profile_id),
        glossary=data.get("glossary", {}),
        corpus_path=data.get("corpus_path"),
        format_rules=data.get("format_rules", []),
    )


def _chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size) if text[i : i + chunk_size].strip()]


@lru_cache(maxsize=8)
def _load_corpus(corpus_dir: str) -> tuple[list[RagChunk], "object"]:
    """Load every .txt under ``corpus_dir`` and build a BM25 index."""
    chunks: list[RagChunk] = []
    if not corpus_dir or not os.path.isdir(corpus_dir):
        return chunks, None

    for root, _, files in os.walk(corpus_dir):
        for name in files:
            if not name.lower().endswith(".txt"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except Exception:
                continue
            for i, ch in enumerate(_chunk_text(raw)):
                chunks.append(
                    RagChunk(
                        doc_id=os.path.relpath(path, corpus_dir),
                        chunk_id=f"{name}#{i}",
                        text=ch,
                    )
                )

    if not chunks:
        return chunks, None

    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return chunks, None

    tokenized = [_tokens(c.text) for c in chunks]
    return chunks, BM25Okapi(tokenized)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", (text or "").lower())


def retrieve(query: str, domain: DomainProfile, top_k: int = 4) -> list[RagChunk]:
    if not domain.corpus_path:
        return []
    base = settings.resolved_domain_profiles_dir()
    corpus_dir = domain.corpus_path
    if not os.path.isabs(corpus_dir):
        corpus_dir = os.path.join(base, corpus_dir)

    chunks, bm25 = _load_corpus(corpus_dir)
    if not chunks or bm25 is None:
        return []

    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    scores = bm25.get_scores(q_tokens)
    ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
    out = []
    for c, s in ranked[:top_k]:
        if s <= 0:
            continue
        out.append(RagChunk(doc_id=c.doc_id, chunk_id=c.chunk_id, text=c.text, score=float(s)))
    return out

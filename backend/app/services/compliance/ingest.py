"""Orchestrate guideline ingestion: PDF → outline → requirements → Qdrant index.

Pure of DB/HTTP — returns plain dicts the route/script layer persists. One call
(``ingest_guideline``) gives everything needed to populate the registry, the
requirement tree, and the section outline.
"""
from __future__ import annotations

from typing import Any

from app.services.compliance.embed_index import index_guideline
from app.services.compliance.pdf_outline import build_outline
from app.services.compliance.requirement_extractor import extract_requirements


def _sort_key(section_no: str, idx: int) -> str:
    """Zero-pad numeric parts so '9.4.6' sorts before '12.1' and group-by-prefix works."""
    parts = section_no.split(".")
    prefix = ".".join(f"{int(p):03d}" if p.isdigit() else p for p in parts)
    return f"{prefix}.{idx:03d}"


def ingest_guideline(code: str, pdf_bytes: bytes) -> dict[str, Any]:
    """Parse + extract + index. Returns title/sections/requirements/collection."""
    outline = build_outline(pdf_bytes)
    sections = outline["sections"]
    requirements = extract_requirements(sections)
    index = index_guideline(code, sections)
    ids_by_section: dict[str, list[str]] = index["point_ids_by_section"]

    counters: dict[str, int] = {}
    for r in requirements:
        sec = r["section_no"]
        idx = counters.get(sec, 0)
        counters[sec] = idx + 1
        r["sort_key"] = _sort_key(sec, idx)
        r["qdrant_point_ids"] = ids_by_section.get(sec, [])
    requirements.sort(key=lambda r: r["sort_key"])

    return {
        "title": outline["title"],
        "page_count": outline["page_count"],
        "collection": index["collection"],
        "indexed_chunks": index.get("indexed", 0),
        "sections": [
            {"section_no": s["section_no"], "title": s["title"], "level": s["level"]}
            for s in sections
        ],
        "requirements": requirements,
    }

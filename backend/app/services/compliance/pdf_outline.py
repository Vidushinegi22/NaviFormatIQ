"""Parse a guideline PDF into an ordered, hierarchical section outline.

ICH-E3's embedded PDF bookmarks are unusable, but its *printed* Table of
Contents is clean and regex-parseable (``1. TITLE PAGE ... 3``,
``9.4.6 Blinding ... 8``). We parse that to get the authoritative section list,
then locate each heading in the body text (searched forward, in document order)
to slice out per-section body text for requirement extraction.

Pure functions over PDF bytes — no DB, no network.
"""
from __future__ import annotations

import re
from typing import Any

# Running header repeated on every page — dropped before parsing.
_HEADER_RE = re.compile(r"structure and content of clinical study reports", re.I)
# A numbered TOC entry: "9.4.6  Blinding ......... 8"  (title may wrap lines).
_TOC_ENTRY_RE = re.compile(r"(\d+(?:\.\d+)*)\.?\s+(.+?)\s*\.{2,}\s*(\d+)\b", re.S)
# Dot-leader + trailing page number — used to detect TOC pages.
_LEADER_RE = re.compile(r"\.{2,}\s*\d+")


def extract_pages(pdf_bytes: bytes) -> list[str]:
    """Per-page plain text via PyMuPDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [doc[i].get_text("text") for i in range(doc.page_count)]
    finally:
        doc.close()


def _toc_page_indexes(pages: list[str]) -> list[int]:
    return [i for i, t in enumerate(pages) if len(_LEADER_RE.findall(t)) >= 3]


def _strip_header(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not _HEADER_RE.search(ln))


def parse_toc(pages: list[str]) -> list[dict[str, Any]]:
    """Ordered list of {section_no, title, page, level} from the printed TOC."""
    toc_idx = _toc_page_indexes(pages)
    if not toc_idx:
        return []
    flat = re.sub(r"\s+", " ", _strip_header("\n".join(pages[i] for i in toc_idx)))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in _TOC_ENTRY_RE.finditer(flat):
        num = m.group(1)
        title = m.group(2).strip(" .")
        # Guard against a stray digit-run swallowing the page number into a title.
        if not title or len(title) > 200:
            continue
        # Annexes are worked examples, not requirements, and (lacking a numeric
        # prefix) get mis-paired with page numbers — drop them.
        if title.upper().startswith("ANNEX"):
            continue
        if num in seen:  # the printed TOC lists each section once
            continue
        seen.add(num)
        out.append(
            {
                "section_no": num,
                "title": title,
                "page": int(m.group(3)),
                "level": num.count(".") + 1,
            }
        )
    return out


def _heading_pattern(entry: dict[str, Any]) -> re.Pattern[str]:
    num = re.escape(entry["section_no"])
    words = [w for w in re.split(r"\s+", entry["title"]) if w][:5]
    title_re = r"\s+".join(re.escape(w) for w in words)
    return re.compile(num + r"\.?\s+" + title_re, re.I)


def segment_sections(pages: list[str], toc: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach body text to each TOC entry by locating headings in document order."""
    toc_idx = _toc_page_indexes(pages)
    body_start = (max(toc_idx) + 1) if toc_idx else 0
    body_lines = [
        ln
        for ln in _strip_header("\n".join(pages[body_start:])).splitlines()
        if not re.fullmatch(r"\s*\d+\s*", ln)  # drop bare page-number lines
    ]
    flat = re.sub(r"\s+", " ", "\n".join(body_lines))

    # Forward scan: headings appear in the body in TOC order, so a moving cursor
    # avoids matching the title where it merely appears in prose.
    starts: list[int | None] = []
    cursor = 0
    for e in toc:
        m = _heading_pattern(e).search(flat, cursor)
        if m:
            starts.append(m.start())
            cursor = m.end()
        else:
            starts.append(None)

    out: list[dict[str, Any]] = []
    for i, e in enumerate(toc):
        pos = starts[i]
        text = ""
        if pos is not None:
            nxt = next((s for s in starts[i + 1 :] if s is not None), None)
            text = flat[pos:nxt].strip() if nxt else flat[pos:].strip()
        out.append({**e, "text": text})
    return out


def build_outline(pdf_bytes: bytes) -> dict[str, Any]:
    """Top-level entry point: {title, page_count, sections:[{section_no,title,level,text,page}]}."""
    pages = extract_pages(pdf_bytes)
    toc = parse_toc(pages)
    sections = segment_sections(pages, toc)
    # Document title = the most prominent line on the cover/first body page.
    title = "Guideline"
    for t in pages[:3]:
        for ln in t.splitlines():
            s = ln.strip()
            if len(s) > 12 and s.upper() == s and "ICH" not in s:
                title = s.title()
                break
        if title != "Guideline":
            break
    return {"title": title, "page_count": len(pages), "sections": sections}

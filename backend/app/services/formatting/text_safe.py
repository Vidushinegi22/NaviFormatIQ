"""
XML-safe text helpers.
=======================

OOXML (`.docx`) text lives in XML, and XML 1.0 forbids most C0 control
characters — only tab (0x09), line-feed (0x0A) and carriage-return (0x0D) are
allowed. python-docx / lxml raise

    ValueError: All strings must be XML compatible: Unicode or ASCII,
                no NULL bytes or control characters

the moment you try to write such a character into a run. These characters reach
us routinely: Word stores a soft line break (Shift+Enter) and page/column breaks
as the vertical-tab (0x0B) / form-feed (0x0C) code points inside text, LLM output
occasionally contains stray control bytes, and pasted content can carry NULs.

Every place that writes *externally sourced* text (rewritten bodies, reviewer
edits, delimited-table cells, derived header/footer text) must pass it through
:func:`xml_safe` first so a single bad byte can never crash a whole render.
"""
from __future__ import annotations

import re
from typing import Optional

# C0 controls that XML 1.0 forbids, EXCEPT \t (09) \n (0A) \r (0D). Also strip
# the rarely-valid C1 range and the Unicode non-characters ￾/￿.
_ILLEGAL_XML = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f￾￿]"
)


# Leading "bullet-like" markers an LLM may emit instead of "- ": unicode bullet
# glyphs, ASCII */+, or a stray control char (models sometimes output 0x10 etc.).
_PSEUDO_BULLET = re.compile(
    r"^(\s*)(?:[•◦▪‣·●○◆◇■□▸►▹–—]|[*+]|[\x01-\x08\x0b\x0c\x0e-\x1f])\s+(?=\S)"
)


def normalize_list_markers(text: Optional[str]) -> str:
    """Rewrite each line's leading pseudo-bullet marker to a clean ``- ``.

    LLM output frequently uses a unicode bullet, ``*``, or even a stray control
    character as a list marker. Normalising to ``- `` means the downstream
    list-aware renderer and diff view recognise it as a real bullet instead of
    flattening it into a paragraph."""
    if not text:
        return text or ""
    return "\n".join(
        _PSEUDO_BULLET.sub(lambda m: m.group(1) + "- ", line) for line in text.split("\n")
    )


# A line that already carries a real list marker ("- ", "1.", "a)", "•", …).
_MARKER_LINE = re.compile(r"^\s*(?:[-*+•◦▪‣·●○]|\(?\s*(?:\d{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]+)\s*[.)\]])\s+\S")
_NUMBERED_MARKER_LINE = re.compile(r"^\s*\(?\s*(?:\d{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]+)\s*[.)\]]\s+\S")


def _is_marker_line(line: str) -> bool:
    return bool(_MARKER_LINE.match(line))


def _is_numbered_marker_line(line: str) -> bool:
    return bool(_NUMBERED_MARKER_LINE.match(line))


def restore_list_structure(original: Optional[str], new: Optional[str]) -> str:
    """Re-apply list markers an LLM dropped while rewriting a list section.

    Models often return a list section as a stack of *bare* lines (no marker at
    all), which then renders as separate paragraphs and the list vanishes. When
    the ``original`` text was clearly a list (≥2 marker lines) but ``new`` lost
    the markers, we re-add bullets or numbered markers to each item line —
    preserving an opening intro paragraph and any ``…:`` lead-in. Idempotent on
    text that already has markers, and a no-op on genuine prose."""
    if not new:
        return new or ""
    orig_lines = [l for l in (original or "").split("\n") if l.strip()]
    marker_lines = sum(1 for l in orig_lines if _is_marker_line(l))
    if marker_lines < 2:
        return new  # original wasn't a list — leave prose untouched
    numbered_lines = sum(1 for l in orig_lines if _is_numbered_marker_line(l))
    prefer_numbered = numbered_lines > marker_lines / 2
    # If the original opened with a non-marker line, treat the new text's first
    # content line as an intro paragraph rather than a bullet.
    has_intro = bool(orig_lines) and not _is_marker_line(orig_lines[0])

    out: list[str] = []
    content_idx = 0
    number = 1
    for line in new.split("\n"):
        s = line.strip()
        if not s:
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip(" "))]
        if _is_marker_line(s) or s.endswith(":"):
            out.append(line)  # already a marker, or a lead-in line
        elif content_idx == 0 and has_intro:
            out.append(line)  # keep the opening intro paragraph
        elif prefer_numbered:
            out.append(f"{indent}{number}. {s}")
            number += 1
        else:
            out.append(f"{indent}- {s}")  # bare item → real bullet
        content_idx += 1
    return "\n".join(out)


def xml_safe(text: Optional[str]) -> str:
    """Return ``text`` with XML-illegal control characters removed.

    Vertical-tab / form-feed (Word soft line / page breaks) become spaces so the
    surrounding words don't run together; all other illegal control chars are
    dropped. ``None`` becomes an empty string."""
    if not text:
        return "" if text is None else text
    # Soft line break (VT) and page/column break (FF) → space (preserve spacing).
    text = text.replace("\x0b", " ").replace("\x0c", " ")
    return _ILLEGAL_XML.sub("", text)


# Matches a line that is a TODO placeholder generated by rag_fill().
_TODO_LINE = re.compile(r"^\[TODO\]\s.*$", re.MULTILINE)


def strip_todo_placeholders(text: Optional[str]) -> str:
    """Remove ``[TODO] …`` placeholder lines from section text.

    The RAG filler emits these when no reference passages match a section.
    They are useful as reviewer hints during the review step but must NOT
    appear in the final exported document.  If the entire text is a single
    TODO line, the function returns an empty string (the section stays
    empty in the output rather than carrying a stale placeholder).
    """
    if not text:
        return ""
    cleaned = _TODO_LINE.sub("", text).strip()
    return cleaned


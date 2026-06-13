"""
LLM-driven document structure refinement.

The heuristic extractors in ``word_ext.py`` and ``pdf_ext.py`` are good
enough on well-authored documents but mis-classify elements on messy
inputs — headings tagged as paragraphs, list items missed entirely,
inconsistent list levels, fragmented runs, and so on. Those mistakes
then poison the *application* step: the styler can't render a real list
out of items it doesn't know are list items.

This module sends the extracted outline to Azure OpenAI (the
``gpt-5-chat`` deployment by default) and asks it to return a list of
corrections. We then apply the corrections in-place on a deep-copied
``DocumentContent`` and hand the refined version to ``apply_styling``.

Designed to fail gracefully: if the LLM is unavailable, mis-quotes the
JSON, or proposes corrections we can't validate, the original content is
returned unchanged. The styling pipeline never blocks on the LLM call.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

from llm_client import chat_json, llm_available
from models import ContentElement, DocumentContent, ElementType, ListType


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refine_document(
    content: DocumentContent,
    *,
    max_elements: int = 250,
) -> DocumentContent:
    """Return a refined copy of ``content`` (or ``content`` itself if the LLM
    is unavailable / refuses / errors).

    ``max_elements`` caps how many elements we send in one request. Very
    long documents are chunked transparently.
    """
    if not content.elements or not llm_available():
        return content

    refined = copy.deepcopy(content)

    # Chunk large documents — each chunk is refined independently. The
    # element indices in the prompt are local to the chunk.
    elements = refined.elements
    for start in range(0, len(elements), max_elements):
        chunk = elements[start : start + max_elements]
        corrections = _ask_for_corrections(chunk)
        if not corrections:
            continue
        _apply_corrections(elements, corrections, offset=start)

    return refined


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a document-structure analyst. You will be given an outline of "
    "a document extracted from a .docx or .pdf. Each element has an index, "
    "a current type, optional heading level, optional list_type / list_level, "
    "and a short text excerpt. Some elements were misclassified by the "
    "extractor — common errors: heading paragraphs tagged as 'paragraph', "
    "list items tagged as 'paragraph', inconsistent heading levels, list "
    "items missing their list_type, or numbered/bullet items mixed together. "
    "Return ONLY a JSON object with a single key 'corrections' whose value is "
    "an array. Each correction is an object with: "
    "  idx (int, 0-based element index in the input), "
    "  type (one of 'heading','paragraph','list_item'), "
    "  level (int 1-6, only when type='heading'; otherwise null), "
    "  list_type (one of 'bullet','numbered'; only when type='list_item'; "
    "             otherwise null), "
    "  list_level (int 0-5; only when type='list_item'; otherwise null), "
    "  rationale (very short string). "
    "Include ONLY elements whose classification needs to change. Do not "
    "rewrite text content. Do not invent new elements."
)


def _ask_for_corrections(chunk: list[ContentElement]) -> Optional[list[dict]]:
    outline = _outline_for_llm(chunk)
    user = (
        "Document outline (index, type, level, list_type, list_level, text excerpt):\n"
        + json.dumps(outline, ensure_ascii=False)
        + "\n\nReturn the corrections JSON now."
    )
    raw = chat_json(_SYSTEM, user, temperature=0.1, max_tokens=3000)
    if not isinstance(raw, dict):
        return None
    corrections = raw.get("corrections")
    if not isinstance(corrections, list):
        return None
    return corrections


def _outline_for_llm(chunk: list[ContentElement]) -> list[dict[str, Any]]:
    out = []
    for idx, el in enumerate(chunk):
        text = ""
        if el.content:
            text = " ".join((r.text or "") for r in el.content).strip()
        out.append(
            {
                "idx": idx,
                "type": el.type.value if el.type else None,
                "level": el.level,
                "list_type": el.list_type.value if el.list_type else None,
                "list_level": el.list_level,
                "text": text[:140],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Applying corrections
# ---------------------------------------------------------------------------

_VALID_ELEMENT_TYPES = {"heading", "paragraph", "list_item"}
_VALID_LIST_TYPES = {"bullet", "numbered"}


def _apply_corrections(
    all_elements: list[ContentElement],
    corrections: list[dict],
    *,
    offset: int,
) -> None:
    """Validate each correction and apply it in-place to all_elements."""
    n_total = len(all_elements)
    for c in corrections:
        if not isinstance(c, dict):
            continue
        try:
            local_idx = int(c.get("idx"))
        except (TypeError, ValueError):
            continue
        idx = offset + local_idx
        if idx < 0 or idx >= n_total:
            continue

        el = all_elements[idx]
        new_type_str = c.get("type")
        if new_type_str not in _VALID_ELEMENT_TYPES:
            continue

        # Type change — convert.
        if new_type_str == "heading":
            el.type = ElementType.HEADING
            lvl = c.get("level")
            try:
                lvl_int = int(lvl) if lvl is not None else (el.level or 2)
            except (TypeError, ValueError):
                lvl_int = el.level or 2
            el.level = max(1, min(lvl_int, 9))
            # Clear list metadata if any
            el.list_type = None
            el.list_level = None
            el.bullet_char = None
            el.number_format = None
        elif new_type_str == "paragraph":
            el.type = ElementType.PARAGRAPH
            el.level = None
            el.list_type = None
            el.list_level = None
            el.bullet_char = None
            el.number_format = None
        else:  # list_item
            el.type = ElementType.LIST_ITEM
            lt = c.get("list_type")
            if lt in _VALID_LIST_TYPES:
                el.list_type = ListType(lt)
            elif el.list_type is None:
                el.list_type = ListType.BULLET
            ll = c.get("list_level")
            try:
                ll_int = int(ll) if ll is not None else (el.list_level or 0)
            except (TypeError, ValueError):
                ll_int = el.list_level or 0
            el.list_level = max(0, min(ll_int, 8))
            el.level = None

"""
Section-level rewriting + RAG-grounded gap filling.

Two entry points consumed by the orchestrator:

  rewrite_section(slot, draft_text, glossary)  → rewritten string
  rag_fill(slot, domain_profile)              → (synthesized string, source ids)

Both gracefully degrade when the LLM is unavailable: the rewriter returns
the source text unchanged, and the RAG filler returns a TODO placeholder
listing the retrieved chunk IDs.
"""

from __future__ import annotations

import re
from typing import Optional

from app.llm.adapters import chat_text, llm_available
from app.schemas.document_model import HeadingSlot
from app.rag.retriever import DomainProfile, RagChunk, retrieve


# ---------------------------------------------------------------------------
# Rewriter
# ---------------------------------------------------------------------------

_REWRITE_SYSTEM = (
    "You are a professional document-revision editor. You rewrite one section "
    "of a draft so it fits the target template's section title and voice. You "
    "may improve clarity and flow and remove redundancy, but the rewrite must "
    "stay faithful to the source — a domain reviewer comparing the two should "
    "find every fact intact.\n"
    "HARD RULES (non-negotiable):\n"
    "1. Preserve ALL numeric values, units, identifiers, codes, dates, "
    "dosages, quantities, and proper nouns VERBATIM — character for "
    "character — unless a user change request explicitly alters them. Never "
    "round, convert, abbreviate, or restate them.\n"
    "2. Never invent facts, data, figures, citations, or references that are "
    "not present in the source text or the provided context. Never drop a "
    "factual claim the source makes. Domain glossary terms must appear "
    "verbatim.\n"
    "3. If USER CHANGE REQUESTS are provided, they are the authoritative "
    "edit list: apply every one of them, and make no unrelated content "
    "changes beyond what fitting the target section requires.\n"
    "4. Match the document's established tone, tense, and terminology — keep "
    "imperative steps imperative, past-tense findings past tense; do not "
    "shift between active and passive voice styles.\n"
    "5. Output ONLY the rewritten body text — no preamble, no commentary, no "
    "explanation of your edits.\n"
    "PRESERVE STRUCTURE EXACTLY so the document re-formats cleanly:\n"
    "- Each bullet stays its own line starting with '- '; each numbered step "
    "stays its own line starting with '1.', '2.', '3.' …\n"
    "- Keep nesting: a sub-item keeps its leading indentation (two spaces per "
    "level, e.g. '  - Sent'). NEVER merge list items into one line or into a "
    "sentence, and never turn plain sentences into lists.\n"
    "- A 'Label: value' line (e.g. 'Schema: cdm', 'Table: stg_mass_email') "
    "stays its own line in the same Label: value form — do not merge these "
    "into prose.\n"
    "- Keep table rows ('| a | b |' lines) as table rows with the same "
    "columns.\n"
    "- A line like [[TABLE_1]] is a protected table placeholder: copy that "
    "line verbatim, exactly once, at its position in the flow — never "
    "remove, rewrite, or duplicate it.\n"
    "- Do NOT repeat the section title — output only the body.\n"
    "- Keep ordinary paragraphs as plain lines separated by a blank line. Do "
    "NOT output markdown headings (no '#') or JSON, and never emit an empty "
    "bullet or number."
)


_LEADING_MARKER_RE = re.compile(
    r"^\s*(?:[-*•◦▪‣]|\(?\s*(?:\d{1,3}|[ivxlcdmIVXLCDM]+|[a-zA-Z])\s*[.)\]])\s+"
)


def _strip_title_echo(text: str, title: str) -> str:
    """Drop a first line that merely repeats the section title.

    Models sometimes open the body by restating the heading ('Sub-logics for
    RULE 2:'), which then renders twice in the document. Compared after
    stripping list markers, leading section numbers, trailing colons,
    whitespace and case."""

    def norm(s: str) -> str:
        s = _LEADING_MARKER_RE.sub("", s.strip())
        s = re.sub(r"^\d+(?:\.\d+)*[\.\)]?\s+", "", s)  # leading section number
        return re.sub(r"\s+", " ", s).rstrip(":").strip().lower()

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if norm(line) == norm(title):
            return "\n".join(lines[i + 1:]).lstrip("\n")
        break
    return text


def rewrite_section(
    slot: HeadingSlot,
    draft_text: str,
    glossary: Optional[dict[str, str]] = None,
    doc_context: Optional[str] = None,
) -> str:
    """Rewrite ``draft_text`` to fit ``slot``. Returns text only.

    ``doc_context`` (document type / tone / summary) lets the model match the
    document's voice instead of guessing a generic tone."""
    glossary = glossary or {}
    if not draft_text or not draft_text.strip():
        return ""

    if not llm_available():
        return draft_text

    glossary_block = (
        "Glossary (preserve verbatim):\n"
        + "\n".join(f"- {k}: {v}" for k, v in list(glossary.items())[:50])
        if glossary
        else ""
    )

    user_msg = (
        (f"Document context: {doc_context}\n\n" if doc_context else "")
        + f"Target section title: {slot.title}\n"
        f"Expected keywords: {', '.join(slot.expected_keywords)}\n"
        f"{glossary_block}\n\n"
        f"Draft source text:\n---\n{draft_text.strip()}\n---\n\n"
        f"Rewrite the source as the body of '{slot.title}'. Keep all facts and "
        f"match the document's established tone and terminology."
    )

    # Scale the budget to the source so long sections never truncate mid-list
    # (a cut-off rewrite silently drops content from the regenerated document).
    max_tokens = min(3000, max(900, len(draft_text) // 2))
    out = chat_text(_REWRITE_SYSTEM, user_msg, temperature=0.25, max_tokens=max_tokens)
    if not out:
        return draft_text.strip()
    return _strip_title_echo(out.strip(), slot.title or "")


# ---------------------------------------------------------------------------
# RAG fill
# ---------------------------------------------------------------------------

_RAG_SYSTEM = (
    "You write one section of a regulatory report by synthesizing STRICTLY "
    "and ONLY from the provided source passages. You have no other knowledge "
    "for this task: do not draw on outside knowledge, general best practice, "
    "or assumptions — if the passages do not state it, you must not write "
    "it.\n"
    "- Every claim must trace to a passage; tag each sentence with its "
    "source id exactly as given, in the form [src:doc#chunk]. Never invent, "
    "alter, or omit a source tag.\n"
    "- Preserve numbers, units, identifiers, dates, and proper nouns from "
    "the passages verbatim.\n"
    "- If the passages are insufficient or off-topic for this section, say "
    "so in ONE sentence (e.g. 'The retrieved sources do not cover X.') "
    "instead of padding with generic filler.\n"
    "- Write bullet points on their own lines starting with '- ' and "
    "numbered steps starting with '1.', '2.' … Keep ordinary paragraphs as "
    "plain lines. No markdown headings or JSON, and never emit an empty "
    "bullet or number."
)


def rag_fill(slot: HeadingSlot, domain: DomainProfile) -> tuple[str, list[str]]:
    """Retrieve top-k passages and synthesize a section. Returns (text, source_ids)."""
    query = f"{slot.title} {' '.join(slot.expected_keywords)}"
    chunks: list[RagChunk] = retrieve(query, domain, top_k=4)
    source_ids = [f"{c.doc_id}#{c.chunk_id}" for c in chunks]

    if not chunks:
        return (
            f"[TODO] No matching reference passages found for '{slot.title}'. "
            f"A reviewer must author this section.",
            [],
        )

    if not llm_available():
        joined = "\n\n".join(f"[src:{c.doc_id}#{c.chunk_id}]\n{c.text}" for c in chunks)
        return (
            f"[DRAFT — LLM unavailable, raw retrieved passages below]\n\n{joined}",
            source_ids,
        )

    passages = "\n\n".join(f"[src:{c.doc_id}#{c.chunk_id}]\n{c.text}" for c in chunks)
    user_msg = (
        f"Section to write: {slot.title}\n"
        f"Expected keywords: {', '.join(slot.expected_keywords)}\n\n"
        f"Source passages:\n{passages}\n\n"
        f"Synthesize the section. Tag every sentence with [src:doc#chunk]."
    )
    out = chat_text(_RAG_SYSTEM, user_msg, temperature=0.2, max_tokens=900)
    return (out or "").strip(), source_ids


# ---------------------------------------------------------------------------
# Compliance gap detection
# ---------------------------------------------------------------------------

def compliance_check(
    slot: HeadingSlot, rendered_text: str, format_rules: Optional[list[str]] = None
) -> list[str]:
    """Return a list of human-readable flags for this slot."""
    flags: list[str] = []
    text = (rendered_text or "").strip()
    if slot.required and not text:
        flags.append(f"Required section '{slot.title}' is empty.")
    elif text and text.lower().startswith("[todo"):
        flags.append(f"Section '{slot.title}' is a TODO placeholder.")
    elif text and len(text) < 60:
        flags.append(f"Section '{slot.title}' is suspiciously short ({len(text)} chars).")

    if slot.expected_keywords and text:
        missing = [
            kw for kw in slot.expected_keywords if kw.lower() not in text.lower()
        ]
        if missing and len(missing) == len(slot.expected_keywords):
            flags.append(
                f"Section '{slot.title}' does not mention any expected keyword."
            )

    for rule in (format_rules or [])[:10]:
        # Very lightweight rule matching: rules of the form "must include: X"
        if rule.lower().startswith("must include:"):
            needle = rule.split(":", 1)[1].strip().lower()
            if needle and needle not in text.lower():
                flags.append(f"Format rule violated: {rule}")

    return flags

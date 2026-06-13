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

from typing import Optional

from llm_client import chat_text, llm_available
from models import HeadingSlot
from rag_client import DomainProfile, RagChunk, retrieve


# ---------------------------------------------------------------------------
# Rewriter
# ---------------------------------------------------------------------------

_REWRITE_SYSTEM = (
    "You are a precise document editor. You rewrite a section of a draft "
    "report so that it fits the target template's section title and voice "
    "while preserving every factual claim, number, and named entity from "
    "the source. Domain glossary terms must appear verbatim. Output plain "
    "text only (no markdown headings, no JSON)."
)


def rewrite_section(
    slot: HeadingSlot,
    draft_text: str,
    glossary: Optional[dict[str, str]] = None,
) -> str:
    """Rewrite ``draft_text`` to fit ``slot``. Returns text only."""
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
        f"Target section title: {slot.title}\n"
        f"Expected keywords: {', '.join(slot.expected_keywords)}\n"
        f"{glossary_block}\n\n"
        f"Draft source text:\n---\n{draft_text.strip()}\n---\n\n"
        f"Rewrite the source as the body of '{slot.title}'. Keep all facts. "
        f"Aim for a clear, professional tone."
    )

    out = chat_text(_REWRITE_SYSTEM, user_msg, temperature=0.25, max_tokens=900)
    return (out or draft_text).strip()


# ---------------------------------------------------------------------------
# RAG fill
# ---------------------------------------------------------------------------

_RAG_SYSTEM = (
    "You write a section of a regulatory report by synthesizing only from "
    "the provided source passages. Every claim must trace to a source; tag "
    "each sentence with [src:doc#chunk]. Output plain text only."
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

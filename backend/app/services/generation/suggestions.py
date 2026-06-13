"""Smart, domain-aware change suggestions for the NEXT version of a document.

Given the current draft and any prior uploaded versions, propose a SMALL set
(2-3) of SUBSTANTIVE edits a domain expert would make for the new revision —
adding a missing point to an existing section, expanding a thin section, or
adding a whole new section the document ought to have. Each suggestion is
grounded in the document's actual content, how it evolved across versions, and
domain best practice.

Explicitly NOT a proofreader: grammar, spelling, wording, punctuation, and
formatting tweaks are out of scope. When the model cannot find genuinely
substantive changes (or the LLM is unavailable) the result is an empty list and
the UI simply hides the panel.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.llm.adapters import chat_json, llm_available

log = get_logger(__name__)

_VALID_KINDS = {"add_section", "expand_section", "revise_section"}


def _outline(draft: Any, *, headings_cap: int = 40, excerpt_cap: int = 180) -> str:
    """Compact "heading: excerpt" outline of a parsed draft, bounded in size."""
    lines: list[str] = []
    for s in (getattr(draft, "sections", None) or [])[:headings_cap]:
        heading = (getattr(s, "heading", None) or "").strip()
        excerpt = " ".join((getattr(s, "text", None) or "").split())[:excerpt_cap]
        if heading and excerpt:
            lines.append(f"- {heading}: {excerpt}")
        elif heading:
            lines.append(f"- {heading}")
        elif excerpt:
            lines.append(f"- {excerpt}")
    return "\n".join(lines)


def suggest_revision_changes(
    draft: Any,
    prior_versions: list[tuple[str, Any]],
    *,
    doc_type: str = "document",
    domain: str = "",
    max_suggestions: int = 3,
) -> list[dict]:
    """Propose ≤``max_suggestions`` substantive changes for the new version.

    ``prior_versions`` is a list of ``(label, DraftStructure)`` for earlier
    uploaded versions (oldest-to-newest context on how the document evolved).
    Returns a list of ``{title, detail, section, kind}`` dicts (possibly empty).
    """
    if not llm_available():
        return []
    current = _outline(draft)
    if not current.strip():
        return []

    prior_block = ""
    for label, pv in (prior_versions or [])[:3]:
        outline = _outline(pv, headings_cap=30, excerpt_cap=90)
        if outline:
            prior_block += f"\n\n[{label}]\n{outline}"

    system = (
        f"You are a senior {domain or 'subject-matter'} expert advising the author on "
        f"the NEXT version of a {doc_type}. Propose at most {max_suggestions} "
        "SUBSTANTIVE, high-value changes for this new revision — for example adding a "
        "missing point to an existing section, expanding a section that is too thin, or "
        "adding a whole new section the document ought to contain. Ground every "
        "suggestion in the document's actual content, how it changed across the prior "
        "versions shown, and domain best practice for this kind of document.\n"
        "STRICT RULES:\n"
        "• Every suggestion must name a CONCRETE section — an exact heading from the "
        "outline, or the proposed heading for a new section — and reference what that "
        "section actually says (or fails to say). No suggestion may apply to 'the "
        "document' in general.\n"
        "• Each detail must state both WHAT to change (the specific content to add, "
        "expand, or revise) and WHY it matters for this document's readers — the risk, "
        "gap, or obligation it addresses.\n"
        "• NEVER suggest grammar, spelling, wording, punctuation, capitalisation, or "
        "formatting fixes.\n"
        "• NEVER suggest vague actions ('review', 'clarify', 'improve', 'ensure "
        "consistency', 'enhance readability') or anything that could apply to any "
        "document of this type unchanged. Each suggestion must add or change real "
        "content a reader would notice.\n"
        "• Prefer the highest-impact gaps; quality over quantity. Returning 2 strong "
        "suggestions beats a padded list of weak ones.\n"
        'Return JSON only: {"suggestions":[{'
        '"title":"<imperative, under 8 words>",'
        '"detail":"<1-2 sentences: what to change and why it matters>",'
        '"section":"<existing heading to change, or the proposed new heading>",'
        '"kind":"add_section|expand_section|revise_section"}]}. '
        "If you cannot find genuinely substantive changes, return an empty array."
    )
    user = (
        f"DOCUMENT TYPE: {doc_type}\nDOMAIN: {domain or 'general'}\n\n"
        f"CURRENT VERSION OUTLINE (heading: excerpt):\n{current}"
        + (
            f"\n\nPRIOR VERSIONS (older context — how the document evolved):{prior_block}"
            if prior_block
            else ""
        )
        + f"\n\nPropose at most {max_suggestions} substantive changes for the new version."
    )

    try:
        raw = chat_json(system, user, temperature=0.4, max_tokens=700)
    except Exception as e:  # noqa: BLE001 — suggestions are best-effort
        log.warning("revision suggestions failed: %s", e)
        return []

    out: list[dict] = []
    items = (raw or {}).get("suggestions") if isinstance(raw, dict) else None
    for s in (items or [])[:max_suggestions]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        detail = str(s.get("detail") or "").strip()
        if not title or not detail:
            continue
        section = str(s.get("section") or "").strip() or None
        kind = s.get("kind") if s.get("kind") in _VALID_KINDS else "revise_section"
        out.append(
            {
                "title": title[:80],
                "detail": detail[:280],
                "section": section[:120] if section else None,
                "kind": kind,
            }
        )
    return out

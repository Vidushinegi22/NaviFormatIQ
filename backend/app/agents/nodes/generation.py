"""Content generator node — rewrite each slot + RAG fill (ported), then apply
the user's free-text suggestions (Flow 1) via one LLM pass."""
from __future__ import annotations

import json
from typing import Any

from app.core.concurrency import run_sync
from app.core.logging import get_logger
from app.llm.base import Message
from app.llm.router import get_llm

log = get_logger(__name__)


def _build_version_context(
    uris: list[str],
    names: list[str],
    *,
    max_versions: int = 4,
    headings_cap: int = 600,
    excerpt_cap: int = 700,
    total_cap: int = 2000,
) -> str:
    """Summarise prior uploaded versions into a compact digest for the generator.

    Each context document is parsed into sections; we keep its heading outline
    plus a short text excerpt. Bounded hard (count + length) so it enriches the
    prompt without bloating every per-section rewrite call.
    """
    if not uris:
        return ""
    from app.agents.nodes.common import load_bytes
    from app.services.orchestration.pipeline_steps import structure_draft

    blocks: list[str] = []
    for uri, name in list(zip(uris, names))[:max_versions]:
        try:
            data = load_bytes(uri)
            draft = structure_draft(data, name)
        except Exception as e:  # noqa: BLE001 — context is best-effort
            log.warning("version-context parse failed for %s: %s", name, e)
            continue
        sections = draft.sections or []
        headings = ", ".join(s.heading for s in sections if s.heading)[:headings_cap]
        excerpt = " ".join((s.text or "") for s in sections).strip()[:excerpt_cap]
        block = f"— {name}:"
        if headings:
            block += f"\n  Sections: {headings}"
        if excerpt:
            block += f"\n  Excerpt: {excerpt}"
        blocks.append(block)
    if not blocks:
        return ""
    body = "\n".join(blocks)[:total_cap]
    return (
        "PRIOR VERSIONS OF THIS DOCUMENT (read-only context on how it evolved — "
        "use it to keep continuity and apply the kind of changes seen across "
        "revisions; do not copy these in wholesale):\n" + body
    )


async def _apply_suggestions(
    rewritten: dict[str, str], suggestions: str, doc_context: str | None = None
) -> dict[str, str]:
    """One LLM pass that revises only the sections the instructions touch."""
    from app.core.config import get_settings
    from app.services.orchestration.pipeline_steps import (
        _protect_tables,
        _restore_tables,
    )

    if not get_settings().azure_openai_configured():
        return rewritten
    # Tables never round-trip through the model — swap them for [[TABLE_n]]
    # placeholders per section and restore the original rows afterwards.
    table_blocks: dict[str, list[str]] = {}
    protected: dict[str, str] = {}
    for k, v in rewritten.items():
        protected[k], table_blocks[k] = _protect_tables(v)
    rewritten = protected
    llm = get_llm()
    system = (
        (f"Document context: {doc_context}\n" if doc_context else "")
        + "You are a senior domain editor revising sections of this document to carry "
        "out the user's instructions. Apply each instruction thoroughly and to EVERY "
        "section it affects — if an instruction implies a change in more than one place, "
        "update all of them, not just the first. When asked to ADD or EXPAND content, "
        "write concrete, on-topic material consistent with the document's domain and the "
        "surrounding sections — never filler, placeholders, or '[TODO]'. Preserve every "
        "existing fact, number, date and named entity unless the instruction explicitly "
        "changes it, and never invent citations, statistics, or regulatory references.\n"
        "Return a JSON object mapping slot_id -> revised_text ONLY for sections "
        "you actually changed; omit every section you leave untouched.\n"
        "CRITICAL — preserve the section's STRUCTURE so it re-formats cleanly:\n"
        "• Keep bullet points on their own lines, each starting with '- ' (or "
        "keep an existing '• '); keep numbered steps on their own lines starting "
        "with '1.', '2.', …\n"
        "• When the user asks to ADD a point to a list, append it as a NEW "
        "bullet/numbered line in the same style — never merge it into a "
        "paragraph and never drop the other bullets.\n"
        "• Keep ordinary paragraphs as plain lines separated by a blank line. "
        "Do NOT emit markdown headings ('#'), tables, or JSON inside the text, "
        "and never produce an empty bullet or number.\n"
        "• A line like [[TABLE_1]] is a protected table placeholder — keep it "
        "verbatim on its own line, exactly once, in position; never remove, "
        "rewrite, or duplicate it."
    )
    user = (
        f"Instructions:\n{suggestions}\n\n"
        "Revise only the relevant sections below, keeping each section's bullet/"
        "numbered list structure intact.\n"
        f"Sections (JSON slot_id -> text):\n{json.dumps(rewritten)[:12000]}"
    )
    try:
        comp = await llm.complete(
            [Message("system", system), Message("user", user)],
            temperature=0.3,
            json_mode=True,
        )
        changes = json.loads(comp.text or "{}")
    except Exception as e:  # noqa: BLE001
        log.warning("apply_suggestions failed: %s", e)
        changes = {}
    out = dict(rewritten)
    for k, v in changes.items():
        if isinstance(v, str) and k in out:
            out[k] = v
    return {k: _restore_tables(v, table_blocks.get(k) or []) for k, v in out.items()}


async def content_generator_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.rag.retriever import load_domain_profile
    from app.schemas.document_model import (
        DraftStructure,
        SectionMapping,
        TemplateFingerprint,
    )
    from app.services.generation.doc_profile import build_profile_and_updates
    from app.services.orchestration.pipeline_steps import (
        build_original_bodies,
        build_rewritten_bodies,
    )

    fp = TemplateFingerprint(**state["fingerprint"])
    draft = DraftStructure(**state["draft_structure"])
    mapping = SectionMapping(**state["section_mapping"])
    suggestions = state.get("user_suggestions")
    skip_ai = bool(state.get("skip_ai_rewrite"))

    # Profile the document (type/tone/summary) and plan the auto field-updates
    # (version bump, date, revision row) so the user need not edit them by hand.
    # The field-update plan is deterministic (regex over the masthead), so it
    # runs on both paths; on the manual path we skip the LLM profiling pass too.
    plan = await run_sync(
        build_profile_and_updates,
        draft,
        version_bump=state.get("version_bump", "minor"),
        target_version=state.get("target_version"),
        change_summary=(suggestions or "")[:200],
        use_llm=not skip_ai,
    )

    # Manual-edit path: carry each section's original text through verbatim so
    # the reviewer edits it by hand. The AI never touches the body, but the
    # version/date/revision-row updates above are still applied at render time.
    if skip_ai:
        rewritten, sources = await run_sync(build_original_bodies, fp, draft, mapping)
        return {
            "rewritten": rewritten,
            "sources": sources,
            "doc_profile": plan["profile"],
            "field_updates": plan["field_updates"],
            "current_agent": "content_generator",
        }

    domain = await run_sync(load_domain_profile, state.get("domain_id") or "generic")
    doc_context = plan["context_str"]

    # Fold in any prior uploaded versions so the rewrite (and the suggestion
    # pass) understand how the document changed across revisions.
    version_context = await run_sync(
        _build_version_context,
        state.get("context_file_uris") or [],
        state.get("context_file_names") or [],
    )
    if version_context:
        doc_context = f"{doc_context}\n\n{version_context}"

    rewritten, sources = await run_sync(
        build_rewritten_bodies, fp, draft, mapping, domain, doc_context
    )
    # Snapshot the freshly-built bodies (which carry the source list markers) so
    # we can restore list structure if the suggestion pass strips the markers.
    pre_edit = dict(rewritten)

    if suggestions:
        rewritten = await _apply_suggestions(rewritten, suggestions, doc_context)

    # Normalise odd bullet markers (unicode glyphs / stray control chars) to
    # "- ", then re-apply list structure the model may have dropped entirely —
    # so a section that WAS a bullet list stays one through AI edits.
    from app.services.formatting.text_safe import (
        normalize_list_markers,
        restore_list_structure,
    )
    rewritten = {
        k: restore_list_structure(pre_edit.get(k, v), normalize_list_markers(v))
        for k, v in rewritten.items()
    }

    return {
        "rewritten": rewritten,
        "sources": sources,
        "doc_profile": plan["profile"],
        "field_updates": plan["field_updates"],
        "current_agent": "content_generator",
    }

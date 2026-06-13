"""Doc-chat tools — thin sync wrappers over the ported services.

Each returns a small JSON-serializable dict. Tools that produce a file store it
and return its artifact URI. The whole agent loop runs via ``run_sync`` from the
async route, so these stay plain sync functions.
"""
from __future__ import annotations

import difflib
import functools
import io
import re
from typing import Any, Callable

from app.agents.nodes.common import DOCX_MIME, ext_of, filename_from_uri
from app.storage import get_storage


def _load(uri: str) -> bytes:
    return get_storage().get(uri)


def _extract(uri: str):
    data = _load(uri)
    name = filename_from_uri(uri, "doc")
    ext = ext_of(uri)
    if ext == "docx":
        from app.services.extraction.word_ext import extract_word_document

        return extract_word_document(file_stream=io.BytesIO(data), filename=name)
    if ext == "pdf":
        from app.services.extraction.pdf_ext import extract_pdf_document

        return extract_pdf_document(file_stream=io.BytesIO(data), filename=name)
    raise ValueError(f"unsupported document type: {ext!r}")


# A chat turn often probes the same document with several tools in a row;
# cache the expensive load+extract per uri. Small maxsize on purpose —
# extracted models can be large. Read-only tools share these; tools that
# mutate or restyle the models keep their own uncached extraction.
@functools.lru_cache(maxsize=8)
def _extract_cached(uri: str):
    return _extract(uri)


@functools.lru_cache(maxsize=8)
def _draft_cached(uri: str):
    from app.services.orchestration.pipeline_steps import structure_draft

    return structure_draft(_load(uri), filename_from_uri(uri, "doc"))


def _content_text(content) -> str:
    parts: list[str] = []
    for el in content.elements:
        if getattr(el, "content", None):
            parts.append("".join(r.text for r in el.content))
    return "\n".join(parts)


# ── tools ──────────────────────────────────────────────────────────────────

def describe_formatting(uri: str) -> dict[str, Any]:
    content, styling = _extract_cached(uri)
    ps = styling.page_style
    orient = ps.orientation.value if hasattr(ps.orientation, "value") else ps.orientation
    fonts = sorted({rs.font_name for rs in styling.run_styles.values() if rs.font_name})
    sizes = sorted({rs.font_size_pt for rs in styling.run_styles.values() if rs.font_size_pt})
    return {
        "page": {
            "width_in": ps.width_inches,
            "height_in": ps.height_inches,
            "orientation": orient,
            "margins": ps.margins.model_dump(),
        },
        "fonts": fonts[:20],
        "font_sizes_pt": sizes[:20],
        "paragraph_styles": len(styling.paragraph_styles),
        "run_styles": len(styling.run_styles),
        "elements": len(content.elements),
    }


def get_content_structure(uri: str) -> dict[str, Any]:
    ds = _draft_cached(uri)
    return {
        "sections": [
            {"index": s.index, "heading": s.heading, "level": s.level}
            for s in ds.sections
        ][:200]
    }


def get_styling_json(uri: str) -> dict[str, Any]:
    _, styling = _extract(uri)
    payload = styling.model_dump_json(exclude_none=True, indent=2).encode("utf-8")
    storage = get_storage()
    key = storage.make_key(project_id="chat", kind="styling", filename=filename_from_uri(uri, "doc") + ".styling.json")
    obj = storage.put(payload, key=key, content_type="application/json")
    return {
        "artifact_uri": obj.uri,
        "fonts": sorted({rs.font_name for rs in styling.run_styles.values() if rs.font_name})[:10],
    }


def summarize_document(uri: str) -> dict[str, Any]:
    from app.llm.adapters import chat_text

    content, _ = _extract_cached(uri)
    text = _content_text(content)[:8000]
    summary = chat_text("Summarize this document in 5 concise bullet points.", text)
    return {"summary": summary or "(LLM unavailable)"}


def profile_document(uri: str) -> dict[str, Any]:
    """High-level profile: document type, tone, summary, version + key fields."""
    from app.services.generation.doc_profile import build_profile_and_updates

    draft = _draft_cached(uri)
    p = build_profile_and_updates(draft, version_bump="minor")["profile"]
    return {
        "doc_type": p.get("doc_type"),
        "tone": p.get("tone"),
        "summary": p.get("summary"),
        "document_number": p.get("document_number"),
        "current_version": p.get("version"),
        "next_version_if_bumped": p.get("new_version"),
        "effective_date": p.get("effective_date"),
        "has_revision_history": p.get("has_revision_table"),
        "author": p.get("author"),
    }


def diff_documents(uri_a: str, uri_b: str) -> dict[str, Any]:
    a = _draft_cached(uri_a)
    b = _draft_cached(uri_b)
    ta = "\n".join(f"{s.heading or ''}\n{s.text}" for s in a.sections).splitlines()
    tb = "\n".join(f"{s.heading or ''}\n{s.text}" for s in b.sections).splitlines()
    diff = list(difflib.unified_diff(ta, tb, lineterm="", n=1))[:300]
    return {"diff": "\n".join(diff)[:6000] or "(no differences)"}


def apply_styling_to_content(content_uri: str, style_source_uri: str) -> dict[str, Any]:
    from app.services.formatting.formater_apply import apply_styling

    content, _ = _extract(content_uri)
    _, styling = _extract(style_source_uri)
    out = apply_styling(content, styling).getvalue()
    storage = get_storage()
    key = storage.make_key(project_id="chat", kind="rendered", filename="styled.docx")
    obj = storage.put(out, key=key, content_type=DOCX_MIME)
    return {"artifact_uri": obj.uri}


def transfer_style_between_docs(content_uri: str, style_uri: str) -> dict[str, Any]:
    from app.services.style.style_engine import transfer_style_smart

    outcome = transfer_style_smart(
        _load(content_uri), filename_from_uri(content_uri, "content.docx"),
        _load(style_uri), filename_from_uri(style_uri, "style"),
        mode="auto", normalize_fonts=True, promote_headings=True,
    )
    storage = get_storage()
    key = storage.make_key(project_id="chat", kind="rendered", filename="restyled.docx")
    obj = storage.put(outcome.docx_bytes, key=key, content_type=DOCX_MIME)
    return {"artifact_uri": obj.uri, "summary": outcome.summary, "structure": outcome.structure}


def retrieve_domain_context(domain_id: str, query: str) -> dict[str, Any]:
    from app.rag.retriever import load_domain_profile, retrieve

    prof = load_domain_profile(domain_id or "pharma")
    hits = retrieve(query, prof, top_k=4)
    return {"hits": [{"text": h.text[:400], "score": round(h.score, 3), "doc": h.doc_id} for h in hits]}


def search_guideline(guideline_code: str, query: str) -> dict[str, Any]:
    """Retrieve the most relevant passages of the selected guideline, with citations."""
    from app.services.compliance.embed_index import search_guideline as _sg

    hits = _sg(guideline_code, query, k=4)
    return {
        "passages": [
            {
                "section": h.metadata.get("section_no"),
                "title": h.metadata.get("title"),
                "text": h.text[:600],
                "score": round(h.score, 3),
            }
            for h in hits
        ]
    }


def get_document_section(uri: str, query: str) -> dict[str, Any]:
    """Return the document section most relevant to a query (to quote the user's own doc)."""
    ds = _draft_cached(uri)
    qtok = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) >= 3}
    best, best_score = None, -1
    for s in ds.sections:
        toks = set(re.findall(r"[a-z0-9]+", ((s.heading or "") + " " + (s.text or "")).lower()))
        sc = len(qtok & toks)
        if sc > best_score:
            best, best_score = s, sc
    if not best:
        return {"section": None}
    return {
        "section": {"heading": best.heading, "level": best.level, "text": (best.text or "")[:1500]}
    }


TOOLS: dict[str, Callable[..., dict]] = {
    "profile_document": profile_document,
    "describe_formatting": describe_formatting,
    "get_content_structure": get_content_structure,
    "get_styling_json": get_styling_json,
    "summarize_document": summarize_document,
    "diff_documents": diff_documents,
    "apply_styling_to_content": apply_styling_to_content,
    "transfer_style_between_docs": transfer_style_between_docs,
    "retrieve_domain_context": retrieve_domain_context,
    "search_guideline": search_guideline,
    "get_document_section": get_document_section,
}

TOOL_SPECS = [
    {"name": "profile_document", "args": ["uri"], "desc": "Document type, tone, summary, current/next version, doc number, author — the high-level picture. Use this first for 'what is this document / what version' questions."},
    {"name": "describe_formatting", "args": ["uri"], "desc": "Fonts, sizes, margins, page setup, style counts for a document."},
    {"name": "get_content_structure", "args": ["uri"], "desc": "Heading hierarchy / section outline of a document."},
    {"name": "get_styling_json", "args": ["uri"], "desc": "Export a document's full styling as a downloadable JSON artifact."},
    {"name": "summarize_document", "args": ["uri"], "desc": "Summarize the document's content."},
    {"name": "diff_documents", "args": ["uri_a", "uri_b"], "desc": "Show what changed between two documents (e.g. two versions)."},
    {"name": "apply_styling_to_content", "args": ["content_uri", "style_source_uri"], "desc": "Apply the styling of style_source to the content document; returns a .docx artifact."},
    {"name": "transfer_style_between_docs", "args": ["content_uri", "style_uri"], "desc": "Transfer the visual identity of style_uri onto content_uri; returns a .docx artifact."},
    {"name": "retrieve_domain_context", "args": ["domain_id", "query"], "desc": "Retrieve reference passages from a domain corpus (e.g. pharma)."},
    {"name": "search_guideline", "args": ["guideline_code", "query"], "desc": "Retrieve exact passages of the selected compliance guideline (e.g. ICH-E3) with section citations. Use to quote what the guideline requires."},
    {"name": "get_document_section", "args": ["uri", "query"], "desc": "Return the user's document section most relevant to a query, to quote their actual text."},
]

TOOL_ARGS = {s["name"]: s["args"] for s in TOOL_SPECS}

"""Turn guideline section text into atomic, checkable requirements (LLM + fallback).

Each requirement is one auditable expectation ("the title page must state the
study title") tagged with a *dimension* (what aspect it governs), a *severity*
(how bad non-compliance is), a *kind*, and an optional machine-checkable
*constraint* (e.g. a page limit). The LLM does the heavy lifting; a deterministic
heuristic backs it up when the LLM is unavailable so ingestion never fails.
"""
from __future__ import annotations

import re
from typing import Any

DIMENSIONS = ("content", "structure", "formatting", "style", "tone")
SEVERITIES = ("critical", "major", "minor", "info")
KINDS = ("presence", "content", "constraint", "formatting", "tone")

# How many sections to send to the LLM per call (bounds output size).
_BATCH = 6
_MAX_SECTION_CHARS = 2200

_SYSTEM = """You are a senior regulatory-affairs analyst. You read one section of a \
guideline (e.g. ICH E3, which governs Clinical Study Reports) and extract the \
ATOMIC, CHECKABLE requirements it imposes on a document that must comply with it.

For every distinct expectation, emit one requirement. Split bullet lists and \
compound sentences into separate atoms. Do NOT invent requirements that are not \
supported by the text.

Each requirement object has:
- section_no: the exact section number it comes from (choose from the provided list)
- title: a short imperative label (<= 12 words), e.g. "State the study title"
- requirement_text: a precise statement of what the document must contain or do
- dimension: one of content | structure | formatting | style | tone
    * content    = substantive information that must be present/correct
    * structure  = a required section/subsection or required ordering must exist
    * formatting = layout/length/numbering/tables/figures/cross-references
    * style      = wording conventions (units, terminology, capitalisation)
    * tone       = clarity, neutrality, absence of ambiguity, scientific register
- severity: one of critical | major | minor | info
    * critical = patient-safety / ethics / GCP / primary efficacy / regulatory-blocking
    * major    = a clearly mandated section or core content is missing/wrong
    * minor    = a recommended detail is missing
    * info     = advisory / "if applicable" guidance
- kind: one of presence | content | constraint | formatting | tone
- constraint: OPTIONAL machine-checkable spec, else null. Supported types:
    {"type":"max_pages","value":N,"applies_to":"<section>"}
    {"type":"min_pages","value":N}
    {"type":"defined_at_first_use"}    (abbreviations spelled out at first use)
    {"type":"cross_reference","value":"16.1.1"}  (must reference an appendix)
    {"type":"must_include","value":["x","y"]}

Return STRICT JSON: {"requirements":[ {...}, ... ]}. Use only the allowed enum \
values. Prefer 1-8 high-quality requirements per section."""

_FEWSHOT_USER = """SECTION 1 — TITLE PAGE
The title page should contain: study title; name of test drug; indication \
studied; name of the sponsor; a statement of whether the study complied with \
Good Clinical Practice (GCP).

SECTION 2 — SYNOPSIS
A brief synopsis (usually limited to 3 pages) summarising the study should be \
provided. It should include numerical data, not just text or p-values."""

_FEWSHOT_ASSISTANT = """{"requirements":[
 {"section_no":"1","title":"State the study title","requirement_text":"The title page must state the study title.","dimension":"content","severity":"major","kind":"presence","constraint":null},
 {"section_no":"1","title":"Name the test drug","requirement_text":"The title page must name the test drug / investigational product.","dimension":"content","severity":"major","kind":"presence","constraint":null},
 {"section_no":"1","title":"State the indication studied","requirement_text":"The title page must state the indication studied.","dimension":"content","severity":"minor","kind":"presence","constraint":null},
 {"section_no":"1","title":"Name the sponsor","requirement_text":"The title page must name the sponsor.","dimension":"content","severity":"minor","kind":"presence","constraint":null},
 {"section_no":"1","title":"Declare GCP compliance","requirement_text":"The title page must state whether the study was conducted in compliance with GCP, including archiving of essential documents.","dimension":"content","severity":"critical","kind":"presence","constraint":null},
 {"section_no":"2","title":"Provide a synopsis","requirement_text":"A brief synopsis summarising the study must be provided.","dimension":"structure","severity":"major","kind":"presence","constraint":null},
 {"section_no":"2","title":"Limit synopsis to 3 pages","requirement_text":"The synopsis should usually be limited to 3 pages.","dimension":"formatting","severity":"minor","kind":"constraint","constraint":{"type":"max_pages","value":3,"applies_to":"synopsis"}},
 {"section_no":"2","title":"Include numerical results","requirement_text":"The synopsis should include numerical data to illustrate results, not just text or p-values.","dimension":"content","severity":"minor","kind":"content","constraint":null}
]}"""


def _norm_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _coerce(req: dict[str, Any], valid_sections: dict[str, str]) -> dict[str, Any] | None:
    sec = str(req.get("section_no") or "").strip()
    if sec not in valid_sections:
        # Sometimes the model returns "9.4" when only "9" was offered; keep the
        # longest known prefix so the requirement still anchors to a real section.
        sec = next((s for s in sorted(valid_sections, key=len, reverse=True) if sec.startswith(s)), "")
    text = str(req.get("requirement_text") or "").strip()
    if not sec or not text:
        return None
    title = str(req.get("title") or "").strip()[:200] or text[:60]
    constraint = req.get("constraint")
    if not isinstance(constraint, dict):
        constraint = None
    return {
        "section_no": sec,
        "section_title": valid_sections[sec],
        "title": title,
        "requirement_text": text[:2000],
        "dimension": _norm_enum(req.get("dimension"), DIMENSIONS, "content"),
        "severity_default": _norm_enum(req.get("severity"), SEVERITIES, "major"),
        "requirement_kind": _norm_enum(req.get("kind"), KINDS, "content"),
        "constraint_spec": constraint,
    }


def _llm_batch(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.llm.adapters import chat_json

    valid = {m["section_no"]: m["title"] for m in members}
    lines = []
    for m in members:
        body = (m.get("text") or "").strip()[:_MAX_SECTION_CHARS]
        lines.append(f"SECTION {m['section_no']} — {m['title']}\n{body}")
    user = (
        f"{_FEWSHOT_USER}\n\n=== now extract for these sections ===\n\n"
        + "\n\n".join(lines)
    )
    # Prime the model with the few-shot by prepending it to the system prompt.
    system = f"{_SYSTEM}\n\nEXAMPLE INPUT:\n{_FEWSHOT_USER}\n\nEXAMPLE OUTPUT:\n{_FEWSHOT_ASSISTANT}"
    data = chat_json(system, user, temperature=0.1, max_tokens=4000)
    if not data or not isinstance(data.get("requirements"), list):
        return []
    out = []
    for r in data["requirements"]:
        if isinstance(r, dict):
            c = _coerce(r, valid)
            if c:
                out.append(c)
    return out


_BULLET_RE = re.compile(r"[−–\-•]\s*(.+?)(?=(?:\s[−–\-•]\s)|$)")
_PAGES_RE = re.compile(r"(?:limited to|maximum[:]?|max(?:imum)?\.?\s*)\s*(\d+)\s*page", re.I)


def _heuristic_batch(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in members:
        sec, title, text = m["section_no"], m["title"], (m.get("text") or "")
        out.append(
            {
                "section_no": sec,
                "section_title": title,
                "title": f"Include section: {title[:60]}",
                "requirement_text": f"The document must address section {sec} — {title}.",
                "dimension": "structure",
                "severity_default": "major" if m["level"] == 1 else "minor",
                "requirement_kind": "presence",
                "constraint_spec": None,
            }
        )
        pm = _PAGES_RE.search(text)
        if pm:
            out.append(
                {
                    "section_no": sec,
                    "section_title": title,
                    "title": f"Limit {title[:40]} length",
                    "requirement_text": f"{title} should be limited to {pm.group(1)} pages.",
                    "dimension": "formatting",
                    "severity_default": "minor",
                    "requirement_kind": "constraint",
                    "constraint_spec": {"type": "max_pages", "value": int(pm.group(1))},
                }
            )
        for bm in _BULLET_RE.finditer(re.sub(r"\s+", " ", text)):
            item = bm.group(1).strip()
            if 6 <= len(item) <= 240:
                out.append(
                    {
                        "section_no": sec,
                        "section_title": title,
                        "title": item[:60],
                        "requirement_text": f"The {title} should include: {item}",
                        "dimension": "content",
                        "severity_default": "minor",
                        "requirement_kind": "content",
                        "constraint_spec": None,
                    }
                )
    return out


def extract_requirements(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract atomic requirements for every section, preserving document order.

    Uses the LLM in section batches; falls back to a deterministic heuristic for
    any batch the LLM can't handle so the result is never empty.
    """
    from app.llm.adapters import llm_available

    use_llm = llm_available()
    out: list[dict[str, Any]] = []
    for i in range(0, len(sections), _BATCH):
        batch = sections[i : i + _BATCH]
        reqs = _llm_batch(batch) if use_llm else []
        if not reqs:
            reqs = _heuristic_batch(batch)
        out.extend(reqs)
    return out

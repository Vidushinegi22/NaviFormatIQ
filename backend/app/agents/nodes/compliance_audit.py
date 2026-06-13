"""Read-only compliance audit nodes (Flow: compliance).

Pipeline: extract the user document → load the guideline requirement tree →
align document sections to guideline sections → check each requirement
(LLM, fanned out by section group) → deterministic checks (page limits,
cross-references, section coverage) → aggregate severity-weighted scores →
build the report. No content is rewritten.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from app.agents.nodes.common import filename_from_uri, load_bytes
from app.core.concurrency import run_sync
from app.core.logging import get_logger
from app.services.compliance import scoring

log = get_logger(__name__)

_DIMENSIONS = ("content", "structure", "formatting", "style", "tone")
_STATUSES = ("compliant", "partial", "non_compliant", "not_applicable")
_SEVERITIES = ("critical", "major", "minor", "info")
# Constraint kinds handled deterministically (excluded from the LLM pass).
_DETERMINISTIC_TYPES = {"max_pages", "min_pages", "cross_reference", "defined_at_first_use"}
_LLM_CONCURRENCY = 6
# Documents at/under this size are sent whole to every section checker (most
# CSRs/synopses); larger ones use semantic retrieval per section instead. This
# is the key to auditing real documents whose headings don't match ICH numbering.
_WHOLE_DOC_LIMIT = 30000
_RETRIEVE_K = 10


# ── small helpers ────────────────────────────────────────────────────────────
def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 3 or w.isdigit()}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _chunk_doc(text: str) -> list[str]:
    from app.rag.chunker import chunk_text

    return chunk_text(text or "", chunk_size=900, overlap=120)


def _build_doc_index(full_text: str):
    """Embed document chunks once for per-section retrieval (large docs only).

    Returns (chunks, vectors) or None when embeddings are unavailable.
    """
    from app.rag.embedder import embeddings_available, embed_sync

    if not embeddings_available():
        return None
    chunks = _chunk_doc(full_text)
    if not chunks:
        return None
    try:
        vecs = embed_sync(chunks)
    except Exception as e:  # noqa: BLE001
        log.warning("doc embed failed, falling back to keyword context: %s", e)
        return None
    return chunks, vecs


def _retrieve(query: str, index, k: int = _RETRIEVE_K) -> str:
    """Top-k document chunks most relevant to a query (cosine), joined."""
    import math

    from app.rag.embedder import embed_sync

    chunks, vecs = index
    try:
        q = embed_sync([query])[0]
    except Exception:  # noqa: BLE001
        return "\n\n".join(chunks[:k])
    qn = math.sqrt(sum(x * x for x in q)) or 1.0
    scored = []
    for ch, v in zip(chunks, vecs):
        dot = sum(a * b for a, b in zip(q, v))
        vn = math.sqrt(sum(x * x for x in v)) or 1.0
        scored.append((dot / (qn * vn), ch))
    scored.sort(key=lambda t: t[0], reverse=True)
    return "\n\n".join(ch for _, ch in scored[:k])


def _norm_enum(v: Any, allowed: tuple[str, ...], default: str) -> str:
    s = str(v or "").strip().lower()
    return s if s in allowed else default


_SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def _bounded_severity(proposed: str, default: str) -> str:
    """Allow the auditor LLM to move severity at most ONE step from the
    requirement's calibrated default — prevents wholesale drift (e.g. an LLM
    quietly downgrading every critical to minor)."""
    if proposed == default:
        return default
    p, d = _SEV_RANK.get(proposed), _SEV_RANK.get(default)
    if p is None or d is None:
        return default
    return proposed if abs(p - d) <= 1 else default


_QUOTE_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                              "–": "-", "—": "-", " ": " "})


def _norm_quote(s: str) -> str:
    """Canonical form for quote matching: lowercase, ASCII punctuation collapsed."""
    s = (s or "").translate(_QUOTE_TRANS).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _grounded(evidence: str, doc_norm: str) -> bool:
    """True iff the evidence quote actually occurs in the document (modulo
    whitespace/punctuation/casing). Elided quotes pass if their head or tail
    fragment is found verbatim."""
    ev = _norm_quote(evidence)
    if not ev:
        return False
    if len(ev) <= 20:  # too short to verify meaningfully — accept page refs etc.
        return True
    if ev in doc_norm:
        return True
    head, tail = ev[:60].strip(), ev[-60:].strip()
    return head in doc_norm or tail in doc_norm


async def _complete_json(system: str, user: str, *, max_tokens: int = 3000) -> dict:
    """One JSON LLM call with a tiny retry; returns {} on failure."""
    from app.llm.base import Message
    from app.llm.router import get_llm

    llm = get_llm()
    for attempt in range(2):
        try:
            comp = await llm.complete(
                [Message(role="system", content=system), Message(role="user", content=user)],
                temperature=0.1,
                max_tokens=max_tokens,
                json_mode=True,
            )
            return json.loads(comp.text or "{}")
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                log.warning("compliance LLM call failed: %s", e)
                return {}
            await asyncio.sleep(1.0 + attempt)
    return {}


# ── 1. extract user document ─────────────────────────────────────────────────
async def extract_user_doc_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.services.orchestration.pipeline_steps import structure_draft

    uri = state["draft_file_uri"]
    name = filename_from_uri(uri, "document.docx")
    data = await run_sync(load_bytes, uri)
    draft = await run_sync(structure_draft, data, name)
    dump = draft.model_dump(mode="json")
    sections = [
        {
            "index": s.get("index", i),
            "heading": s.get("heading") or "",
            "level": s.get("level", 1),
            "text": s.get("text") or "",
            "page_range": s.get("page_range"),
        }
        for i, s in enumerate(dump.get("sections", []))
    ]
    full_text = "\n\n".join(
        ((s["heading"] + "\n") if s["heading"] else "") + s["text"] for s in sections
    )
    return {
        "doc_sections": sections,
        "doc_meta": dump.get("metadata") or {},
        "full_text": full_text,
        "current_agent": "extract_user_doc",
    }


# ── 2. load guideline ────────────────────────────────────────────────────────
async def load_guideline_node(state: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core.db import get_sessionmaker
    from app.models.compliance import Guideline, GuidelineRequirement

    gid = state.get("guideline_id")
    if not gid:
        return {"guideline_ref": None, "warnings": ["no guideline_id provided"], "current_agent": "load_guideline"}

    dims = set(state.get("dimensions") or [])
    sm = get_sessionmaker()
    async with sm() as s:
        g = await s.get(Guideline, uuid.UUID(str(gid)))
        if not g:
            return {"guideline_ref": None, "warnings": [f"guideline {gid} not found"], "current_agent": "load_guideline"}
        rows = (
            await s.execute(
                select(GuidelineRequirement)
                .where(GuidelineRequirement.guideline_id == g.id, GuidelineRequirement.enabled == True)  # noqa: E712
                .order_by(GuidelineRequirement.sort_key)
            )
        ).scalars().all()
        reqs = []
        for r in rows:
            if dims and r.dimension not in dims:
                continue
            reqs.append(
                {
                    "id": str(r.id),
                    "section_no": r.section_no,
                    "section_title": "",
                    "title": r.title,
                    "requirement_text": r.requirement_text,
                    "dimension": r.dimension,
                    "severity_default": r.severity_default,
                    "requirement_kind": r.requirement_kind,
                    "constraint_spec": r.constraint_spec,
                }
            )
        sections = (g.meta or {}).get("sections", [])
        sec_titles = {s.get("section_no"): s.get("title", "") for s in sections}
        for r in reqs:
            r["section_title"] = sec_titles.get(r["section_no"], "")
        ref = {
            "id": str(g.id),
            "code": g.code,
            "title": g.title,
            "version": g.version,
            "collection": g.qdrant_collection,
            "sections": sections,
            "requirements": reqs,
        }
    return {"guideline_ref": ref, "current_agent": "load_guideline"}


# ── 3. align document sections to guideline sections ─────────────────────────
async def align_sections_node(state: dict[str, Any]) -> dict[str, Any]:
    ref = state.get("guideline_ref") or {}
    g_sections = ref.get("sections", [])
    doc_sections = state.get("doc_sections", [])

    doc_tok = [(d, _tokens(d["heading"])) for d in doc_sections]
    alignment: dict[str, Any] = {}
    for gs in g_sections:
        sec_no = gs.get("section_no")
        if not sec_no:
            continue
        g_tok = _tokens(gs.get("title", ""))
        best, best_score = None, 0.0
        for d, dtok in doc_tok:
            head = d["heading"].strip()
            score = 0.0
            if head.startswith(sec_no + " ") or head.startswith(sec_no + ".") or head == sec_no:
                score = 1.0
            else:
                score = _overlap(g_tok, dtok)
            if score > best_score:
                best, best_score = d, score
        if best is not None and best_score >= 0.5:
            pr = best.get("page_range") or []
            pages = (pr[1] - pr[0] + 1) if len(pr) == 2 and all(isinstance(x, int) for x in pr) else None
            alignment[sec_no] = {
                "doc_idx": best["index"],
                "heading": best["heading"],
                "text": best["text"],
                "score": round(best_score, 3),
                "pages": pages,
            }
    return {"alignment": alignment, "current_agent": "align_sections"}


# ── 4. check requirements (LLM, grouped by top-level section) ────────────────
_CHECK_SYSTEM = """You are a senior regulatory-affairs auditor performing a formal compliance \
audit of a DOCUMENT against the guideline {code} (which governs clinical study reports). Your \
findings go into a professional audit report, so they must be precise, evidence-grounded and \
actionable — never speculative.

You are given the DOCUMENT (or its most relevant parts) and a list of requirements. The \
document may use DIFFERENT section names/numbering, or be a SUMMARY/SYNOPSIS — so SEARCH \
THE WHOLE TEXT for the substance of each requirement; do NOT expect it under a matching \
heading. Judge on substance, with evidence.

Return STRICT JSON: {{"findings":[{{
  "id": "<the requirement id you were given>",
  "status": "compliant|partial|non_compliant|not_applicable",
  "severity": "critical|major|minor|info",
  "confidence": 0.0-1.0,
  "evidence": "<<=240-char VERBATIM quote copied character-for-character FROM THE DOCUMENT, or empty if absent>",
  "doc_location": "<the document heading/paragraph where it appears, or where it was expected>",
  "rationale": "<1-2 sentences: what you looked for, what you found, why this status>",
  "citation": {{"guideline_section": "<section no>", "quote": "<short guideline text>"}},
  "suggested_fix": "<for partial/non_compliant: a concrete, specific edit — WHAT to add/change and WHERE. Empty only for compliant/not_applicable>"
}}]}}

Status rules:
- compliant: the document clearly and fully satisfies it (quote the evidence).
- partial: the topic IS addressed but at a summary level or missing some of the detail the \
requirement asks for. This is COMMON when the document is a synopsis/summary — prefer \
partial over non_compliant whenever the document touches the topic at all (quote the evidence).
- non_compliant: the topic is genuinely not addressed ANYWHERE in the document.
- not_applicable: the requirement is conditional and does not apply to THIS study, OR it \
concerns a full-report component a summary/synopsis legitimately omits (e.g. appendices, \
case report forms, individual patient listings, raw data tabulations). Prefer not_applicable \
over non_compliant for clearly out-of-scope components.

Evidence rules (an audit finding without real evidence is worthless):
- evidence MUST be copied verbatim from the DOCUMENT, never paraphrased, never from the \
guideline. Quotes are machine-verified against the document; fabricated quotes are discarded \
and discredit the finding.
- For compliant/partial you MUST quote evidence. For non_compliant leave evidence empty.

Severity: keep the severity you were given for the requirement. Move it at most one step, \
and only when the document's specific context clearly warrants it (say why in the rationale).

Confidence calibration (be honest — downstream weighting uses this):
- 0.9-1.0: explicit verbatim evidence found (or its absence is certain after a full search).
- 0.7-0.8: clear judgement from related passages; minor interpretation involved.
- 0.5-0.6: judgement call — topic is touched ambiguously, or the provided excerpt may be incomplete.
- <0.5: you could not properly assess this requirement from the text given.

One finding per id; never invent ids; never skip an id you were given."""


async def _check_group(
    code: str,
    group_title: str,
    reqs: list[dict],
    doc_ctx: str,
    doc_norm: str,
    sem: asyncio.Semaphore,
) -> list[dict]:
    from app.services.compliance.embed_index import search_guideline

    # Guideline passages to ground the citations for this section group.
    query = (group_title + " " + " ".join(r["title"] for r in reqs[:4]))[:400]
    hits = await run_sync(search_guideline, code, query, 3)
    g_ctx = "\n".join(f"[{h.metadata.get('section_no','?')}] {h.text}" for h in hits)[:1600]

    req_lines = [
        json.dumps(
            {
                "id": r["id"],
                "section_no": r["section_no"],
                "title": r["title"],
                "requirement": r["requirement_text"],
                "dimension": r["dimension"],
                "severity": r["severity_default"],
            },
            ensure_ascii=False,
        )
        for r in reqs
    ]
    user = (
        f"GUIDELINE CONTEXT (for citations):\n{g_ctx or '(none retrieved)'}\n\n"
        f"DOCUMENT (search ALL of it — headings may differ):\n"
        f"{doc_ctx or '(no document text available)'}\n\n"
        f"REQUIREMENTS TO CHECK — guideline section {group_title} (one finding each):\n"
        + "\n".join(req_lines)
    )
    system = _CHECK_SYSTEM.format(code=code)

    async with sem:
        data = await _complete_json(system, user, max_tokens=4000)

    by_id = {str(f.get("id")): f for f in data.get("findings", []) if isinstance(f, dict)}
    missing = [r["id"] for r in reqs if r["id"] not in by_id]
    if missing:
        log.warning(
            "compliance check: auditor returned no verdict for %d/%d requirements in group %r",
            len(missing), len(reqs), group_title,
        )
    out: list[dict] = []
    for r in reqs:
        f = by_id.get(r["id"])
        citation = (f or {}).get("citation") if isinstance((f or {}).get("citation"), dict) else None
        if not citation:
            citation = {"guideline_section": r["section_no"], "quote": r["requirement_text"][:200]}
        base = {
            "requirement_id": r["id"],
            "section_no": r["section_no"],
            "section_title": r["section_title"],
            "requirement_title": r["title"],
            "dimension": r["dimension"],
            "citation": citation,
        }
        if f is None:
            # The auditor returned no verdict for this requirement. Be honest:
            # report it as unassessed (excluded from scoring) rather than
            # fabricating a failure with made-up confidence.
            out.append(
                {
                    **base,
                    "status": "not_applicable",
                    "severity": r["severity_default"],
                    "confidence": 0.0,
                    "evidence": None,
                    "doc_location": None,
                    "rationale": "Not assessed — the auditor did not return a verdict for this requirement.",
                    "suggested_fix": None,
                }
            )
            continue

        status = _norm_enum(f.get("status"), _STATUSES, "non_compliant")
        try:
            confidence = max(0.0, min(1.0, float(f.get("confidence"))))
        except (TypeError, ValueError):
            confidence = 0.6
        evidence = (str(f.get("evidence") or "")[:500]) or None
        rationale = (str(f.get("rationale") or "")[:600]) or None

        # Ground the quote: a finding citing text that isn't in the document is
        # a hallucination — strip the quote and discount the verdict.
        if evidence and not _grounded(evidence, doc_norm):
            log.warning("ungrounded evidence dropped for requirement %s (%s)", r["id"], r["title"])
            evidence = None
            confidence = min(confidence, 0.4)
            rationale = ((rationale or "") + " [Quoted evidence could not be located in the document and was removed.]").strip()[:700]

        out.append(
            {
                **base,
                "status": status,
                "severity": _bounded_severity(
                    _norm_enum(f.get("severity"), _SEVERITIES, r["severity_default"]),
                    r["severity_default"],
                ),
                "confidence": confidence,
                "evidence": evidence,
                "doc_location": (str(f.get("doc_location") or "")[:240]) or None,
                "rationale": rationale,
                "suggested_fix": (str(f.get("suggested_fix") or "")[:800]) or None,
            }
        )
    return out


async def check_requirements_node(state: dict[str, Any]) -> dict[str, Any]:
    ref = state.get("guideline_ref") or {}
    code = ref.get("code", "")
    full_text = (state.get("full_text") or "").strip()
    reqs = ref.get("requirements", [])
    # LLM handles everything except the deterministically-checkable constraints.
    llm_reqs = [
        r
        for r in reqs
        if not (isinstance(r.get("constraint_spec"), dict)
                and r["constraint_spec"].get("type") in _DETERMINISTIC_TYPES)
    ]

    groups: dict[str, list[dict]] = {}
    for r in llm_reqs:
        groups.setdefault(scoring.top_section(r["section_no"]), []).append(r)
    sec_title = {s.get("section_no"): s.get("title", "") for s in ref.get("sections", [])}

    # Robust content delivery: send the whole document to each section checker
    # for small files (the LLM searches it all, regardless of heading names);
    # for large files, embed once and retrieve the most relevant chunks per group.
    whole_doc = full_text[:_WHOLE_DOC_LIMIT]
    index = await run_sync(_build_doc_index, full_text) if len(full_text) > _WHOLE_DOC_LIMIT else None
    doc_norm = _norm_quote(full_text)  # for grounding evidence quotes

    sem = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def _run_group(top: str, grp: list[dict]) -> list[dict]:
        if index is None:
            ctx = whole_doc
        else:
            q = f"{top} {sec_title.get(top, '')} " + " ".join(r["title"] for r in grp[:6])
            ctx = await run_sync(_retrieve, q, index)
        return await _check_group(code, sec_title.get(top, "") or top, grp, ctx, doc_norm, sem)

    results = await asyncio.gather(*(_run_group(t, g) for t, g in groups.items()), return_exceptions=True)
    findings: list[dict] = []
    for res in results:
        if isinstance(res, list):
            findings.extend(res)
        else:
            log.warning("check group failed: %s", res)
    return {"findings": findings, "current_agent": "check_requirements"}


# ── 5. deterministic checks (page limits, cross-refs, section coverage) ──────
def _det_finding(r: dict, status: str, *, evidence=None, rationale=None, fix=None, confidence: float = 0.95) -> dict:
    return {
        "requirement_id": r["id"],
        "section_no": r["section_no"],
        "section_title": r["section_title"],
        "requirement_title": r["title"],
        "dimension": r["dimension"],
        "status": status,
        "severity": r["severity_default"],
        "confidence": confidence,
        "evidence": evidence,
        "doc_location": r["section_title"] or r["section_no"],
        "rationale": rationale,
        "citation": {"guideline_section": r["section_no"], "quote": r["requirement_text"][:200]},
        "suggested_fix": fix,
    }


async def deterministic_checks_node(state: dict[str, Any]) -> dict[str, Any]:
    ref = state.get("guideline_ref") or {}
    alignment = state.get("alignment") or {}
    full_text = (state.get("full_text") or "")
    reqs = ref.get("requirements", [])
    extra: list[dict] = []

    for r in reqs:
        cs = r.get("constraint_spec")
        if not isinstance(cs, dict):
            continue
        ctype = cs.get("type")
        if ctype not in _DETERMINISTIC_TYPES:
            continue
        a = alignment.get(r["section_no"])

        if ctype == "max_pages":
            pages = a.get("pages") if a else None
            limit = cs.get("value")
            if pages is None or not isinstance(limit, (int, float)):
                extra.append(_det_finding(r, "not_applicable", rationale="Page count for this section could not be measured."))
            elif pages <= limit:
                extra.append(_det_finding(r, "compliant", evidence=f"≈{pages} page(s)", rationale=f"Within the {limit}-page limit."))
            else:
                extra.append(_det_finding(r, "non_compliant", evidence=f"≈{pages} pages", rationale=f"Exceeds the {limit}-page limit.", fix=f"Condense to ≤ {limit} pages."))
        elif ctype == "min_pages":
            pages = a.get("pages") if a else None
            limit = cs.get("value")
            if pages is None or not isinstance(limit, (int, float)):
                extra.append(_det_finding(r, "not_applicable", rationale="Page count for this section could not be measured."))
            else:
                extra.append(_det_finding(r, "compliant" if pages >= limit else "partial", evidence=f"≈{pages} page(s)"))
        elif ctype == "cross_reference":
            target = str(cs.get("value") or "")
            present = bool(target and re.search(re.escape(target), full_text))
            if present:
                extra.append(_det_finding(r, "compliant", evidence=f"references {target}", rationale=f"Appendix {target} is referenced."))
            else:
                extra.append(_det_finding(r, "non_compliant", rationale=f"No reference to appendix {target} found.", fix=f"Cross-reference appendix {target}."))
        elif ctype == "defined_at_first_use":
            has_abbrev = bool(re.search(r"abbreviation", full_text, re.I))
            extra.append(
                _det_finding(
                    r,
                    "compliant" if has_abbrev else "partial",
                    rationale="An abbreviations section appears to be present (keyword match); "
                    "definition-at-first-use was not individually verified."
                    if has_abbrev
                    else "No clear abbreviations list found.",
                    fix=None if has_abbrev else "Add a list of abbreviations and define each at first use.",
                    confidence=0.6,  # keyword heuristic, not a true per-term check
                )
            )

    # NB: structure-coverage findings are derived later (in aggregate), AFTER
    # verify_findings, so they reflect final statuses.
    findings = list(state.get("findings") or []) + extra
    return {"findings": findings, "current_agent": "deterministic_checks"}


def _structure_coverage(
    ref: dict[str, Any], findings: list[dict], alignment: dict[str, Any] | None = None
) -> list[dict]:
    """Flag top-level guideline sections with NO trace in the document.

    Only emits gap rows. Synthetic "compliant" rows are deliberately not
    produced: the requirement-level findings already carry the positive
    signal, and padding the pool with free compliant rows both inflates and
    dilutes the severity-weighted score. A section counts as present when the
    auditor found content for it (compliant/partial) OR a document heading
    aligned to it.
    """
    alignment = alignment or {}
    present_tops: set[str] = {
        scoring.top_section(f.get("section_no"))
        for f in findings
        if f.get("status") in ("compliant", "partial")
    }
    present_tops |= {scoring.top_section(sec) for sec in alignment}
    out: list[dict] = []
    for s in ref.get("sections", []):
        sec_no = s.get("section_no") or ""
        if not sec_no.isdigit() or sec_no in present_tops:
            continue
        out.append(
            {
                "requirement_id": None,
                "section_no": sec_no,
                "section_title": s.get("title", ""),
                "requirement_title": f"Include section {sec_no}: {s.get('title','')[:60]}",
                "dimension": "structure",
                "status": "non_compliant",
                "severity": "major",
                "confidence": 0.85,
                "evidence": None,
                "doc_location": s.get("title", ""),
                "rationale": "No content for this section was found anywhere in the document "
                "(no aligned heading and no requirement-level evidence).",
                "citation": {"guideline_section": sec_no, "quote": s.get("title", "")},
                "suggested_fix": f"Add a section covering {s.get('title','')}.",
            }
        )
    return out


# ── 6. verify high-impact non-compliant findings (cut false positives) ───────
_VERIFY_DOC_LIMIT = 24000
_VERIFY_MAX_ITEMS = 40

_VERIFY_SYSTEM = (
    "You are an independent reviewer double-checking ALLEGED non-compliance findings from a "
    "document audit. For each item, search the FULL document text for the substance of the "
    "requirement (headings may differ; it may appear anywhere).\n"
    "Overturn a finding ONLY when you can quote VERBATIM document text that addresses the "
    "requirement — paraphrases or inferences are not sufficient. If the text genuinely does "
    "not address it, confirm non_compliant.\n"
    'Return JSON {"verdicts":[{"idx":N,"status":"compliant|partial|non_compliant",'
    '"evidence":"<verbatim doc quote, required when status is not non_compliant>"}]}.'
)


async def verify_findings_node(state: dict[str, Any]) -> dict[str, Any]:
    findings = list(state.get("findings") or [])
    full_text = (state.get("full_text") or "")[:_VERIFY_DOC_LIMIT]
    doc_norm = _norm_quote(full_text)
    # Re-check every critical gap, plus major gaps asserted without evidence —
    # the two classes where a false positive does the most damage to the score.
    suspects = [
        (i, f)
        for i, f in enumerate(findings)
        if f.get("status") == "non_compliant"
        and f.get("requirement_id")
        and (f.get("severity") == "critical" or (f.get("severity") == "major" and not f.get("evidence")))
    ][:_VERIFY_MAX_ITEMS]
    if not suspects:
        return {"current_agent": "verify_findings"}

    items = [
        {"idx": i, "requirement": f["requirement_title"], "section": f.get("section_no")}
        for i, f in suspects
    ]
    user = f"FULL DOCUMENT:\n{full_text}\n\nITEMS:\n" + "\n".join(json.dumps(it) for it in items)
    data = await _complete_json(_VERIFY_SYSTEM, user, max_tokens=3000)
    suspect_idx = {i for i, _ in suspects}
    for v in data.get("verdicts", []):
        if not isinstance(v, dict):
            continue
        idx = v.get("idx")
        if not isinstance(idx, int) or idx not in suspect_idx:
            continue
        new_status = _norm_enum(v.get("status"), _STATUSES, findings[idx]["status"])
        if new_status == findings[idx]["status"]:
            findings[idx]["confidence"] = max(float(findings[idx].get("confidence") or 0.0), 0.85)
            continue
        ev = str(v.get("evidence") or "")[:500]
        # An overturn must come with grounded proof; otherwise keep the gap.
        if new_status in ("compliant", "partial") and not (ev and _grounded(ev, doc_norm)):
            log.warning("verify: overturn without grounded evidence rejected (idx=%s)", idx)
            continue
        findings[idx]["status"] = new_status
        if ev:
            findings[idx]["evidence"] = ev
        findings[idx]["confidence"] = max(float(findings[idx].get("confidence") or 0.0), 0.8)
        findings[idx]["rationale"] = (
            (findings[idx].get("rationale") or "")
            + " [Corrected on independent re-check: the document does address this.]"
        ).strip()[:700]
    return {"findings": findings, "current_agent": "verify_findings"}


# ── 7. aggregate scores + executive summary ──────────────────────────────────
async def aggregate_scores_node(state: dict[str, Any]) -> dict[str, Any]:
    ref = state.get("guideline_ref") or {}
    findings = list(state.get("findings") or [])
    # Derive structure-coverage from the FINAL (post-verify) findings.
    findings += _structure_coverage(ref, findings, state.get("alignment"))
    sec_titles = {scoring.top_section(s.get("section_no")): s.get("title", "")
                  for s in ref.get("sections", []) if (s.get("section_no") or "").isdigit()}
    scores = scoring.aggregate(findings, section_titles=sec_titles)

    # Executive summary over the most serious issues.
    issues = [f for f in findings if f.get("status") in ("non_compliant", "partial")]
    issues.sort(key=lambda f: scoring.SEVERITY_WEIGHT.get(f.get("severity", "minor"), 0), reverse=True)
    top_issues = "\n".join(
        f"- [{f['severity']}/{f['dimension']}] {f['requirement_title']} ({f.get('rationale') or ''})"
        for f in issues[:15]
    )
    pct = round(scores["overall_score"] * 100)
    strengths = [
        f"{d}: {round((v or 0) * 100)}%"
        for d, v in (scores.get("per_dimension") or {}).items()
        if v is not None and v >= 0.75
    ]
    summary = ""
    data = await _complete_json(
        "You are the lead auditor writing the executive summary of a formal compliance audit "
        "report against {code}. Write 4-6 sentences in a measured, professional register: "
        "(1) the overall posture with the score; (2) what the document does well, if anything; "
        "(3) the most material gaps, naming their severity; (4) the 2-3 highest-priority "
        "remediations in order. Be specific and factual — no hedging, no filler, no "
        "recommendations you cannot tie to a listed issue. "
        'Return JSON {{"summary":"..."}}.'.format(code=ref.get("code", "the guideline")),
        f"Overall score: {pct}% ({scores['status_label']}). "
        f"Severity counts: {scores['severity_counts']}. "
        f"Strong dimensions: {', '.join(strengths) or '(none)'}.\n\nTop issues:\n{top_issues or '(none)'}",
        max_tokens=600,
    )
    summary = str(data.get("summary") or "").strip()
    if not summary:
        summary = (
            f"The document scored {pct}% against {ref.get('code','the guideline')} "
            f"({scores['status_label']}). {scores['severity_counts'].get('critical',0)} critical and "
            f"{scores['severity_counts'].get('major',0)} major issues were identified."
        )

    compliance = {
        "overall_score": scores["overall_score"],
        "status_label": scores["status_label"],
        "per_dimension": scores["per_dimension"],
        "per_section": scores["per_section"],
        "severity_counts": scores["severity_counts"],
        "summary": summary,
        "guideline": {
            "id": ref.get("id"),
            "code": ref.get("code"),
            "title": ref.get("title"),
            "version": ref.get("version"),
        },
    }
    return {
        "scores": scores,
        "summary": summary,
        "compliance": compliance,
        "findings": findings,  # now includes structure-coverage rows
        "current_agent": "aggregate_scores",
    }


# ── 8. build report (charts → DOCX/PDF/JSON/CSV) ─────────────────────────────
async def report_build_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build + store the downloadable report. Defensive: never fails the run."""
    try:
        from app.services.compliance.report import build_and_store_report

        uris = await run_sync(
            build_and_store_report,
            state.get("project_id"),
            state.get("run_id"),
            state.get("compliance") or {},
            state.get("findings") or [],
            state.get("doc_meta") or {},
        )
    except Exception as e:  # noqa: BLE001
        log.warning("report build skipped/failed: %s", e)
        uris = {}
    # NB: we deliberately do NOT set rendered_docx_uri/rendered_pdf_uri here.
    # Those drive the generic "finished document" export specs; a compliance
    # audit has no rewritten document — only the report artifacts, which are
    # surfaced via their own compliance_report_* export ids.
    return {
        "report_uris": uris,
        "status": "done",
        "current_agent": "report_build",
    }

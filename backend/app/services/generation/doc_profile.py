"""
Document profiling + intelligent version/field auto-updates.
============================================================

When the user generates a NEW VERSION of a document, a set of fields are
"redundant" for them to edit by hand — the version number should increment, the
effective/issue date should move to the release date, and the revision-history
table should gain a row. This module:

  1. **profiles** the document — type (SOP / policy / report …), a one-line
     summary, and the tone of the writing — so downstream rewriting can match
     the document's voice (LLM, with a keyword fallback); and

  2. **plans the auto-updates** deterministically — detect the current version
     and date in the masthead, compute the bumped version + today's date in the
     document's own date format, and build a revision-history row keyed to the
     table's real columns.

The plan is returned as plain find/replace pairs + a revision row so the
renderer can apply them to the live document without disturbing anything else.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Field detection
# ---------------------------------------------------------------------------

_DATE_TOKEN = (
    r"\d{1,2}[-/.\s][A-Za-z]{3,9}[-/.\s]\d{2,4}"   # 01-Jun-2025 / 1 June 2025
    r"|[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}"            # June 1, 2025
    r"|\d{4}-\d{2}-\d{2}"                            # 2025-06-01
    r"|\d{1,2}/\d{1,2}/\d{2,4}"                      # 01/06/2025
)
_DATE_RE = re.compile(r"\b(" + _DATE_TOKEN + r")\b")
_VERSION_RE = re.compile(r"\b(version|revision|ver|rev|v)\s*[:.\-]?\s*(\d+(?:\.\d+){0,2})\b", re.I)
_EFFDATE_RE = re.compile(
    r"((?:effective|issue|issued|release|revision)\s*date|date\s*of\s*issue|dated)\s*[:.\-]?\s*("
    + _DATE_TOKEN + r")",
    re.I,
)
# Tolerate spaces the extractor may inject around hyphens ("SOP-QA- 0042").
_DOCNUM_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:-\s?[A-Z0-9]+){1,4})\b")
_PREPARED_BY_RE = re.compile(r"(?:prepared|authored|written|created)\s*by\s*[:.\-]?\s*([^\n|]{2,40})", re.I)
_LABEL_WORDS = re.compile(r"reviewed|approved|prepared|department|\bby\b", re.I)
_LEADING_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[-*+•◦▪‣·●○]|\(?\s*(?:\d{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]+)\s*[.)\]])\s+"
)

_DATE_FORMATS = (
    "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y",
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y",
)


def _detect_version(text: str) -> tuple[Optional[str], Optional[str]]:
    m = _VERSION_RE.search(text or "")
    return (m.group(2), m.group(0)) if m else (None, None)


def _detect_effective_date(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (date_value, full_matched_substring) preferring a labelled date."""
    m = _EFFDATE_RE.search(text or "")
    if m:
        return m.group(2), m.group(0)
    m = _DATE_RE.search(text or "")
    if m:
        return m.group(1), m.group(1)
    return None, None


def _detect_docnum(text: str) -> Optional[str]:
    m = _DOCNUM_RE.search(text or "")
    return re.sub(r"\s+", "", m.group(1)) if m else None


def _clean_name(s: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", (s or "")).strip()


def _detect_author(text: str, tables: list) -> Optional[str]:
    # Prefer a real "Prepared By"/"Author" column in a masthead table.
    for grid in tables or []:
        if len(grid) < 2 or not grid[0]:
            continue
        for ci, h in enumerate((c or "" for c in grid[0])):
            hl = h.lower()
            if "prepared" in hl or "author" in hl or hl == "by":
                if ci < len(grid[1]):
                    val = _clean_name(grid[1][ci])
                    if val and not _LABEL_WORDS.fullmatch(val or ""):
                        return val
    m = _PREPARED_BY_RE.search(text or "")
    if m:
        cand = _clean_name(m.group(1))
        # Reject captures that are really adjacent field labels.
        if cand and not _LABEL_WORDS.search(cand):
            return cand
    return None


def _bump_version(version: str, kind: str) -> str:
    parts = version.split(".")
    nums = [int(p) for p in parts if p.isdigit()]
    if not nums:
        return version
    if kind == "major":
        nums[0] += 1
        for i in range(1, len(nums)):
            nums[i] = 0
        if len(nums) == 1:
            nums.append(0)
    else:  # minor
        if len(nums) == 1:
            nums.append(1)
        else:
            nums[-1] += 1
    return ".".join(str(n) for n in nums)


def _version_gt(a: str, b: str) -> bool:
    """True if dotted version ``a`` is numerically greater than ``b``.

    Compares component-wise so 1.10 > 1.9 and 10 > 9.0. Used to decide whether
    an app-level ``target_version`` (the upload counter) should override the
    document's own masthead version — it only should when it actually advances.
    """
    pa = [int(x) for x in re.findall(r"\d+", a or "")]
    pb = [int(x) for x in re.findall(r"\d+", b or "")]
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb


def _reformat_today(date_str: str, today: datetime) -> str:
    """Render ``today`` in the same format as the detected ``date_str``."""
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(date_str.strip(), fmt)
            return today.strftime(fmt)
        except ValueError:
            continue
    return today.strftime("%d-%b-%Y")


# ---------------------------------------------------------------------------
# Revision-history table
# ---------------------------------------------------------------------------

def _find_revision_header(tables: list[list[list[str]]]) -> Optional[list[str]]:
    for grid in tables or []:
        if not grid or not grid[0]:
            continue
        header = " ".join((c or "").lower() for c in grid[0])
        if "version" in header and "date" in header and any(
            k in header for k in ("change", "summary", "description", "author", "revision")
        ):
            return [str(c) for c in grid[0]]
    return None


def _revision_row(columns: list[str], *, new_version: str, today_str: str,
                  change_summary: str, author: Optional[str]) -> list[str]:
    row: list[str] = []
    for c in columns:
        cl = (c or "").lower()
        if "version" in cl or cl in ("ver", "rev", "revision", "#", "no."):
            row.append(new_version)
        elif "date" in cl:
            row.append(today_str)
        elif "author" in cl or "by" in cl or "owner" in cl or "approv" in cl:
            row.append(author or "—")
        elif any(k in cl for k in ("change", "summary", "description", "note", "detail")):
            row.append(change_summary)
        else:
            row.append("")
    return row


def _clean_change_summary(change_summary: str) -> str:
    """Convert reviewer instructions into prose suitable for a revision table."""
    lines = []
    for line in (change_summary or "").splitlines():
        s = line.strip()
        if not s:
            continue
        lines.append(_LEADING_LIST_MARKER_RE.sub("", s).strip())
    return "; ".join(l for l in lines if l).strip()


# ---------------------------------------------------------------------------
# LLM context (type / summary / tone)
# ---------------------------------------------------------------------------

_HEUR_TYPES = [
    (r"standard\s+operating\s+procedure|\bSOP\b", "Standard Operating Procedure"),
    (r"\bpolicy\b", "Policy"),
    (r"\bprotocol\b", "Protocol"),
    (r"work\s+instruction", "Work Instruction"),
    (r"\breport\b", "Report"),
    (r"\bagreement|contract\b", "Agreement"),
    (r"\bletter\b", "Letter"),
    (r"\bmemo\b", "Memo"),
    (r"\bmanual|guide\b", "Manual"),
]


def _heuristic_context(masthead: str, full_text: str) -> dict:
    blob = (masthead + "\n" + full_text[:2000]).lower()
    doc_type = "Document"
    for pat, name in _HEUR_TYPES:
        if re.search(pat, blob, re.I):
            doc_type = name
            break
    title = (masthead.strip().split("\n", 1)[0] if masthead.strip() else "")[:120]
    return {
        "doc_type": doc_type,
        "summary": title or f"A {doc_type.lower()}.",
        "tone": "formal, professional",
    }


def _llm_context(masthead: str, full_text: str, tables: list) -> Optional[dict]:
    try:
        from app.llm.adapters import chat_json, llm_available
    except Exception:
        return None
    if not llm_available():
        return None
    system = (
        "You analyse a document to guide an editor. Return ONLY JSON: "
        '{"doc_type":"<e.g. Standard Operating Procedure / Policy / Report>",'
        '"summary":"<one sentence on what this document is about>",'
        '"tone":"<2-4 words, e.g. formal, regulatory, technical>"}.'
    )
    digest = "MASTHEAD:\n" + masthead[:800] + "\n\nCONTENT (excerpt):\n" + full_text[:2500]
    raw = chat_json(system, digest, temperature=0.0, max_tokens=200)
    if isinstance(raw, dict) and raw.get("doc_type"):
        return {
            "doc_type": str(raw.get("doc_type"))[:80],
            "summary": str(raw.get("summary") or "")[:300],
            "tone": str(raw.get("tone") or "formal")[:60],
        }
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_profile_and_updates(
    draft,
    *,
    version_bump: str = "minor",     # "minor" | "major" | "none"
    target_version: Optional[str] = None,  # explicit new version; overrides bump
    change_summary: str = "",
    update_dates: bool = True,
    today: Optional[datetime] = None,
    use_llm: Optional[bool] = None,
) -> dict:
    """Profile the draft and plan auto field-updates for the new version.

    When ``target_version`` is given (e.g. the user uploaded versions 9 and 2,
    so the new document is "10") it is used verbatim as the new version number;
    otherwise the masthead version is bumped per ``version_bump``.

    Returns ``{profile, field_updates, context_str, notes}``."""
    today = today or datetime.now(timezone.utc)
    sections = getattr(draft, "sections", []) or []

    # The masthead is the opening block — everything before the first NUMBERED
    # section ("1. …"). The extractor often promotes the title/subtitle to
    # headings, so we can't rely on a single heading=None section; we walk the
    # opening sections (capped) and stop at the first numbered heading.
    head_parts: list[str] = []
    masthead_tables: list = []
    all_tables: list = []
    full_parts: list[str] = []
    masthead_done = False
    for i, s in enumerate(sections):
        is_numbered = bool(s.heading and re.match(r"^\s*\d+[.\s]", s.heading))
        if not masthead_done and not is_numbered and i < 4:
            if s.heading:
                head_parts.append(s.heading)
            if s.text:
                head_parts.append(s.text)
            masthead_tables.extend(s.tables or [])
        else:
            masthead_done = True
        all_tables.extend(s.tables or [])
        if s.heading:
            full_parts.append(s.heading)
        if s.text:
            full_parts.append(s.text)
    masthead = "\n".join(head_parts)
    full_text = "\n".join(full_parts)
    # The masthead may itself include a key/value table (Department / Prepared By).
    masthead_blob = masthead + "\n" + "\n".join(
        " ".join(c for c in row) for grid in masthead_tables for row in grid
    )

    ctx = (_llm_context(masthead, full_text, all_tables) if (use_llm is not False) else None) \
        or _heuristic_context(masthead, full_text)

    version, version_ctx = _detect_version(masthead_blob)
    eff_date, eff_ctx = _detect_effective_date(masthead_blob)
    docnum = _detect_docnum(masthead_blob)
    author = _detect_author(masthead, masthead_tables)

    replacements: list[dict] = []
    notes: list[str] = []

    # Decide the new version. Default: bump the document's OWN masthead version
    # (the version readers see, e.g. 2.1 → 2.2). The app-level ``target_version``
    # (the upload counter, "max uploaded + 1") only overrides when it genuinely
    # ADVANCES the masthead version — otherwise we'd downgrade a "2.1" document
    # to "2" just because it happens to be the second upload.
    target = (target_version or "").strip()
    bumped = (
        _bump_version(version, version_bump)
        if version and version_bump in ("minor", "major")
        else None
    )
    if target and (not version or _version_gt(target, version)):
        new_version = target
    else:
        new_version = bumped or (target or None)

    # Rewrite the version string in the masthead only when we detected one and
    # are actually changing it. ``kind``/``old``/``new`` let the renderer apply a
    # robust, whitespace-tolerant replacement; ``find``/``replace`` drive the UI.
    if new_version and version and version_ctx and new_version != version:
        replacements.append({
            "label": "Version",
            "find": version_ctx,
            "replace": version_ctx.replace(version, new_version),
            "kind": "version",
            "old": version,
            "new": new_version,
        })

    new_date = None
    if eff_date and update_dates and (new_version is not None or version_bump != "none"):
        new_date = _reformat_today(eff_date, today)
        if new_date != eff_date and eff_ctx:
            replacements.append({
                "label": "Effective date",
                "find": eff_ctx,
                "replace": eff_ctx.replace(eff_date, new_date),
                "kind": "date",
                "old": eff_date,
                "new": new_date,
            })

    rev_header = _find_revision_header(all_tables)
    revision = None
    if rev_header and (new_version or version):
        summary = _clean_change_summary(change_summary) or "Reviewed regeneration; content updated."
        revision = {
            "header": rev_header,
            "row": _revision_row(
                rev_header,
                new_version=new_version or version or "",
                today_str=new_date or _reformat_today(eff_date or "01-Jan-2000", today),
                change_summary=summary[:200],
                author=author,
            ),
        }

    profile = {
        "doc_type": ctx["doc_type"],
        "summary": ctx["summary"],
        "tone": ctx["tone"],
        "document_number": docnum,
        "version": version,
        "new_version": new_version,
        "effective_date": eff_date,
        "new_date": new_date,
        "has_revision_table": revision is not None,
        "author": author,
    }
    context_str = (
        f"This is a {ctx['doc_type']}"
        + (f" ({docnum})" if docnum else "")
        + f". Tone: {ctx['tone']}. "
        + (f"About: {ctx['summary']} " if ctx.get("summary") else "")
        + "Match this document's type, terminology and tone in any edits."
    )
    if new_version:
        notes.append(f"Version {version} → {new_version}")
    if new_date:
        notes.append(f"Date {eff_date} → {new_date}")
    if revision:
        notes.append("Added a revision-history row")

    return {
        "profile": profile,
        "field_updates": {"replacements": replacements, "revision": revision},
        "context_str": context_str,
        "notes": notes,
    }

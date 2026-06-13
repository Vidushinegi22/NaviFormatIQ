"""
Semantic mapping between draft sections and template slots.

For each ``HeadingSlot`` in the template fingerprint we decide which draft
``DraftSection`` (if any) should populate it, and what action the pipeline
should take: ``fill``, ``rewrite``, ``rag``, or ``flag``.

Strategy:
  1. Ask Azure OpenAI to return a JSON array of mappings (one per slot).
  2. Validate the response against the ``SectionMapping`` schema; retry once
     on parse failure.
  3. For any slot the LLM omitted (or if the LLM is unavailable), fall back
     to a TF-IDF cosine-similarity matcher using slot titles + keywords vs
     draft headings + first 200 chars.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from app.llm.adapters import chat_json, llm_available
from app.schemas.document_model import (
    DraftStructure,
    Mapping,
    MappingAction,
    SectionMapping,
    TemplateFingerprint,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def map_sections(
    fingerprint: TemplateFingerprint, draft: DraftStructure
) -> SectionMapping:
    """Return a SectionMapping covering every slot in ``fingerprint``."""
    slots = fingerprint.heading_hierarchy
    sections = draft.sections
    if not slots:
        return SectionMapping(mappings=[])

    valid_idxs = {s.index for s in sections}
    mappings_by_slot: dict[str, Mapping] = {}
    used_section_indices: set[int] = set()

    # 0) Deterministic pre-pass — exact normalized-heading and section-number
    #    matches are ground truth. They skip the LLM entirely, which keeps the
    #    common case (same headings, different formatting) fast and stable.
    unresolved = []
    for slot in slots:
        m = _exact_match(slot, sections, exclude=used_section_indices)
        if m is not None:
            mappings_by_slot[slot.slot_id] = m
            used_section_indices.add(m.draft_section_idx)
        else:
            unresolved.append(slot)

    # 1) LLM mapping for the rest — validated against the real slot ids and
    #    section indices so a hallucinated answer can never corrupt the run.
    if unresolved and llm_available():
        llm_result = _try_llm_mapping(unresolved, sections)
        if llm_result:
            unresolved_ids = {s.slot_id for s in unresolved}
            for m in llm_result.mappings:
                if m.slot_id not in unresolved_ids or m.slot_id in mappings_by_slot:
                    continue  # unknown or duplicate slot — drop
                if m.draft_section_idx is not None and m.draft_section_idx not in valid_idxs:
                    m = Mapping(
                        slot_id=m.slot_id,
                        draft_section_idx=None,
                        confidence=0.0,
                        action=MappingAction.RAG,
                        rationale="LLM referenced a non-existent draft section; needs retrieval.",
                    )
                mappings_by_slot[m.slot_id] = m
                if m.draft_section_idx is not None:
                    used_section_indices.add(m.draft_section_idx)

    # 2) Fill any remaining gaps deterministically with a 1:1 greedy assignment
    #    so we never bind two slots to the same draft section.
    for slot in slots:
        if slot.slot_id in mappings_by_slot:
            continue
        m = _heuristic_match(slot, sections, exclude=used_section_indices)
        if m.draft_section_idx is not None:
            used_section_indices.add(m.draft_section_idx)
        mappings_by_slot[slot.slot_id] = m

    # Preserve template slot order
    ordered = [mappings_by_slot[s.slot_id] for s in slots if s.slot_id in mappings_by_slot]
    return SectionMapping(mappings=ordered)


def _exact_match(slot, sections, exclude: set[int]) -> Optional[Mapping]:
    """Deterministic ground-truth matches: identical normalized heading, or an
    identical leading section number ("3.1"). Returns None when ambiguous."""
    slot_key = _heading_key(slot.title)
    for section in sections:
        if section.index in exclude or not section.heading:
            continue
        if _heading_key(section.heading) == slot_key:
            return Mapping(
                slot_id=slot.slot_id,
                draft_section_idx=section.index,
                confidence=1.0,
                action=MappingAction.REWRITE,
                rationale="Exact normalized heading match.",
            )
    # Match on the heading text WITHOUT the section number ("1. Purpose" ↔ "Purpose").
    slot_bare = _strip_section_number(slot.title)
    if slot_bare:
        for section in sections:
            if section.index in exclude or not section.heading:
                continue
            if _strip_section_number(section.heading) == slot_bare:
                return Mapping(
                    slot_id=slot.slot_id,
                    draft_section_idx=section.index,
                    confidence=0.97,
                    action=MappingAction.REWRITE,
                    rationale="Heading match ignoring section numbering.",
                )
    return None


def _strip_section_number(text: str | None) -> str:
    return _heading_key(_LEADING_SECTION_NO_RE.sub("", text or ""))


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

_MAPPER_SYSTEM = (
    "You map sections of a draft document onto slots in a target template. "
    "Return JSON only. The JSON must be an object with a 'mappings' key whose "
    "value is an array of objects with keys: slot_id (string), "
    "draft_section_idx (int or null), confidence (0..1), action "
    "(one of 'fill','rewrite','rag','flag'), rationale (short string). "
    "Choose 'fill' when the draft section already matches the template "
    "voice; 'rewrite' when the content matches but tone/format must change; "
    "'rag' when no draft section fits and the slot must be retrieved from a "
    "reference corpus; 'flag' when even RAG is unlikely to help."
)


def _try_llm_mapping(slots, sections) -> Optional[SectionMapping]:
    slot_summary = [
        {
            "slot_id": s.slot_id,
            "title": s.title,
            "level": s.level,
            "keywords": s.expected_keywords,
        }
        for s in slots
    ]
    section_summary = [
        {
            "idx": s.index,
            "heading": s.heading,
            "snippet": (s.text or "")[:200],
        }
        for s in sections
    ]

    import json

    user_msg = (
        "Template slots:\n"
        + json.dumps(slot_summary, ensure_ascii=False)
        + "\n\nDraft sections:\n"
        + json.dumps(section_summary, ensure_ascii=False)
        + "\n\nReturn one mapping per template slot."
    )

    for _ in range(2):
        raw = chat_json(_MAPPER_SYSTEM, user_msg, temperature=0.1)
        if not raw:
            return None
        try:
            mappings = []
            for m in raw.get("mappings", []):
                action = m.get("action", "flag")
                try:
                    action_enum = MappingAction(action)
                except ValueError:
                    action_enum = MappingAction.FLAG
                idx = m.get("draft_section_idx")
                if idx is not None:
                    try:
                        idx = int(idx)
                    except (TypeError, ValueError):
                        idx = None
                try:
                    confidence = max(0.0, min(1.0, float(m.get("confidence", 0.0))))
                except (TypeError, ValueError):
                    confidence = 0.0
                mappings.append(
                    Mapping(
                        slot_id=str(m["slot_id"]),
                        draft_section_idx=idx,
                        confidence=confidence,
                        action=action_enum,
                        rationale=m.get("rationale"),
                    )
                )
            return SectionMapping(mappings=mappings)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Deterministic fallback (TF-IDF cosine over bag-of-words)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z]{3,}")
_LEADING_SECTION_NO_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[\.\)]?\s+")

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "are",
    "was", "were", "but", "not", "you", "your", "our", "any", "all",
}


def _stem(tok: str) -> str:
    """Tiny stemmer that collapses common English plural/verbal suffixes.

    Crude on purpose — its job is to make 'risks' match 'risk' and
    'summaries' match 'summary'. Anything more sophisticated belongs in
    the LLM-driven mapping path.
    """
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 4 and tok.endswith("sses"):
        return tok[:-2]
    if len(tok) > 4 and tok.endswith("ses"):
        return tok[:-1]
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    if len(tok) > 4 and tok.endswith("ing"):
        return tok[:-3]
    if len(tok) > 4 and tok.endswith("ed"):
        return tok[:-2]
    return tok


def _tokens(text: str) -> list[str]:
    raw = (t.lower() for t in _TOKEN_RE.findall(text or ""))
    return [_stem(t) for t in raw if t not in _STOPWORDS]


def _heading_key(text: str | None) -> str:
    """Normalize a heading for exact matching before semantic fallback."""
    text = re.sub(r"\s+", " ", text or "").strip().lower()
    text = re.sub(r"[\u2010-\u2015-]+", "-", text)
    return text


def _section_number(text: str | None) -> str | None:
    m = _LEADING_SECTION_NO_RE.match(text or "")
    return m.group(1) if m else None


def _tfidf_vectors(corpus: list[list[str]]) -> tuple[list[dict[str, float]], dict[str, float]]:
    df: Counter = Counter()
    for doc in corpus:
        df.update(set(doc))
    n = max(len(corpus), 1)
    idf = {term: math.log((n + 1) / (cnt + 1)) + 1 for term, cnt in df.items()}
    vectors = []
    for doc in corpus:
        tf = Counter(doc)
        vec = {term: tf[term] * idf.get(term, 1.0) for term in tf}
        vectors.append(vec)
    return vectors, idf


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in set(a) | set(b))
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _heuristic_match(slot, sections, exclude: Optional[set[int]] = None) -> Mapping:
    exclude = exclude or set()
    slot_key = _heading_key(slot.title)
    slot_number = _section_number(slot.title)
    for section in sections:
        if section.index in exclude:
            continue
        if _heading_key(section.heading) == slot_key:
            return Mapping(
                slot_id=slot.slot_id,
                draft_section_idx=section.index,
                confidence=1.0,
                action=MappingAction.REWRITE,
                rationale="Exact normalized heading match.",
            )
    if slot_number:
        for section in sections:
            if section.index in exclude:
                continue
            if _section_number(section.heading) == slot_number:
                return Mapping(
                    slot_id=slot.slot_id,
                    draft_section_idx=section.index,
                    confidence=0.95,
                    action=MappingAction.REWRITE,
                    rationale="Matching numbered section prefix.",
                )

    slot_tokens = _tokens(slot.title) + [_stem(k.lower()) for k in slot.expected_keywords]
    if not sections or not slot_tokens:
        return Mapping(
            slot_id=slot.slot_id,
            draft_section_idx=None,
            confidence=0.0,
            action=MappingAction.RAG if slot.required else MappingAction.FLAG,
            rationale="No draft sections to match against.",
        )

    section_token_lists = [
        _tokens((s.heading or "") + " " + (s.text or "")[:400]) for s in sections
    ]
    vectors, _ = _tfidf_vectors(section_token_lists + [slot_tokens])
    slot_vec = vectors[-1]
    sims = [_cosine(slot_vec, v) for v in vectors[:-1]]
    if not sims:
        return Mapping(
            slot_id=slot.slot_id,
            confidence=0.0,
            action=MappingAction.RAG,
            rationale="No vectors computed.",
        )

    # Pick the best non-excluded section.
    eligible = [
        (i, sims[i])
        for i in range(len(sims))
        if sections[i].index not in exclude
    ]
    if slot_number and any(_section_number(s.heading) for s in sections):
        numbered_eligible = [
            (i, sim) for i, sim in eligible if _section_number(sections[i].heading)
        ]
        if numbered_eligible:
            eligible = numbered_eligible
    if not eligible:
        return Mapping(
            slot_id=slot.slot_id,
            confidence=0.0,
            action=MappingAction.RAG if slot.required else MappingAction.FLAG,
            rationale="All draft sections already mapped.",
        )

    best_idx, best_sim = max(eligible, key=lambda x: x[1])

    if best_sim >= 0.15:
        action = MappingAction.REWRITE
        chosen_idx = sections[best_idx].index
    else:
        action = MappingAction.RAG if slot.required else MappingAction.FLAG
        chosen_idx = None

    return Mapping(
        slot_id=slot.slot_id,
        draft_section_idx=chosen_idx,
        confidence=round(best_sim, 3),
        action=action,
        rationale=f"TF-IDF cosine={best_sim:.3f} against section '{sections[best_idx].heading}'.",
    )

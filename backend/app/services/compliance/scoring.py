"""Severity- and confidence-weighted compliance scoring (pure functions over finding dicts).

A finding contributes credit (compliant 1.0 / partial 0.5 / non_compliant 0;
not_applicable is excluded) weighted by severity and by the auditor's
confidence: a tentative verdict (confidence 0.0) carries 60% of the weight of
a certain one (confidence 1.0), so uncertain calls move the score less in
either direction without ever being silently dropped. Scores are 0..1. Any
failing *critical* requirement forces the overall label to "failing"
regardless of score.
"""
from __future__ import annotations

from typing import Any, Optional

SEVERITY_WEIGHT = {"critical": 4.0, "major": 3.0, "minor": 2.0, "info": 1.0}
STATUS_CREDIT = {"compliant": 1.0, "partial": 0.5, "non_compliant": 0.0}
_ISSUE_STATUSES = {"partial", "non_compliant"}
# Confidence scales the weight between these bounds (0.6x .. 1.0x).
_CONF_FLOOR = 0.6
_DEFAULT_CONFIDENCE = 0.7


def top_section(section_no: Optional[str]) -> str:
    if not section_no:
        return "—"
    return section_no.split(".")[0]


def _confidence(f: dict[str, Any]) -> float:
    try:
        c = float(f.get("confidence"))
    except (TypeError, ValueError):
        return _DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, c))


def _weighted(findings: list[dict[str, Any]]) -> Optional[float]:
    num = den = 0.0
    for f in findings:
        if f.get("status") == "not_applicable":
            continue
        w = SEVERITY_WEIGHT.get(f.get("severity", "minor"), 2.0)
        w *= _CONF_FLOOR + (1.0 - _CONF_FLOOR) * _confidence(f)
        num += w * STATUS_CREDIT.get(f.get("status", "non_compliant"), 0.0)
        den += w
    return round(num / den, 4) if den else None


def status_label(score: Optional[float], *, has_critical_fail: bool = False) -> str:
    if has_critical_fail:
        return "failing"
    if score is None:
        return "n/a"
    if score >= 0.90:
        return "strong"
    if score >= 0.75:
        return "moderate"
    if score >= 0.50:
        return "weak"
    return "failing"


def aggregate(
    findings: list[dict[str, Any]],
    *,
    section_titles: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Roll findings up into overall / per-dimension / per-section scores + counts."""
    section_titles = section_titles or {}
    dims = ("content", "structure", "formatting", "style", "tone")

    per_dimension: dict[str, Optional[float]] = {}
    for d in dims:
        sub = [f for f in findings if f.get("dimension") == d]
        if sub:
            per_dimension[d] = _weighted(sub)

    by_top: dict[str, list[dict]] = {}
    for f in findings:
        by_top.setdefault(top_section(f.get("section_no")), []).append(f)

    def _sort_key(sec: str) -> tuple:
        return (0, int(sec)) if sec.isdigit() else (1, 0)

    per_section: list[dict[str, Any]] = []
    for sec in sorted(by_top, key=_sort_key):
        group = by_top[sec]
        sc = _weighted(group)
        crit_fail = any(
            f.get("severity") == "critical" and f.get("status") == "non_compliant"
            for f in group
        )
        per_section.append(
            {
                "section": sec,
                "title": section_titles.get(sec, ""),
                "score": sc,
                "status": status_label(sc, has_critical_fail=crit_fail),
                "findings_count": sum(1 for f in group if f.get("status") in _ISSUE_STATUSES),
                "total": len(group),
            }
        )

    severity_counts = {s: 0 for s in SEVERITY_WEIGHT}
    for f in findings:
        if f.get("status") in _ISSUE_STATUSES:
            severity_counts[f.get("severity", "minor")] = (
                severity_counts.get(f.get("severity", "minor"), 0) + 1
            )

    overall = _weighted(findings)
    has_crit = any(
        f.get("severity") == "critical" and f.get("status") == "non_compliant"
        for f in findings
    )
    return {
        "overall_score": overall if overall is not None else 0.0,
        "status_label": status_label(overall, has_critical_fail=has_crit),
        "per_dimension": per_dimension,
        "per_section": per_section,
        "severity_counts": severity_counts,
    }

"""Compliance report charts → PNG bytes (matplotlib, headless Agg backend).

Colours mirror the frontend design tokens so the downloadable report and the
in-app dashboard read as one product.
"""
from __future__ import annotations

import io
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless; no display
import matplotlib.pyplot as plt  # noqa: E402

BRAND = "#051D60"
EMERALD = "#059669"
AMBER = "#B45309"
ROSE = "#BE123C"
INK = "#64748B"
SEVERITY_COLORS = {"critical": "#BE123C", "major": "#EA580C", "minor": "#CA8A04", "info": "#2563EB"}
_DIM_ORDER = ["content", "structure", "formatting", "style", "tone"]


def _band(score: float) -> str:
    if score >= 0.75:
        return EMERALD
    if score >= 0.50:
        return AMBER
    return ROSE


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def overall_donut(score: float) -> bytes:
    """A ring gauge showing the overall compliance percentage."""
    score = max(0.0, min(1.0, float(score or 0.0)))
    color = _band(score)
    fig, ax = plt.subplots(figsize=(3.2, 3.2), subplot_kw={"aspect": "equal"})
    ax.pie(
        [score, 1 - score],
        colors=[color, "#E5E7EB"],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.32, "edgecolor": "white"},
    )
    ax.text(0, 0.06, f"{round(score * 100)}%", ha="center", va="center", fontsize=30, fontweight="bold", color=BRAND)
    ax.text(0, -0.26, "compliant", ha="center", va="center", fontsize=10, color=INK)
    return _fig_to_png(fig)


def dimension_bars(per_dimension: dict[str, float]) -> bytes:
    dims = [d for d in _DIM_ORDER if d in per_dimension] or list(per_dimension.keys())
    vals = [float(per_dimension.get(d) or 0.0) for d in dims]
    fig, ax = plt.subplots(figsize=(5.0, 2.8))
    y = range(len(dims))
    ax.barh(list(y), [v * 100 for v in vals], color=[_band(v) for v in vals], height=0.6)
    ax.set_yticks(list(y))
    ax.set_yticklabels([d.capitalize() for d in dims], fontsize=10)
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.set_xlabel("Compliance %", fontsize=9, color=INK)
    for i, v in enumerate(vals):
        ax.text(v * 100 + 1.5, i, f"{round(v * 100)}%", va="center", fontsize=9, color=BRAND)
    ax.spines[["top", "right"]].set_visible(False)
    return _fig_to_png(fig)


def section_bars(per_section: list[dict[str, Any]], limit: int = 16) -> bytes:
    rows = [s for s in per_section if s.get("score") is not None][:limit]
    labels = [f"{s.get('section','')}. {(s.get('title') or '')[:26]}" for s in rows]
    vals = [float(s.get("score") or 0.0) for s in rows]
    h = max(2.6, 0.34 * len(rows) + 0.8)
    fig, ax = plt.subplots(figsize=(5.6, h))
    y = range(len(rows))
    ax.barh(list(y), [v * 100 for v in vals], color=[_band(v) for v in vals], height=0.62)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.set_xlabel("Compliance % by section", fontsize=9, color=INK)
    ax.spines[["top", "right"]].set_visible(False)
    return _fig_to_png(fig)


def severity_bar(severity_counts: dict[str, int]) -> bytes:
    order = ["critical", "major", "minor", "info"]
    counts = [int(severity_counts.get(s, 0)) for s in order]
    labels = [s.capitalize() for s in order]
    fig, ax = plt.subplots(figsize=(4.6, 2.6))
    bars = ax.bar(labels, counts, color=[SEVERITY_COLORS[s] for s in order], width=0.62)
    ax.set_ylabel("Open issues", fontsize=9, color=INK)
    ax.bar_label(bars, fontsize=10, color=BRAND, padding=2)
    ax.tick_params(axis="x", labelsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    return _fig_to_png(fig)

"""
PDF Document Extractor
======================
Extracts content structure and styling from .pdf files into JSON.

Uses a combination of:
  - PyMuPDF (fitz) for text blocks with font info, images, and page metadata
  - pdfplumber for table detection and extraction

Produces the same (DocumentContent, DocumentStyling) schema as word_ext.py,
enabling cross-format style transfer.

NOTE: PDF is a layout format, not a semantic one. This extractor uses heuristics
(font size thresholds, spacing analysis, bullet pattern matching) to infer
document structure. Results are best-effort.
"""

from __future__ import annotations

import base64
import io
import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import fitz  # PyMuPDF
import pdfplumber

from app.schemas.document_model import (
    Alignment,
    CellStyle,
    ContentElement,
    DocumentContent,
    DocumentMetadata,
    DocumentStyling,
    ElementType,
    ImagePosition,
    IndentStyle,
    ListType,
    Orientation,
    PageMargins,
    PageStyle,
    ParagraphStyle,
    RunStyle,
    StyleMetadata,
    TableCell,
    TableRow,
    TableStyle,
    TextRun,
    VerticalAlignment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pt_to_inches(pt: float) -> float:
    return round(pt / 72, 4)


def _hex_color(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _fitz_color_to_hex(color: int) -> str:
    """Convert fitz integer color to hex string."""
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return _hex_color(r, g, b)


_BULLET_PATTERNS = re.compile(
    r"^[\s]*[•◦▪▸‣●○►▻◆◇■□★☆⬥\-–—]\s"
)
_NUMBERED_PATTERNS = re.compile(
    r"^[\s]*(?:\d+[.)]\s|[a-zA-Z][.)]\s|[ivxIVX]+[.)]\s)"
)


# ---------------------------------------------------------------------------
# Multi-signal heading detection
# ---------------------------------------------------------------------------
# PDF is a layout format with no semantic heading markup, so we layer several
# independent signals and combine them into a confidence score. A block is a
# heading when its score crosses the threshold (or the legacy font-size rule
# fires) and it is short enough to plausibly be a heading. Signal weights and
# the threshold are tunable constants.

W_TOC = 0.80          # block text matches a PDF outline (bookmark) entry
W_FONT_SIZE = 0.40    # dominant font is meaningfully larger than body text
W_BOLD_SHORT = 0.25   # mostly-bold and short (same-size bold L3 headings)
W_NUMBERING = 0.30    # "1.", "1.1", "3.2.1", "A.1" prefix on a short line
W_UPPERCASE = 0.15    # short ALL-CAPS line

HEADING_CONFIDENCE_THRESHOLD = 0.55
MAX_HEADING_CHARS = 180
MAX_HEADING_WORDS = 24

# A line that is *only* a numbering prefix ("1.2.3") with no title — an orphan
# numbering artefact, never a heading.
_BARE_NUMBER_RE = re.compile(r"^\s*\d+(\.\d+)*\.?\s*$")
# Leading "3.2.1" / "A.1" numeric/letter prefix followed by real title text.
_NUMBER_PREFIX_RE = re.compile(r"^\s*((?:\d+|[A-Z])(?:\.[\dA-Za-z]+)*)\.?\s+\S")
# Undo soft hyphenation introduced by hard line breaks: "compli- ance" → "compliance".
_HYPHEN_LINEBREAK_RE = re.compile(r"(\w+)-\s+(\w+)")
# A dotted leader ("......" or ". . . .") — the tell-tale of a table-of-contents
# entry. These mirror real headings, so we keep them as plain text instead of
# duplicating the whole TOC into the heading outline.
_DOTTED_LEADER_RE = re.compile(r"(?:\.\s?){4,}|…{2,}")


def _dehyphenate(text: str) -> str:
    """Repair words split by a hyphen across a line break."""
    return _HYPHEN_LINEBREAK_RE.sub(r"\1\2", text)


# ---------------------------------------------------------------------------
# Running header/footer + list-marker helpers
# ---------------------------------------------------------------------------
# PDFs have no header/footer semantics, so the page's running head ("Structure
# and Content of Clinical Study Reports" on every page) and the page numbers
# are extracted as ordinary body text — they pollute the content AND, worse,
# get glued onto the first real line of each page, hiding the section headings
# underneath. We strip them with a cross-page repetition pass.

# Fraction of page height treated as the top / bottom margin "running" zone.
_TOP_ZONE_FRAC = 0.12
_BOTTOM_ZONE_FRAC = 0.12

# A standalone page-number line: "12", "- 12 -", "Page 12", "12 / 80", "iv".
_PAGE_NUM_RE = re.compile(
    r"^(?:page\s+)?\d+(?:\s*(?:of|/|-|–)\s*\d+)?$"
    r"|^[-–—\s]*\d+[-–—\s]*$"
    r"|^[ivxlcdm]{1,7}$",
    re.IGNORECASE,
)
# Opening marker of a list item: a bullet glyph, or a SINGLE-level "1." / "a)" /
# "iv." prefix. Multi-level numbers like "2.1." deliberately do NOT match (those
# are section headings, handled separately).
_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[•◦▪▸‣●○►▻◆◇■□★☆⬥\-–—]\s|(?:\d+|[A-Za-z]|[ivxIVX]+)[.)]\s)"
)


def _norm_running(text: str) -> str:
    """Normalize a line for cross-page repetition counting (digits → '#')."""
    t = re.sub(r"\d+", "#", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def _looks_like_page_number(text: str) -> bool:
    t = text.strip()
    return bool(t) and len(t) <= 20 and bool(_PAGE_NUM_RE.match(t))


def _starts_list_marker(text: str) -> bool:
    return bool(_LIST_MARKER_RE.match(text))


def _strip_leading_marker(
    runs: list["TextRun"], list_type: "ListType"
) -> tuple[list["TextRun"], Optional[str], Optional[str]]:
    """Remove the leading list marker ("• ", "1. ", "a) ") from a list item's
    runs and report it via ``bullet_char`` / ``number_format`` instead — matching
    how native DOCX lists store the marker separately from the text (and avoiding
    a doubled bullet when the document is re-rendered).
    """
    bullet_char: Optional[str] = None
    number_format: Optional[str] = None
    i = 0
    while i < len(runs) and not (runs[i].text or "").strip():
        i += 1
    if i >= len(runs):
        return runs, None, None
    first = runs[i].text or ""
    if list_type == ListType.BULLET:
        m = re.match(r"\s*([•◦▪▸‣●○►▻◆◇■□★☆⬥\-–—])\s*", first)
        if m:
            bullet_char = m.group(1)
            runs[i].text = first[m.end():]
    else:
        m = re.match(r"\s*(?:\d+|[A-Za-z]|[ivxIVX]+)[.)]\s*", first)
        if m:
            number_format = "decimal"
            runs[i].text = first[m.end():]
    # If the marker lived in its own span, that run is now empty — drop it and
    # left-trim the run that actually carries the text.
    if i < len(runs) and not (runs[i].text or "").strip():
        runs = runs[:i] + runs[i + 1:]
        if i < len(runs) and runs[i].text:
            runs[i].text = runs[i].text.lstrip()
    return runs, bullet_char, number_format


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _numbering_depth(text: str) -> int:
    """Return the dotted-segment depth of a leading number ("3.2.1" → 3)."""
    m = _NUMBER_PREFIX_RE.match(text)
    if not m:
        return 0
    return m.group(1).count(".") + 1


def _is_uppercase_like(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / len(letters) >= 0.85 and len(text.split()) <= 12


def _level_from_ratio(ratio: float) -> int:
    if ratio >= 1.8:
        return 1
    if ratio >= 1.4:
        return 2
    if ratio >= 1.2:
        return 3
    return 4


def _looks_like_header_row(row: list[str]) -> bool:
    """A header row is fully populated and mostly Title-case / short cells."""
    cells = [(c or "").strip() for c in row]
    if not cells or any(not c for c in cells):
        return False
    if any(len(c) > 60 for c in cells):
        return False
    title_like = sum(1 for c in cells if c[0].isupper() or c.isupper())
    return title_like >= max(1, len(cells) // 2)


def _score_heading(
    text: str,
    max_size: float,
    is_bold: bool,
    page_number: int,
    body_size: float,
    heading_thresholds: dict,
    toc_index: dict,
) -> tuple[float, int]:
    """Combine heading signals into ``(confidence, level_hint)``.

    ``level_hint`` is 0 when no signal had an opinion on the level.
    """
    score = 0.0
    level_hint = 0
    t = text.strip()
    if not t:
        return 0.0, 0

    # 1. PDF outline (TOC) alignment — the strongest signal we have.
    norm = _norm_title(t)
    toc_level = toc_index.get((page_number, norm))
    if toc_level is None and toc_index:
        for (p, title), lvl in toc_index.items():
            if p == page_number and (title.startswith(norm) or norm.startswith(title)):
                toc_level = lvl
                break
    if toc_level is not None:
        score += W_TOC
        level_hint = toc_level

    # 2. Font size relative to the body text.
    if max_size in heading_thresholds:
        score += W_FONT_SIZE
        if not level_hint:
            level_hint = heading_thresholds[max_size]
    elif body_size and max_size >= body_size * 1.15:
        bump = W_FONT_SIZE + (0.10 if max_size >= body_size * 1.6 else 0.0)
        score += bump
        if not level_hint:
            level_hint = _level_from_ratio(max_size / body_size)

    # 3. Bold + short.
    if is_bold and len(t) <= 120:
        score += W_BOLD_SHORT
        if not level_hint:
            level_hint = 3

    # 4. Numbering depth.
    depth = _numbering_depth(t)
    if depth:
        score += W_NUMBERING
        if not level_hint or depth < level_hint:
            level_hint = depth

    # 5. Uppercase-y short line.
    if _is_uppercase_like(t):
        score += W_UPPERCASE
        if not level_hint:
            level_hint = 1

    return score, level_hint


# ---------------------------------------------------------------------------
# Text block with font info
# ---------------------------------------------------------------------------

class _TextSpan:
    """A span of text with consistent font properties within a page."""

    def __init__(
        self,
        text: str,
        font_name: str,
        font_size: float,
        is_bold: bool,
        is_italic: bool,
        color_hex: str,
        bbox: tuple[float, float, float, float],  # x0, y0, x1, y1
        page_num: int,
        origin_y: float = 0.0,  # text baseline y (span "origin")
        is_superscript: bool = False,
    ):
        self.text = text
        self.font_name = font_name
        self.font_size = font_size
        self.is_bold = is_bold
        self.is_italic = is_italic
        self.color_hex = color_hex
        self.bbox = bbox
        self.page_num = page_num
        self.origin_y = origin_y
        self.is_superscript = is_superscript

    @property
    def y_center(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def baseline(self) -> float:
        """Text baseline (span origin y), falling back to the bbox bottom.

        Grouping/sorting on the baseline — not the vertical centre — keeps
        small-caps initials (a tall first letter at a larger font) in line with
        the smaller letters that follow them, instead of sorting every large
        initial ahead of every small letter and scrambling the heading.
        """
        return self.origin_y or self.bbox[3]

    @property
    def x0(self) -> float:
        return self.bbox[0]


class _TextLine:
    """A group of spans on the same visual line."""
    def __init__(self):
        self.spans: list[_TextSpan] = []

    @property
    def y_center(self) -> float:
        if not self.spans:
            return 0
        return sum(s.y_center for s in self.spans) / len(self.spans)

    @property
    def baseline(self) -> float:
        if not self.spans:
            return 0
        return sum(s.baseline for s in self.spans) / len(self.spans)

    @property
    def x0(self) -> float:
        return min(s.x0 for s in self.spans) if self.spans else 0

    @property
    def text(self) -> str:
        # Spans are already ordered left-to-right. Join them, but re-insert a
        # space wherever two spans are visually separated yet neither carries a
        # space character at the seam — PDFs often encode the gap at a font
        # boundary (e.g. italic emphasis) via glyph positioning instead of a
        # real space, which otherwise glues words together ("is"+"inherent" →
        # "isinherent"). A clear horizontal gap relative to the font size is the
        # tell; contiguous or overlapping spans (mid-word splits) get no space.
        parts: list[str] = []
        prev: Optional[_TextSpan] = None
        for s in self.spans:
            if not s.text:
                continue
            if (
                prev is not None
                and parts
                and not parts[-1][-1:].isspace()
                and not s.text[:1].isspace()
                and (s.x0 - prev.bbox[2]) > 0.25 * min(prev.font_size, s.font_size)
            ):
                parts.append(" ")
            parts.append(s.text)
            prev = s
        return "".join(parts)

    @property
    def max_font_size(self) -> float:
        return max(s.font_size for s in self.spans) if self.spans else 0

    @property
    def dominant_bold(self) -> bool:
        bold_chars = sum(len(s.text) for s in self.spans if s.is_bold)
        total_chars = sum(len(s.text) for s in self.spans) or 1
        return bold_chars / total_chars > 0.5


class _TextBlock:
    """A paragraph-level grouping of lines."""
    def __init__(self):
        self.lines: list[_TextLine] = []
        self.page_num: int = 0

    @property
    def text(self) -> str:
        joined = " ".join(line.text.strip() for line in self.lines if line.text.strip())
        return _dehyphenate(joined)

    @property
    def max_font_size(self) -> float:
        return max(line.max_font_size for line in self.lines) if self.lines else 0

    @property
    def dominant_bold(self) -> bool:
        bold_lines = sum(1 for line in self.lines if line.dominant_bold)
        return bold_lines / max(len(self.lines), 1) > 0.5

    @property
    def x0(self) -> float:
        return min(line.x0 for line in self.lines) if self.lines else 0

    @property
    def top(self) -> float:
        """Top y-coordinate of the block (for reading-order sorting)."""
        tops = [s.bbox[1] for line in self.lines for s in line.spans]
        return min(tops) if tops else 0.0

    @property
    def y_center(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.y_center for line in self.lines) / len(self.lines)


# ---------------------------------------------------------------------------
# PDF Extractor class
# ---------------------------------------------------------------------------

class PDFExtractor:
    """Extract content and styling from a PDF document."""

    def __init__(self):
        self._run_styles: dict[str, RunStyle] = {}
        self._para_styles: dict[str, ParagraphStyle] = {}
        self._table_styles: dict[str, TableStyle] = {}
        self._cell_styles: dict[str, CellStyle] = {}
        self._run_style_counter = 0
        self._para_style_counter = 0
        self._run_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        file_path: Optional[str] = None,
        file_stream: Optional[io.BytesIO] = None,
        filename: Optional[str] = None,
    ) -> tuple[DocumentContent, DocumentStyling]:
        """
        Extract content and styling from a PDF.

        Provide either ``file_path`` or ``file_stream``.
        """
        if file_path:
            pdf_bytes = open(file_path, "rb").read()
            source_name = os.path.basename(file_path)
        elif file_stream:
            pdf_bytes = file_stream.read()
            file_stream.seek(0)
            source_name = filename or "uploaded.pdf"
        else:
            raise ValueError("Provide either file_path or file_stream")

        # Open with both libraries
        fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        plumber_pdf = pdfplumber.open(io.BytesIO(pdf_bytes))

        # ---- Page style ----
        page_style = self._extract_page_style(fitz_doc)

        # ---- Analyze font sizes for heading detection ----
        font_stats = self._analyze_fonts(fitz_doc)

        # ---- PDF outline (bookmarks) → strongest heading signal ----
        toc_index = self._build_toc_index(fitz_doc)

        # ---- Running headers / footers + page numbers (cross-page repetition) ----
        running_lines = self._detect_running_lines(fitz_doc)

        # ---- Extract text blocks per page ----
        all_elements: list[ContentElement] = []

        for page_num in range(len(fitz_doc)):
            fitz_page = fitz_doc[page_num]
            plumber_page = plumber_pdf.pages[page_num] if page_num < len(plumber_pdf.pages) else None

            # 1) Extract tables (so we can exclude their regions from text)
            table_items, table_bboxes = self._extract_tables(plumber_page, page_num)

            # 2) Extract text blocks (excluding table regions + running head/foot)
            text_blocks = self._extract_text_blocks(
                fitz_page, page_num, table_bboxes, running_lines
            )

            # 3) Extract images (with on-page position)
            image_items = self._extract_images(fitz_page, page_num)

            # 4) Convert text blocks to positioned ContentElements
            text_items = self._blocks_to_elements(text_blocks, font_stats, toc_index)

            # 5) Interleave everything in true reading order (top→bottom, left→right)
            page_elements = self._merge_in_reading_order(
                text_items, table_items, image_items
            )
            all_elements.extend(page_elements)

            # Add page break between pages (except the last)
            if page_num < len(fitz_doc) - 1:
                all_elements.append(ContentElement(type=ElementType.PAGE_BREAK))

        # ---- Metadata ----
        fitz_meta = fitz_doc.metadata or {}
        metadata = DocumentMetadata(
            source_file=source_name,
            source_type="pdf",
            extracted_at=datetime.now(timezone.utc).isoformat(),
            page_count=len(fitz_doc),
            author=fitz_meta.get("author"),
            title=fitz_meta.get("title"),
        )

        # Cross-page artefact: a table caption already captured as a heading
        # repeats as a plain paragraph when the table continues on the next
        # page — drop the duplicate so the caption appears once.
        all_elements = self._dedupe_repeated_captions(all_elements)

        content = DocumentContent(metadata=metadata, elements=all_elements)

        styling = DocumentStyling(
            metadata=StyleMetadata(
                source_file=source_name,
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
            page_style=page_style,
            paragraph_styles=self._para_styles,
            run_styles=self._run_styles,
            table_styles=self._table_styles,
            cell_styles=self._cell_styles,
        )

        fitz_doc.close()
        plumber_pdf.close()

        return content, styling

    @staticmethod
    def _dedupe_repeated_captions(elements: list[ContentElement]) -> list[ContentElement]:
        """Drop a paragraph that repeats the text of a recent heading.

        Tables/figures spanning pages re-print their caption on each page;
        the first occurrence is detected as a heading, the repeats arrive as
        plain paragraphs. Only short, recently-seen heading texts are matched
        so genuine body text is never removed."""
        out: list[ContentElement] = []
        recent_headings: list[str] = []

        def norm(el: ContentElement) -> str:
            return re.sub(r"\s+", " ", "".join(r.text or "" for r in (el.content or []))).strip().lower()

        for el in elements:
            if el.type == ElementType.HEADING:
                key = norm(el)
                if key:
                    recent_headings.append(key)
                    recent_headings[:] = recent_headings[-8:]
            elif el.type == ElementType.PARAGRAPH:
                key = norm(el)
                if key and len(key) <= 120 and key in recent_headings:
                    continue  # repeated caption — skip
            out.append(el)
        return out

    # ------------------------------------------------------------------
    # Page style
    # ------------------------------------------------------------------

    def _extract_page_style(self, doc: fitz.Document) -> PageStyle:
        if len(doc) == 0:
            return PageStyle()

        page = doc[0]
        rect = page.rect
        width = _pt_to_inches(rect.width)
        height = _pt_to_inches(rect.height)
        orientation = Orientation.LANDSCAPE if width > height else Orientation.PORTRAIT

        # Infer margins from first page text bounds
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        blocks = text_dict.get("blocks", [])

        if blocks:
            min_x = min(b["bbox"][0] for b in blocks if b.get("type") == 0) if any(b.get("type") == 0 for b in blocks) else rect.width * 0.1
            max_x = max(b["bbox"][2] for b in blocks if b.get("type") == 0) if any(b.get("type") == 0 for b in blocks) else rect.width * 0.9
            min_y = min(b["bbox"][1] for b in blocks if b.get("type") == 0) if any(b.get("type") == 0 for b in blocks) else rect.height * 0.1
            max_y = max(b["bbox"][3] for b in blocks if b.get("type") == 0) if any(b.get("type") == 0 for b in blocks) else rect.height * 0.9
        else:
            min_x, max_x = rect.width * 0.1, rect.width * 0.9
            min_y, max_y = rect.height * 0.1, rect.height * 0.9

        return PageStyle(
            width_inches=width,
            height_inches=height,
            orientation=orientation,
            margins=PageMargins(
                top_inches=_pt_to_inches(min_y),
                bottom_inches=_pt_to_inches(rect.height - max_y),
                left_inches=_pt_to_inches(min_x),
                right_inches=_pt_to_inches(rect.width - max_x),
            ),
        )

    # ------------------------------------------------------------------
    # Font analysis (for heading detection)
    # ------------------------------------------------------------------

    def _analyze_fonts(self, doc: fitz.Document) -> dict:
        """Analyze font size distribution across the document to detect body vs heading text."""
        size_counter: Counter = Counter()
        left_counter: Counter = Counter()
        all_sizes: list[float] = []

        for page in doc:
            text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    lb = line.get("bbox")
                    if lb and any(s.get("text", "").strip() for s in line.get("spans", [])):
                        left_counter[round(lb[0])] += 1
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text:
                            size = round(span["size"], 1)
                            size_counter[size] += len(text)
                            all_sizes.append(size)

        if not size_counter:
            return {"body_size": 12.0, "heading_thresholds": {}, "body_left": 0.0}

        # Body size = most common font size
        body_size = size_counter.most_common(1)[0][0]

        # Body left edge = most common line-start x (baseline for list indentation)
        body_left = float(left_counter.most_common(1)[0][0]) if left_counter else 0.0

        # Heading thresholds: sizes larger than body size
        larger_sizes = sorted([s for s in size_counter.keys() if s > body_size], reverse=True)

        heading_thresholds = {}
        for i, size in enumerate(larger_sizes[:6]):  # max 6 heading levels
            heading_thresholds[size] = i + 1  # Heading 1, 2, 3...

        return {
            "body_size": body_size,
            "heading_thresholds": heading_thresholds,
            "body_left": body_left,
        }

    def _build_toc_index(self, doc: fitz.Document) -> dict[tuple[int, str], int]:
        """Index the PDF outline as ``{(page_1indexed, normalized_title): level}``.

        ``get_toc`` returns ``[level, title, page]`` rows with 1-indexed pages;
        we keep that convention and reconcile against 0-indexed block pages at
        match time. Returns an empty dict when the PDF carries no bookmarks.
        """
        idx: dict[tuple[int, str], int] = {}
        try:
            entries = doc.get_toc(simple=True) or []
        except Exception:
            return idx
        for entry in entries:
            try:
                level, title, page = int(entry[0]), str(entry[1] or ""), int(entry[2])
            except (IndexError, TypeError, ValueError):
                continue
            norm = _norm_title(title)
            if norm and page:
                idx[(page, norm)] = level
        return idx

    def _detect_running_lines(self, doc: fitz.Document) -> set[str]:
        """Find lines that repeat in the top/bottom margin zone across pages.

        These are the document's running header and footer (e.g. the title
        repeated on every page). A normalized line (digits collapsed to '#')
        that appears in a margin zone on at least half the pages — or at least
        three — is treated as a running line and suppressed during extraction.
        Returns an empty set for documents too short to judge (< 3 pages); pure
        page-number lines are still stripped separately by ``_looks_like_page_number``.
        """
        n_pages = len(doc)
        if n_pages < 3:
            return set()
        counter: Counter = Counter()
        for page in doc:
            height = page.rect.height or 1.0
            top_zone = height * _TOP_ZONE_FRAC
            bot_zone = height * (1 - _BOTTOM_ZONE_FRAC)
            try:
                text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            except Exception:
                continue
            seen: set[str] = set()
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    lb = line.get("bbox", (0, 0, 0, 0))
                    y_center = (lb[1] + lb[3]) / 2
                    if top_zone < y_center < bot_zone:
                        continue  # not in a margin zone
                    txt = "".join(s.get("text", "") for s in line.get("spans", []))
                    norm = _norm_running(txt)
                    if norm and len(norm) <= 120 and norm not in seen:
                        counter[norm] += 1
                        seen.add(norm)
        threshold = max(3, int(n_pages * 0.5))
        return {norm for norm, count in counter.items() if count >= threshold}

    def _is_running_line(
        self,
        line: "_TextLine",
        top_zone: float,
        bot_zone: float,
        running_lines: set[str],
    ) -> bool:
        """True when a line is a running header/footer or a page number sitting
        in a margin zone — i.e. it should be dropped from the body content."""
        if not line.spans:
            return False
        if top_zone < line.y_center < bot_zone:
            return False  # inside the body, never a running line
        txt = line.text.strip()
        if not txt:
            return False
        if _looks_like_page_number(txt):
            return True
        return _norm_running(txt) in running_lines

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_text_blocks(
        self,
        page: fitz.Page,
        page_num: int,
        exclude_bboxes: list[tuple[float, float, float, float]],
        running_lines: Optional[set[str]] = None,
    ) -> list[_TextBlock]:
        """Extract text from a page, excluding table regions + running
        headers/footers, and group the remaining lines into blocks."""
        running_lines = running_lines or set()
        page_height = page.rect.height or 1.0
        top_zone = page_height * _TOP_ZONE_FRAC
        bot_zone = page_height * (1 - _BOTTOM_ZONE_FRAC)
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        spans: list[_TextSpan] = []

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            block_bbox = tuple(block["bbox"])

            # Skip if block overlaps with any table region
            if self._overlaps_any(block_bbox, exclude_bboxes):
                continue

            for line in block.get("lines", []):
                for span_data in line.get("spans", []):
                    text = span_data.get("text", "")
                    if not text:
                        continue

                    font_name = span_data.get("font", "")
                    font_size = round(span_data.get("size", 12.0), 2)
                    flags = span_data.get("flags", 0)
                    # Flag bits are unreliable for many PDFs (and base-14 bold
                    # fonts), so fall back to the font-name encoding too.
                    fl = font_name.lower()
                    is_bold = bool(flags & 2**4) or "bold" in fl or "black" in fl  # bit 4 = bold
                    is_italic = bool(flags & 2**1) or "italic" in fl or "oblique" in fl  # bit 1 = italic

                    # Color
                    color_int = span_data.get("color", 0)
                    color_hex = _fitz_color_to_hex(color_int)

                    bbox = tuple(span_data.get("bbox", (0, 0, 0, 0)))
                    origin_y = float(span_data.get("origin", (0.0, 0.0))[1])

                    spans.append(_TextSpan(
                        text=text,
                        font_name=font_name,
                        font_size=font_size,
                        is_bold=is_bold,
                        is_italic=is_italic,
                        color_hex=color_hex,
                        bbox=bbox,
                        page_num=page_num,
                        origin_y=origin_y,
                        is_superscript=bool(flags & 1),  # bit 0 = superscripted
                    ))

        # Group spans into lines (same y-center within tolerance)
        lines = self._group_spans_into_lines(spans)

        # Drop running headers/footers and page numbers living in the margin
        # zones — otherwise they get glued onto the first real line of the page
        # and hide the section heading underneath.
        lines = [
            ln
            for ln in lines
            if not self._is_running_line(ln, top_zone, bot_zone, running_lines)
        ]

        # Group lines into blocks (paragraphs) based on spacing
        blocks = self._group_lines_into_blocks(lines, page_num)

        return blocks

    def _group_spans_into_lines(self, spans: list[_TextSpan]) -> list[_TextLine]:
        """Group spans sharing a text baseline into lines.

        Sorting/grouping on the baseline (not the vertical centre) is what keeps
        small-caps headings readable: a 14pt initial and the 11pt letters after
        it share one baseline, so they stay interleaved by x-position rather than
        every large initial sorting ahead of every small letter.
        """
        if not spans:
            return []

        # Superscripts ("[14C]", footnote markers) sit on a RAISED baseline a
        # few points above the text they decorate. Grouping them naively makes
        # them their own line/block ("14" paragraphs, "1414" merges), so they
        # are attached to their host line after the normal grouping pass.
        normal = [s for s in spans if not s.is_superscript]
        supers = [s for s in spans if s.is_superscript]
        if not normal:  # an all-superscript region — treat it as normal text
            normal, supers = spans, []

        # Sort by baseline, then x
        normal.sort(key=lambda s: (round(s.baseline, 1), s.x0))

        lines: list[_TextLine] = []
        current_line = _TextLine()
        current_line.spans.append(normal[0])

        for span in normal[1:]:
            if abs(span.baseline - current_line.baseline) < 3.0:  # same line tolerance
                current_line.spans.append(span)
            else:
                lines.append(current_line)
                current_line = _TextLine()
                current_line.spans.append(span)

        if current_line.spans:
            lines.append(current_line)

        # Attach each superscript to the line it decorates: the host baseline
        # sits slightly BELOW the raised one, and the span must overlap the
        # host horizontally (with a small margin for end-of-line markers).
        for sp in supers:
            best: Optional[_TextLine] = None
            best_d = float("inf")
            for ln in lines:
                d = ln.baseline - sp.baseline
                if d < -1.0 or d > max(8.0, ln.max_font_size * 0.9):
                    continue
                lx0 = min(s.bbox[0] for s in ln.spans)
                lx1 = max(s.bbox[2] for s in ln.spans)
                if sp.bbox[2] < lx0 - 4.0 or sp.bbox[0] > lx1 + 4.0:
                    continue
                if abs(d) < best_d:
                    best, best_d = ln, abs(d)
            if best is not None:
                best.spans.append(sp)
            else:  # no host found — keep the text, as its own line
                ln = _TextLine()
                ln.spans.append(sp)
                lines.append(ln)

        for ln in lines:
            ln.spans.sort(key=lambda s: s.x0)
        lines.sort(key=lambda ln: (round(ln.baseline, 1), ln.x0))

        return lines

    def _group_lines_into_blocks(self, lines: list[_TextLine], page_num: int) -> list[_TextBlock]:
        """Group lines into paragraph blocks based on vertical spacing."""
        if not lines:
            return []

        blocks: list[_TextBlock] = []
        current_block = _TextBlock()
        current_block.page_num = page_num
        current_block.lines.append(lines[0])

        for i in range(1, len(lines)):
            prev_line = lines[i - 1]
            curr_line = lines[i]

            # Calculate gap between lines
            prev_bottom = max(s.bbox[3] for s in prev_line.spans)
            curr_top = min(s.bbox[1] for s in curr_line.spans)
            gap = curr_top - prev_bottom

            # Average line height
            avg_line_height = sum(
                s.bbox[3] - s.bbox[1] for s in prev_line.spans
            ) / max(len(prev_line.spans), 1)

            # If gap > 1.5x line height or significant font size change → new block
            font_size_changed = abs(curr_line.max_font_size - prev_line.max_font_size) > 2.0
            large_gap = gap > avg_line_height * 1.2
            # A line that opens with a list marker ("• ", "1. ", "a) ") starts a
            # new list item — otherwise a tightly-spaced list collapses into one
            # block and only the first marker is ever seen.
            starts_item = _starts_list_marker(curr_line.text)
            # A bold→regular (or regular→bold) transition is a strong paragraph
            # boundary: it splits a bold heading from the body text that follows
            # it on the next line, so the heading can be recognized on its own
            # instead of being buried inside a paragraph.
            bold_changed = prev_line.dominant_bold != curr_line.dominant_bold

            if large_gap or font_size_changed or starts_item or bold_changed:
                blocks.append(current_block)
                current_block = _TextBlock()
                current_block.page_num = page_num

            current_block.lines.append(curr_line)

        if current_block.lines:
            blocks.append(current_block)

        return blocks

    def _overlaps_any(
        self,
        bbox: tuple,
        exclude_bboxes: list[tuple[float, float, float, float]],
        threshold: float = 0.5,
    ) -> bool:
        """Check if bbox significantly overlaps with any excluded region."""
        x0, y0, x1, y1 = bbox[:4]
        area = max((x1 - x0) * (y1 - y0), 1)

        for ex in exclude_bboxes:
            ex0, ey0, ex1, ey1 = ex
            # Intersection
            ix0 = max(x0, ex0)
            iy0 = max(y0, ey0)
            ix1 = min(x1, ex1)
            iy1 = min(y1, ey1)

            if ix0 < ix1 and iy0 < iy1:
                intersection = (ix1 - ix0) * (iy1 - iy0)
                if intersection / area > threshold:
                    return True
        return False

    # ------------------------------------------------------------------
    # Table extraction (via pdfplumber)
    # ------------------------------------------------------------------

    def _extract_tables(
        self,
        page: Optional[pdfplumber.page.Page],
        page_num: int,
    ) -> tuple[list[tuple[float, float, ContentElement]], list[tuple[float, float, float, float]]]:
        """Extract tables from a pdfplumber page as ``(top, left, element)`` items."""
        if page is None:
            return [], []

        items: list[tuple[float, float, ContentElement]] = []
        bboxes: list[tuple[float, float, float, float]] = []

        try:
            tables = page.find_tables()
        except Exception:
            return [], []

        for table in tables:
            bbox = table.bbox
            bboxes.append(bbox)

            try:
                extracted = table.extract()
            except Exception:
                continue

            if not extracted:
                continue

            # Create table style
            table_style_key = f"table_{len(self._table_styles)}"
            self._table_styles[table_style_key] = TableStyle()

            # Only tag the first row as a header when it actually looks like
            # one (fully populated, short, Title-case) — many PDF tables have
            # no header row at all.
            has_header = _looks_like_header_row(extracted[0]) if extracted else False

            rows: list[TableRow] = []
            for row_idx, row_data in enumerate(extracted):
                cells: list[TableCell] = []
                for cell_text in row_data:
                    text = cell_text or ""
                    run_style = RunStyle(font_size_pt=10.0)  # default
                    style_ref = self._register_run_style(run_style)
                    cells.append(
                        TableCell(
                            content=[TextRun(text=text, style_ref=style_ref)] if text else [],
                        )
                    )
                rows.append(TableRow(cells=cells, is_header=(row_idx == 0 and has_header)))

            items.append((
                float(bbox[1]),  # top
                float(bbox[0]),  # left
                ContentElement(
                    type=ElementType.TABLE,
                    rows=rows,
                    table_style_ref=table_style_key,
                ),
            ))

        return items, bboxes

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    def _extract_images(
        self, page: fitz.Page, page_num: int
    ) -> list[tuple[float, float, ContentElement]]:
        """Extract images from a PyMuPDF page as ``(top, left, element)`` items."""
        items: list[tuple[float, float, ContentElement]] = []

        try:
            image_list = page.get_images(full=True)
        except Exception:
            return items

        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
                if base_image is None:
                    continue

                image_bytes = base_image["image"]
                img_ext = base_image.get("ext", "png")
                width = base_image.get("width", 0)
                height = base_image.get("height", 0)

                img_b64 = base64.b64encode(image_bytes).decode("utf-8")

                # Convert pixel dimensions to approximate inches (assume 96 DPI)
                width_inches = round(width / 96, 4) if width else None
                height_inches = round(height / 96, 4) if height else None

                # On-page placement → reading-order position. When the rect
                # can't be resolved, sink the image to the bottom of the page.
                top, left = 1e9, 0.0
                try:
                    rects = page.get_image_rects(xref)
                    if rects:
                        top = min(r.y0 for r in rects)
                        left = min(r.x0 for r in rects)
                except Exception:
                    pass

                items.append((
                    top,
                    left,
                    ContentElement(
                        type=ElementType.IMAGE,
                        data_base64=img_b64,
                        image_format=img_ext,
                        width_inches=width_inches,
                        height_inches=height_inches,
                        image_position=ImagePosition.INLINE,
                    ),
                ))
            except Exception:
                continue

        return items

    # ------------------------------------------------------------------
    # Convert text blocks to ContentElements
    # ------------------------------------------------------------------

    def _blocks_to_elements(
        self,
        blocks: list[_TextBlock],
        font_stats: dict,
        toc_index: Optional[dict] = None,
    ) -> list[tuple[float, float, ContentElement]]:
        """Convert text blocks to positioned ``(top, left, element)`` items,
        detecting headings and lists."""
        items: list[tuple[float, float, ContentElement]] = []
        body_size = font_stats["body_size"]
        heading_thresholds = font_stats["heading_thresholds"]
        body_left = font_stats.get("body_left", 0.0)
        toc_index = toc_index or {}

        for block in blocks:
            text = block.text.strip()
            if not text:
                continue

            max_size = block.max_font_size
            is_bold = block.dominant_bold

            # ---- Detect heading (multi-signal) ----
            # Legacy font-size rule, kept as a sufficient condition so we never
            # drop a heading the size-only heuristic used to catch.
            legacy_level = None
            if max_size in heading_thresholds:
                legacy_level = heading_thresholds[max_size]
            elif max_size > body_size * 2.0 and is_bold:
                legacy_level = 1
            elif max_size > body_size * 1.6 and is_bold:
                legacy_level = 2
            elif max_size > body_size * 1.3 and is_bold:
                legacy_level = 3
            elif max_size > body_size * 1.2 and is_bold and len(text) < 100:
                legacy_level = 4

            # Additional signals (PDF outline, numbering, uppercase, bold-short)
            # catch headings the size rule misses — including same-size sections.
            score, level_hint = _score_heading(
                text, max_size, is_bold, block.page_num + 1,
                body_size, heading_thresholds, toc_index,
            )

            # A table-of-contents entry ("1. TITLE PAGE......5") mirrors a real
            # heading but isn't one — keep it as plain text so the outline holds
            # only genuine headings.
            is_toc_entry = bool(_DOTTED_LEADER_RE.search(text))

            heading_level = None
            short_enough = (
                len(text) <= MAX_HEADING_CHARS
                and len(text.split()) <= MAX_HEADING_WORDS
                and not _BARE_NUMBER_RE.match(text)
            )
            if (
                short_enough
                and not is_toc_entry
                and (legacy_level is not None or score >= HEADING_CONFIDENCE_THRESHOLD)
            ):
                heading_level = max(1, min(level_hint or legacy_level or 1, 9))

            # ---- Detect list ----
            list_type = None
            list_level = None

            if is_toc_entry:
                pass  # neither heading nor list — falls through to paragraph
            elif _BULLET_PATTERNS.match(text):
                list_type = ListType.BULLET
            elif _NUMBERED_PATTERNS.match(text):
                list_type = ListType.NUMBERED

            if list_type is not None:
                # Nesting level from how far the item is indented past the body
                # left edge (~18pt ≈ one indent step). Flat lists stay at 0.
                indent = block.x0 - body_left
                list_level = 0 if indent < 12 else min(int(round(indent / 18.0)), 5)

            # ---- Build runs from spans ----
            # Spans flow across line boundaries inside one paragraph block, so
            # a separating space is re-inserted at every line seam and at clear
            # horizontal gaps inside a line (PDFs often encode word gaps via
            # glyph positioning, not space characters) — otherwise the last
            # word of one line glues onto the first word of the next.
            runs: list[TextRun] = []
            prev_span: Optional[_TextSpan] = None
            for line_no, line in enumerate(block.lines):
                at_line_start = line_no > 0
                for span in line.spans:
                    span_text = span.text
                    if not span_text:
                        continue

                    if runs and prev_span is not None:
                        needs_space = (
                            not runs[-1].text[-1:].isspace()
                            and not span_text[:1].isspace()
                        )
                        if needs_space and not at_line_start:
                            gap = span.x0 - prev_span.bbox[2]
                            needs_space = gap > 0.25 * min(
                                prev_span.font_size, span.font_size
                            )
                        if needs_space:
                            span_text = " " + span_text

                    run_style = RunStyle(
                        font_name=span.font_name or None,
                        font_size_pt=span.font_size,
                        bold=span.is_bold or None,
                        italic=span.is_italic or None,
                        color_hex=span.color_hex if span.color_hex != "#000000" else None,
                    )
                    style_ref = self._register_run_style(run_style)
                    runs.append(TextRun(text=span_text, style_ref=style_ref))
                    prev_span = span
                    at_line_start = False

            if not runs:
                continue

            # ---- Build paragraph style ----
            # Estimate alignment from x-position relative to page
            para_style = ParagraphStyle()
            self._para_style_counter += 1
            para_key = f"para_{self._para_style_counter}"
            self._para_styles[para_key] = para_style

            # ---- Create element ----
            if heading_level is not None:
                element = ContentElement(
                    type=ElementType.HEADING,
                    level=heading_level,
                    content=runs,
                    style_ref=para_key,
                )
            elif list_type is not None:
                # Move the marker out of the text into bullet_char/number_format
                # so a re-render doesn't show a doubled bullet.
                runs, bullet_char, number_format = _strip_leading_marker(runs, list_type)
                if not runs:
                    continue
                element = ContentElement(
                    type=ElementType.LIST_ITEM,
                    content=runs,
                    style_ref=para_key,
                    list_type=list_type,
                    list_level=list_level,
                    bullet_char=bullet_char,
                    number_format=number_format,
                )
            else:
                element = ContentElement(
                    type=ElementType.PARAGRAPH,
                    content=runs,
                    style_ref=para_key,
                )

            items.append((block.top, block.x0, element))

        return items

    # ------------------------------------------------------------------
    # Merge elements in reading order
    # ------------------------------------------------------------------

    def _merge_in_reading_order(
        self,
        *positioned_lists: list[tuple[float, float, ContentElement]],
    ) -> list[ContentElement]:
        """Merge positioned ``(top, left, element)`` items into true reading
        order — top→bottom, then left→right.

        This replaces the old "all text, then all tables, then all images"
        concatenation, which shoved every table and image to the end of its
        page regardless of where it actually appeared in the text flow. The
        left tiebreaker also gives a sensible order to items sharing a row.
        """
        items: list[tuple[float, float, ContentElement]] = []
        for lst in positioned_lists:
            items.extend(lst)
        items.sort(key=lambda it: (it[0], it[1]))
        return [element for _, _, element in items]

    # ------------------------------------------------------------------
    # Style registration
    # ------------------------------------------------------------------

    def _register_run_style(self, run_style: RunStyle) -> str:
        """Register and deduplicate run styles."""
        # Build a fingerprint
        parts = []
        for field_name in run_style.model_fields:
            val = getattr(run_style, field_name)
            if val is not None:
                parts.append(f"{field_name}={val}")
        fp = "|".join(parts) if parts else "__default__"

        if fp in self._run_cache:
            return self._run_cache[fp]

        self._run_style_counter += 1
        key = f"run_{self._run_style_counter}"
        self._run_cache[fp] = key
        self._run_styles[key] = run_style
        return key


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

# Below this many extractable characters per page we suspect the embedded text
# layer is unusable; combined with an abundance of vector glyph-paths it signals
# text that was flattened to outlines (curves) and can only be recovered by OCR.
_OUTLINE_TEXT_CHARS_PER_PAGE = 350
_OUTLINE_MIN_DRAWINGS = 200


def _text_layer_is_unreliable(pdf_bytes: bytes) -> bool:
    """True when a PDF's body text cannot be read by text extraction.

    Two failure modes surface here and both require OCR:

    * **scanned / image-only** pages — almost no extractable text at all;
    * **text flattened to vector outlines** (curves) — a handful of stray real
      spans (italic emphasis, code chips) still extract while the body is drawn
      as thousands of vector fill-paths, roughly one per glyph. To text
      extraction this looks like a near-empty page with a little garbled text,
      which is exactly what produced the broken "structure" users saw.

    We sample the first few pages: with plenty of real text we trust the text
    layer (and skip the costlier drawings probe); otherwise a page full of
    vector paths that dwarfs the character count is treated as outline text.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return False
    try:
        pages = len(doc) or 1
        sample = min(pages, 8)
        total_chars = sum(len(doc[i].get_text().strip()) for i in range(sample))
        if total_chars / sample >= _OUTLINE_TEXT_CHARS_PER_PAGE:
            return False  # healthy text layer — trust it, skip the drawings probe
        total_drawings = 0
        for i in range(sample):
            try:
                total_drawings += len(doc[i].get_drawings())
            except Exception:
                pass
        return (
            total_drawings >= _OUTLINE_MIN_DRAWINGS
            and total_drawings > max(total_chars, 1) * 2
        )
    finally:
        doc.close()


def extract_pdf_document(
    file_path: Optional[str] = None,
    file_stream: Optional[io.BytesIO] = None,
    filename: Optional[str] = None,
    use_ocr: Optional[bool] = None,
) -> tuple[DocumentContent, DocumentStyling]:
    """
    Extract content and styling from a PDF document.

    When ``use_ocr`` is None (default), text-extraction is attempted first and
    the call falls back to Azure Document Intelligence (or local Tesseract OCR)
    automatically when the PDF appears to be scanned/image-only *or* when its
    text has been flattened to vector outlines (see ``_text_layer_is_unreliable``).

    Returns:
        (DocumentContent, DocumentStyling)
    """
    # Read the source once so we can extract, probe the text layer, and OCR
    # without re-opening the file or disturbing a stream's read position.
    if file_path:
        with open(file_path, "rb") as _fh:
            pdf_bytes = _fh.read()
        filename = filename or os.path.basename(file_path)
    elif file_stream is not None:
        file_stream.seek(0)
        pdf_bytes = file_stream.read()
        file_stream.seek(0)
    else:
        raise ValueError("Provide either file_path or file_stream")

    if use_ocr is True:
        return _extract_via_ocr(file_stream=io.BytesIO(pdf_bytes), filename=filename)

    extractor = PDFExtractor()
    content, styling = extractor.extract(
        file_stream=io.BytesIO(pdf_bytes), filename=filename
    )

    if use_ocr is False:
        return content, styling

    # Auto: route to OCR when the embedded text is unusable — either too sparse
    # (scanned/image-only) or drawn as vector outlines rather than real text.
    from app.core.config import settings as _settings

    page_count = content.metadata.page_count or 1
    total_text = sum(
        len(" ".join((r.text or "") for r in (el.content or [])))
        for el in content.elements
        if el.type and el.content
    )
    avg_chars = total_text / max(page_count, 1)
    needs_ocr = (
        avg_chars < _settings.ocr_char_threshold
        or _text_layer_is_unreliable(pdf_bytes)
    )
    if not needs_ocr:
        return content, styling

    try:
        return _extract_via_ocr(file_stream=io.BytesIO(pdf_bytes), filename=filename)
    except Exception:
        # OCR not available — return whatever text extraction managed to find.
        return content, styling


def _extract_via_ocr(
    file_path: Optional[str] = None,
    file_stream: Optional[io.BytesIO] = None,
    filename: Optional[str] = None,
) -> tuple[DocumentContent, DocumentStyling]:
    """Run Azure DI (preferred) or Tesseract on a PDF and map into our schema."""
    from app.services.extraction.azure_di import analyze_pdf, layout_to_content

    if file_path:
        with open(file_path, "rb") as fh:
            pdf_bytes = fh.read()
        if not filename:
            filename = os.path.basename(file_path)
    elif file_stream is not None:
        file_stream.seek(0)
        pdf_bytes = file_stream.read()
    else:
        raise ValueError("Either file_path or file_stream must be provided")

    layout = analyze_pdf(pdf_bytes, filename or "draft.pdf")
    return layout_to_content(layout, filename or "draft.pdf")


def fingerprint_pdf_template(
    file_path: Optional[str] = None,
    file_stream: Optional[io.BytesIO] = None,
    filename: Optional[str] = None,
) -> "TemplateFingerprint":
    """Synthesize a Word-template-equivalent TemplateFingerprint from a PDF.

    PDF templates are routed through Azure DI's prebuilt-layout model so the
    section/title structure can be recovered. The resulting fingerprint has
    ``source_format="pdf"`` and no ``template_b64`` — callers should fall
    back to a generated minimal .docx wrapper at render time.
    """
    from app.schemas.document_model import HeadingSlot, TemplateFingerprint
    import re as _re

    content, styling = _extract_via_ocr(
        file_path=file_path, file_stream=file_stream, filename=filename
    )

    slots: list[HeadingSlot] = []
    used: set[str] = set()
    for el in content.elements:
        if el.type and el.type.value == "heading":
            text = " ".join((r.text or "") for r in (el.content or [])).strip()
            if not text:
                continue
            base = _re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_") or f"slot_{len(slots)+1}"
            slot_id = base
            i = 1
            while slot_id in used:
                i += 1
                slot_id = f"{base}_{i}"
            used.add(slot_id)
            slots.append(
                HeadingSlot(
                    slot_id=slot_id,
                    level=el.level or 1,
                    title=text,
                    expected_keywords=[w.lower() for w in _re.findall(r"[A-Za-z]{4,}", text)],
                )
            )

    return TemplateFingerprint(
        metadata=content.metadata,
        page_style=styling.page_style,
        heading_hierarchy=slots,
        style_registry=styling,
        source_format="pdf",
    )


def structure_pdf_draft(
    file_path: Optional[str] = None,
    file_stream: Optional[io.BytesIO] = None,
    filename: Optional[str] = None,
) -> "DraftStructure":
    """Split a PDF draft into DraftSection chunks at every heading."""
    from app.schemas.document_model import DraftSection, DraftStructure, ElementType

    content, _ = extract_pdf_document(
        file_path=file_path, file_stream=file_stream, filename=filename
    )

    sections: list[DraftSection] = []
    current = DraftSection(index=0, heading=None, level=0, text="")

    def _flush():
        if current.heading or current.text.strip() or current.tables:
            sections.append(current)

    for el in content.elements:
        if el.type == ElementType.HEADING:
            _flush()
            heading_text = _dehyphenate(" ".join((r.text or "") for r in (el.content or [])))
            current = DraftSection(
                index=len(sections),
                heading=heading_text.strip(),
                level=el.level or 1,
                text="",
            )
        elif el.type == ElementType.PARAGRAPH:
            text = _dehyphenate(" ".join((r.text or "") for r in (el.content or [])))
            if text:
                current.text = (current.text + "\n" + text).strip() if current.text else text
        elif el.type == ElementType.LIST_ITEM:
            text = _dehyphenate(" ".join((r.text or "") for r in (el.content or [])))
            if text:
                current.text = (current.text + "\n• " + text).strip() if current.text else f"• {text}"
        elif el.type == ElementType.TABLE and el.rows:
            grid = [
                [
                    " ".join((r.text or "") for r in (cell.content or []))
                    for cell in row.cells
                ]
                for row in el.rows
            ]
            current.tables.append(grid)
        elif el.type == ElementType.IMAGE and el.data_base64:
            current.images_b64.append(el.data_base64)

    _flush()
    return DraftStructure(metadata=content.metadata, sections=sections)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pdf_ext.py <path-to-pdf>")
        sys.exit(1)

    path = sys.argv[1]
    content, styling = extract_pdf_document(file_path=path)

    base = os.path.splitext(path)[0]

    content_path = f"{base}_content.json"
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(content.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)
    print(f"Content JSON written to: {content_path}")

    styling_path = f"{base}_styling.json"
    with open(styling_path, "w", encoding="utf-8") as f:
        json.dump(styling.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)
    print(f"Styling JSON written to: {styling_path}")

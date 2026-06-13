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

from models import (
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


def _dehyphenate(text: str) -> str:
    """Repair words split by a hyphen across a line break."""
    return _HYPHEN_LINEBREAK_RE.sub(r"\1\2", text)


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
    ):
        self.text = text
        self.font_name = font_name
        self.font_size = font_size
        self.is_bold = is_bold
        self.is_italic = is_italic
        self.color_hex = color_hex
        self.bbox = bbox
        self.page_num = page_num

    @property
    def y_center(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

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
    def x0(self) -> float:
        return min(s.x0 for s in self.spans) if self.spans else 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

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

        # ---- Extract text blocks per page ----
        all_elements: list[ContentElement] = []

        for page_num in range(len(fitz_doc)):
            fitz_page = fitz_doc[page_num]
            plumber_page = plumber_pdf.pages[page_num] if page_num < len(plumber_pdf.pages) else None

            # 1) Extract tables (so we can exclude their regions from text)
            table_elements, table_bboxes = self._extract_tables(plumber_page, page_num)

            # 2) Extract text blocks (excluding table regions)
            text_blocks = self._extract_text_blocks(fitz_page, page_num, table_bboxes)

            # 3) Extract images
            image_elements = self._extract_images(fitz_page, page_num)

            # 4) Convert text blocks to ContentElements
            text_elements = self._blocks_to_elements(text_blocks, font_stats, toc_index)

            # 5) Merge all elements in reading order (by y-position)
            page_elements = self._merge_in_reading_order(
                text_elements, table_elements, image_elements
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
        all_sizes: list[float] = []

        for page in doc:
            text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text:
                            size = round(span["size"], 1)
                            size_counter[size] += len(text)
                            all_sizes.append(size)

        if not size_counter:
            return {"body_size": 12.0, "heading_thresholds": {}}

        # Body size = most common font size
        body_size = size_counter.most_common(1)[0][0]

        # Heading thresholds: sizes larger than body size
        larger_sizes = sorted([s for s in size_counter.keys() if s > body_size], reverse=True)

        heading_thresholds = {}
        for i, size in enumerate(larger_sizes[:6]):  # max 6 heading levels
            heading_thresholds[size] = i + 1  # Heading 1, 2, 3...

        return {
            "body_size": body_size,
            "heading_thresholds": heading_thresholds,
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

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_text_blocks(
        self,
        page: fitz.Page,
        page_num: int,
        exclude_bboxes: list[tuple[float, float, float, float]],
    ) -> list[_TextBlock]:
        """Extract text from a page, excluding table regions, and group into blocks."""
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

                    spans.append(_TextSpan(
                        text=text,
                        font_name=font_name,
                        font_size=font_size,
                        is_bold=is_bold,
                        is_italic=is_italic,
                        color_hex=color_hex,
                        bbox=bbox,
                        page_num=page_num,
                    ))

        # Group spans into lines (same y-center within tolerance)
        lines = self._group_spans_into_lines(spans)

        # Group lines into blocks (paragraphs) based on spacing
        blocks = self._group_lines_into_blocks(lines, page_num)

        return blocks

    def _group_spans_into_lines(self, spans: list[_TextSpan]) -> list[_TextLine]:
        """Group spans with similar y-center into lines."""
        if not spans:
            return []

        # Sort by y-center, then x
        spans.sort(key=lambda s: (round(s.y_center, 1), s.x0))

        lines: list[_TextLine] = []
        current_line = _TextLine()
        current_line.spans.append(spans[0])

        for span in spans[1:]:
            if abs(span.y_center - current_line.y_center) < 3.0:  # same line tolerance
                current_line.spans.append(span)
            else:
                lines.append(current_line)
                current_line = _TextLine()
                current_line.spans.append(span)

        if current_line.spans:
            lines.append(current_line)

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

            if large_gap or font_size_changed:
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
    ) -> tuple[list[ContentElement], list[tuple[float, float, float, float]]]:
        """Extract tables from a pdfplumber page."""
        if page is None:
            return [], []

        elements: list[ContentElement] = []
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

            elements.append(ContentElement(
                type=ElementType.TABLE,
                rows=rows,
                table_style_ref=table_style_key,
            ))

        return elements, bboxes

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    def _extract_images(self, page: fitz.Page, page_num: int) -> list[ContentElement]:
        """Extract images from a PyMuPDF page."""
        elements: list[ContentElement] = []

        try:
            image_list = page.get_images(full=True)
        except Exception:
            return elements

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

                elements.append(ContentElement(
                    type=ElementType.IMAGE,
                    data_base64=img_b64,
                    image_format=img_ext,
                    width_inches=width_inches,
                    height_inches=height_inches,
                    image_position=ImagePosition.INLINE,
                ))
            except Exception:
                continue

        return elements

    # ------------------------------------------------------------------
    # Convert text blocks to ContentElements
    # ------------------------------------------------------------------

    def _blocks_to_elements(
        self,
        blocks: list[_TextBlock],
        font_stats: dict,
        toc_index: Optional[dict] = None,
    ) -> list[ContentElement]:
        """Convert text blocks to ContentElements, detecting headings and lists."""
        elements: list[ContentElement] = []
        body_size = font_stats["body_size"]
        heading_thresholds = font_stats["heading_thresholds"]
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

            heading_level = None
            short_enough = (
                len(text) <= MAX_HEADING_CHARS
                and len(text.split()) <= MAX_HEADING_WORDS
                and not _BARE_NUMBER_RE.match(text)
            )
            if short_enough and (legacy_level is not None or score >= HEADING_CONFIDENCE_THRESHOLD):
                heading_level = max(1, min(level_hint or legacy_level or 1, 9))

            # ---- Detect list ----
            list_type = None
            list_level = None
            bullet_char = None
            number_format = None

            if _BULLET_PATTERNS.match(text):
                list_type = ListType.BULLET
                list_level = 0
                bullet_char = text.strip()[0]
            elif _NUMBERED_PATTERNS.match(text):
                list_type = ListType.NUMBERED
                list_level = 0
                number_format = "decimal"

            # ---- Build runs from spans ----
            runs: list[TextRun] = []
            for line in block.lines:
                for span in line.spans:
                    span_text = span.text
                    if not span_text:
                        continue

                    run_style = RunStyle(
                        font_name=span.font_name or None,
                        font_size_pt=span.font_size,
                        bold=span.is_bold or None,
                        italic=span.is_italic or None,
                        color_hex=span.color_hex if span.color_hex != "#000000" else None,
                    )
                    style_ref = self._register_run_style(run_style)
                    runs.append(TextRun(text=span_text, style_ref=style_ref))

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
                elements.append(ContentElement(
                    type=ElementType.HEADING,
                    level=heading_level,
                    content=runs,
                    style_ref=para_key,
                ))
            elif list_type is not None:
                elements.append(ContentElement(
                    type=ElementType.LIST_ITEM,
                    content=runs,
                    style_ref=para_key,
                    list_type=list_type,
                    list_level=list_level,
                    bullet_char=bullet_char,
                    number_format=number_format,
                ))
            else:
                elements.append(ContentElement(
                    type=ElementType.PARAGRAPH,
                    content=runs,
                    style_ref=para_key,
                ))

        return elements

    # ------------------------------------------------------------------
    # Merge elements in reading order
    # ------------------------------------------------------------------

    def _merge_in_reading_order(
        self,
        text_elements: list[ContentElement],
        table_elements: list[ContentElement],
        image_elements: list[ContentElement],
    ) -> list[ContentElement]:
        """Merge different element types in approximate reading order.
        
        For now, we use: text first, then tables, then images.
        A more advanced approach would track y-coordinates, but this is
        a good default for most single-column documents.
        """
        return text_elements + table_elements + image_elements

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

def extract_pdf_document(
    file_path: Optional[str] = None,
    file_stream: Optional[io.BytesIO] = None,
    filename: Optional[str] = None,
    use_ocr: Optional[bool] = None,
) -> tuple[DocumentContent, DocumentStyling]:
    """
    Extract content and styling from a PDF document.

    When ``use_ocr`` is None (default), text-extraction is attempted first
    and the call falls back to Azure Document Intelligence (or local
    Tesseract OCR) automatically when the PDF appears to be scanned/image-only.

    Returns:
        (DocumentContent, DocumentStyling)
    """
    if use_ocr is True:
        return _extract_via_ocr(
            file_path=file_path, file_stream=file_stream, filename=filename
        )

    extractor = PDFExtractor()
    content, styling = extractor.extract(
        file_path=file_path, file_stream=file_stream, filename=filename
    )

    if use_ocr is False:
        return content, styling

    # Auto: if extracted text is too sparse, retry through OCR.
    from config import settings as _settings

    page_count = content.metadata.page_count or 1
    total_text = sum(
        len(" ".join((r.text or "") for r in (el.content or [])))
        for el in content.elements
        if el.type and el.content
    )
    avg_chars = total_text / max(page_count, 1)
    if avg_chars >= _settings.ocr_char_threshold:
        return content, styling

    try:
        return _extract_via_ocr(
            file_path=file_path, file_stream=file_stream, filename=filename
        )
    except Exception:
        # OCR not available — return whatever text extraction managed to find.
        return content, styling


def _extract_via_ocr(
    file_path: Optional[str] = None,
    file_stream: Optional[io.BytesIO] = None,
    filename: Optional[str] = None,
) -> tuple[DocumentContent, DocumentStyling]:
    """Run Azure DI (preferred) or Tesseract on a PDF and map into our schema."""
    from azure_di import analyze_pdf, layout_to_content

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
    from models import HeadingSlot, TemplateFingerprint
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
    from models import DraftSection, DraftStructure, ElementType

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

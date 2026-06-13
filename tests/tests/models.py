"""
Pydantic models defining the JSON schema for document content and styling.

These models serve as the shared contract between:
- Word extractor (word_ext.py)
- PDF extractor (pdf_ext.py)
- Styling applier (formater_apply.py)
- FastAPI endpoints (main.py)
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ElementType(str, enum.Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    TABLE = "table"
    LIST_ITEM = "list_item"
    PAGE_BREAK = "page_break"
    SECTION_BREAK = "section_break"
    HYPERLINK = "hyperlink"
    HEADER = "header"
    FOOTER = "footer"


class Alignment(str, enum.Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class UnderlineType(str, enum.Enum):
    NONE = "none"
    SINGLE = "single"
    DOUBLE = "double"
    DOTTED = "dotted"
    DASHED = "dashed"
    WAVY = "wavy"
    THICK = "thick"
    WORDS_ONLY = "words_only"


class ListType(str, enum.Enum):
    BULLET = "bullet"
    NUMBERED = "numbered"


class ImagePosition(str, enum.Enum):
    INLINE = "inline"
    FLOATING = "floating"


class Orientation(str, enum.Enum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class VerticalAlignment(str, enum.Enum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class BorderStyle(str, enum.Enum):
    NONE = "none"
    SINGLE = "single"
    DOUBLE = "double"
    DOTTED = "dotted"
    DASHED = "dashed"
    THICK = "thick"


# ---------------------------------------------------------------------------
# Run (inline text) style
# ---------------------------------------------------------------------------

class RunStyle(BaseModel):
    """Style properties for an inline text run."""
    font_name: Optional[str] = None
    font_size_pt: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[UnderlineType] = None
    strikethrough: Optional[bool] = None
    double_strikethrough: Optional[bool] = None
    superscript: Optional[bool] = None
    subscript: Optional[bool] = None
    color_hex: Optional[str] = None
    highlight_color: Optional[str] = None
    all_caps: Optional[bool] = None
    small_caps: Optional[bool] = None
    character_spacing_pt: Optional[float] = None


# ---------------------------------------------------------------------------
# Content elements (inside a paragraph / heading)
# ---------------------------------------------------------------------------

class TextRun(BaseModel):
    """A run of text with a style reference."""
    text: str
    style_ref: Optional[str] = None
    # Inline style override (used when no named style ref exists)
    inline_style: Optional[RunStyle] = None
    hyperlink_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Paragraph style
# ---------------------------------------------------------------------------

class IndentStyle(BaseModel):
    left_inches: Optional[float] = None
    right_inches: Optional[float] = None
    first_line_inches: Optional[float] = None
    hanging_inches: Optional[float] = None


class ParagraphStyle(BaseModel):
    """Style properties for a paragraph."""
    alignment: Optional[Alignment] = None
    space_before_pt: Optional[float] = None
    space_after_pt: Optional[float] = None
    line_spacing: Optional[float] = None
    line_spacing_rule: Optional[str] = None
    indent: Optional[IndentStyle] = None
    keep_with_next: Optional[bool] = None
    keep_together: Optional[bool] = None
    widow_control: Optional[bool] = None
    outline_level: Optional[int] = None
    tab_stops: Optional[list[float]] = None


# ---------------------------------------------------------------------------
# Table styles
# ---------------------------------------------------------------------------

class BorderDef(BaseModel):
    style: Optional[BorderStyle] = None
    color_hex: Optional[str] = None
    width_pt: Optional[float] = None


class CellBorders(BaseModel):
    top: Optional[BorderDef] = None
    bottom: Optional[BorderDef] = None
    left: Optional[BorderDef] = None
    right: Optional[BorderDef] = None


class CellStyle(BaseModel):
    width_inches: Optional[float] = None
    shading_color_hex: Optional[str] = None
    borders: Optional[CellBorders] = None
    vertical_alignment: Optional[VerticalAlignment] = None
    col_span: Optional[int] = 1
    row_span: Optional[int] = 1


class TableStyle(BaseModel):
    """Style properties for a table."""
    alignment: Optional[Alignment] = None
    border: Optional[BorderDef] = None
    cell_padding_inches: Optional[float] = None
    autofit: Optional[bool] = None
    width_inches: Optional[float] = None


# ---------------------------------------------------------------------------
# Content-level elements (the document body)
# ---------------------------------------------------------------------------

class TableCell(BaseModel):
    content: list[TextRun] = Field(default_factory=list)
    style_ref: Optional[str] = None
    inline_style: Optional[CellStyle] = None


class TableRow(BaseModel):
    cells: list[TableCell] = Field(default_factory=list)
    is_header: bool = False


class ContentElement(BaseModel):
    """A single element in the document body."""
    type: ElementType
    # Heading / paragraph
    level: Optional[int] = None  # heading level 1-9
    content: Optional[list[TextRun]] = None
    style_ref: Optional[str] = None
    inline_style: Optional[ParagraphStyle] = None
    # Image
    data_base64: Optional[str] = None
    image_format: Optional[str] = None
    width_inches: Optional[float] = None
    height_inches: Optional[float] = None
    alt_text: Optional[str] = None
    image_position: Optional[ImagePosition] = None
    # Table
    rows: Optional[list[TableRow]] = None
    table_style_ref: Optional[str] = None
    inline_table_style: Optional[TableStyle] = None
    # List item
    list_type: Optional[ListType] = None
    list_level: Optional[int] = None
    bullet_char: Optional[str] = None
    number_format: Optional[str] = None
    # Hyperlink
    url: Optional[str] = None


# ---------------------------------------------------------------------------
# Top-level document content
# ---------------------------------------------------------------------------

class DocumentMetadata(BaseModel):
    source_file: Optional[str] = None
    source_type: Optional[str] = None  # "docx" | "pdf"
    extracted_at: Optional[str] = None
    page_count: Optional[int] = None
    author: Optional[str] = None
    title: Optional[str] = None


class DocumentContent(BaseModel):
    """The full content structure of a document."""
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    elements: list[ContentElement] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level document styling
# ---------------------------------------------------------------------------

class PageMargins(BaseModel):
    top_inches: float = 1.0
    bottom_inches: float = 1.0
    left_inches: float = 1.25
    right_inches: float = 1.25


class PageStyle(BaseModel):
    width_inches: float = 8.5
    height_inches: float = 11.0
    orientation: Orientation = Orientation.PORTRAIT
    margins: PageMargins = Field(default_factory=PageMargins)


class StyleMetadata(BaseModel):
    source_file: Optional[str] = None
    created_at: Optional[str] = None


class DocumentStyling(BaseModel):
    """The full styling definitions for a document — reusable across documents."""
    metadata: StyleMetadata = Field(default_factory=StyleMetadata)
    page_style: PageStyle = Field(default_factory=PageStyle)
    paragraph_styles: dict[str, ParagraphStyle] = Field(default_factory=dict)
    run_styles: dict[str, RunStyle] = Field(default_factory=dict)
    table_styles: dict[str, TableStyle] = Field(default_factory=dict)
    cell_styles: dict[str, CellStyle] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    """Result returned by extraction endpoints."""
    content: DocumentContent
    styling: DocumentStyling


class ApplyRequest(BaseModel):
    """Request body for the apply endpoint."""
    content: DocumentContent
    styling: DocumentStyling


class StyleTransferRequest(BaseModel):
    """Request body for style transfer — content from one doc, style from another."""
    content: DocumentContent
    styling: DocumentStyling


# ---------------------------------------------------------------------------
# Template fingerprint + draft structure (hackathon spec)
# ---------------------------------------------------------------------------

class HeadingSlot(BaseModel):
    """A single section slot in the template fingerprint."""
    slot_id: str
    level: int
    title: str
    required: bool = True
    expected_keywords: list[str] = Field(default_factory=list)
    placeholder_marker: Optional[str] = None  # e.g. "{{ slot_executive_summary }}"


class TableSchema(BaseModel):
    """The shape of a table in the template — column headers + row count expectation."""
    slot_id: Optional[str] = None
    header_row: list[str] = Field(default_factory=list)
    expected_columns: int = 0
    is_dynamic: bool = True  # True if the row count should scale to data


class HeaderFooterSection(BaseModel):
    """One section's header/footer text."""
    section_index: int
    header_text: Optional[str] = None
    footer_text: Optional[str] = None


class NumberingDef(BaseModel):
    """A list-numbering definition lifted from numbering.xml."""
    num_id: str
    abstract_num_id: Optional[str] = None
    levels: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TemplateFingerprint(BaseModel):
    """The structural fingerprint of a target template."""
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    page_style: PageStyle = Field(default_factory=PageStyle)
    heading_hierarchy: list[HeadingSlot] = Field(default_factory=list)
    numbering_defs: list[NumberingDef] = Field(default_factory=list)
    table_schemas: list[TableSchema] = Field(default_factory=list)
    headers_footers: list[HeaderFooterSection] = Field(default_factory=list)
    toc_location: Optional[int] = None  # paragraph index where TOC sits
    style_registry: DocumentStyling = Field(default_factory=DocumentStyling)
    # Raw template bytes (base64) so the docxtpl emitter can reuse the file.
    # Kept off the wire response when None.
    template_b64: Optional[str] = None
    # For PDF templates: original PDF kept so DI hints can be re-run.
    source_format: str = "docx"  # "docx" | "pdf"


class DraftSection(BaseModel):
    """A single semantically detected section from a draft document."""
    index: int
    heading: Optional[str] = None
    level: int = 1
    text: str = ""
    tables: list[list[list[str]]] = Field(default_factory=list)
    images_b64: list[str] = Field(default_factory=list)
    page_range: Optional[list[int]] = None  # [start, end]


class DraftStructure(BaseModel):
    """Section-tagged JSON tree of the inbound draft."""
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    sections: list[DraftSection] = Field(default_factory=list)


class MappingAction(str, enum.Enum):
    FILL = "fill"          # paste draft text directly
    REWRITE = "rewrite"    # LLM rewrites draft text into template voice
    RAG = "rag"            # no good draft match — pull from RAG
    FLAG = "flag"          # no match and no RAG hit — surface to reviewer


class Mapping(BaseModel):
    slot_id: str
    draft_section_idx: Optional[int] = None
    confidence: float = 0.0
    action: MappingAction = MappingAction.FLAG
    rationale: Optional[str] = None


class SectionMapping(BaseModel):
    mappings: list[Mapping] = Field(default_factory=list)


class ComplianceFlag(BaseModel):
    slot_id: str
    kind: str  # "missing" | "format" | "length" | "guidance"
    note: str


class ReviewDiff(BaseModel):
    slot_id: str
    title: str
    original: str
    proposed: str
    sources: list[str] = Field(default_factory=list)
    accepted: Optional[bool] = None
    reviewer_edit: Optional[str] = None


class ProcessJobResult(BaseModel):
    """The full result of the pipeline, returned to the caller."""
    job_id: str
    fingerprint: TemplateFingerprint
    structure: DraftStructure
    mapping: SectionMapping
    flags: list[ComplianceFlag] = Field(default_factory=list)
    diff: list[ReviewDiff] = Field(default_factory=list)
    artifact_docx_b64: Optional[str] = None
    artifact_pdf_b64: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class ReviewDecisions(BaseModel):
    """Reviewer-provided accept/edit/reject decisions for /review/diff."""
    job_id: str
    decisions: list[ReviewDiff]
    output_format: str = "docx"  # "docx" | "pdf" | "pdfa"

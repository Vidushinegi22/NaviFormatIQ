"""
Azure Document Intelligence wrapper + OCR fallbacks.

Used for:
  - Scanned PDFs (image-only, no extractable text)
  - PDF templates where we need semantic layout (titles, sections, tables,
    key-value pairs)

If Azure DI is not configured (`AZURE_DI_ENDPOINT`/`AZURE_DI_KEY` unset),
falls back to local `pytesseract` + `pdf2image` for OCR. If neither is
available, raises a clear error so the caller can surface it to the user.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

from config import settings
from models import (
    Alignment,
    ContentElement,
    DocumentContent,
    DocumentMetadata,
    DocumentStyling,
    ElementType,
    PageStyle,
    ParagraphStyle,
    StyleMetadata,
    TableCell,
    TableRow,
    TextRun,
)


# ---------------------------------------------------------------------------
# Simplified layout result (provider-agnostic)
# ---------------------------------------------------------------------------

@dataclass
class DiParagraph:
    text: str
    role: Optional[str] = None  # "title" | "sectionHeading" | "pageHeader" | ...
    page: int = 1


@dataclass
class DiTable:
    rows: list[list[str]]
    page: int = 1


@dataclass
class DiLayout:
    paragraphs: list[DiParagraph] = field(default_factory=list)
    tables: list[DiTable] = field(default_factory=list)
    page_count: int = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class OcrUnavailable(RuntimeError):
    """Raised when no OCR backend can be reached."""


def analyze_pdf(pdf_bytes: bytes, filename: str = "draft.pdf") -> DiLayout:
    """Run Azure Document Intelligence (preferred) or fall back to Tesseract.

    Returns a provider-agnostic ``DiLayout``.
    """
    if settings.azure_di_configured():
        return _analyze_azure(pdf_bytes)
    return _analyze_tesseract(pdf_bytes)


def layout_to_content(
    layout: DiLayout, filename: str
) -> tuple[DocumentContent, DocumentStyling]:
    """Convert a DiLayout into the existing ContentElement-based schema."""
    elements: list[ContentElement] = []

    role_to_level = {
        "title": 1,
        "sectionHeading": 2,
        "subSectionHeading": 3,
    }

    for p in layout.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        if p.role in role_to_level:
            elements.append(
                ContentElement(
                    type=ElementType.HEADING,
                    level=role_to_level[p.role],
                    content=[TextRun(text=text)],
                )
            )
        else:
            elements.append(
                ContentElement(
                    type=ElementType.PARAGRAPH,
                    content=[TextRun(text=text)],
                    inline_style=ParagraphStyle(alignment=Alignment.LEFT),
                )
            )

    for t in layout.tables:
        rows = [
            TableRow(
                cells=[TableCell(content=[TextRun(text=str(c))]) for c in row],
                is_header=(idx == 0),
            )
            for idx, row in enumerate(t.rows)
        ]
        elements.append(ContentElement(type=ElementType.TABLE, rows=rows))

    content = DocumentContent(
        metadata=DocumentMetadata(
            source_file=filename,
            source_type="pdf",
            page_count=layout.page_count,
        ),
        elements=elements,
    )
    styling = DocumentStyling(
        metadata=StyleMetadata(source_file=filename),
        page_style=PageStyle(),
    )
    return content, styling


# ---------------------------------------------------------------------------
# Azure DI implementation
# ---------------------------------------------------------------------------

def _analyze_azure(pdf_bytes: bytes) -> DiLayout:
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        from azure.core.credentials import AzureKeyCredential
    except ImportError as e:  # pragma: no cover
        raise OcrUnavailable(
            "azure-ai-documentintelligence is not installed. "
            "Run: pip install azure-ai-documentintelligence"
        ) from e

    client = DocumentIntelligenceClient(
        endpoint=settings.azure_di_endpoint,
        credential=AzureKeyCredential(settings.azure_di_key),
    )
    poller = client.begin_analyze_document(
        "prebuilt-layout",
        AnalyzeDocumentRequest(bytes_source=pdf_bytes),
    )
    result = poller.result()

    paragraphs = [
        DiParagraph(
            text=p.content or "",
            role=getattr(p, "role", None),
            page=(p.bounding_regions[0].page_number if p.bounding_regions else 1),
        )
        for p in (result.paragraphs or [])
    ]

    tables: list[DiTable] = []
    for tbl in result.tables or []:
        # Reconstruct the row/column grid from the flat cell list
        rows = max((c.row_index for c in tbl.cells), default=-1) + 1
        cols = max((c.column_index for c in tbl.cells), default=-1) + 1
        grid = [["" for _ in range(cols)] for _ in range(rows)]
        for c in tbl.cells:
            grid[c.row_index][c.column_index] = c.content or ""
        page = (
            tbl.bounding_regions[0].page_number if tbl.bounding_regions else 1
        )
        tables.append(DiTable(rows=grid, page=page))

    return DiLayout(
        paragraphs=paragraphs,
        tables=tables,
        page_count=len(result.pages or []) or 1,
    )


# ---------------------------------------------------------------------------
# Local OCR fallback (pytesseract + pdf2image)
# ---------------------------------------------------------------------------

def _analyze_tesseract(pdf_bytes: bytes) -> DiLayout:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError as e:
        raise OcrUnavailable(
            "OCR backend unavailable. Either configure Azure DI "
            "(AZURE_DI_ENDPOINT/KEY) or install: pip install pytesseract pdf2image "
            "(and the system 'tesseract' + 'poppler' binaries)."
        ) from e

    try:
        images = convert_from_bytes(pdf_bytes, dpi=200)
    except Exception as e:
        raise OcrUnavailable(f"Failed to rasterize PDF (is poppler installed?): {e}") from e

    paragraphs: list[DiParagraph] = []
    for page_idx, img in enumerate(images, start=1):
        text = pytesseract.image_to_string(img) or ""
        # Split into paragraph blocks at blank lines
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        for i, block in enumerate(blocks):
            # First non-empty line of the first block per page → treat as a heading
            role = None
            if i == 0 and page_idx == 1:
                role = "title"
            elif len(block) < 80 and block.endswith((":", ".")) is False and "\n" not in block:
                role = "sectionHeading"
            paragraphs.append(DiParagraph(text=block, role=role, page=page_idx))

    return DiLayout(paragraphs=paragraphs, tables=[], page_count=len(images))

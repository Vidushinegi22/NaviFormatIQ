"""
FastAPI Service for Document Formatting Extraction & Application
================================================================

Endpoints:
  POST /extract/word     — Upload .docx → returns content JSON + styling JSON
  POST /extract/pdf      — Upload .pdf  → returns content JSON + styling JSON
  POST /apply/docx       — Send content + styling JSON → returns formatted .docx
  POST /apply/style-transfer — Upload source doc (style) + target doc (content) → restyled .docx
  GET  /health           — Health check

Run with:
  uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import traceback
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from formater_apply import apply_styling, style_transfer
from models import (
    ApplyRequest,
    DocumentContent,
    DocumentStyling,
    DraftStructure,
    ExtractionResult,
    ProcessJobResult,
    ReviewDecisions,
    StyleTransferRequest,
    TemplateFingerprint,
)
from pdf_ext import extract_pdf_document, fingerprint_pdf_template, structure_pdf_draft
from pipeline import (
    apply_review_decisions,
    fingerprint_template,
    run_pipeline,
    structure_draft,
)
from word_ext import (
    extract_word_document,
    fingerprint_word_template,
    structure_word_draft,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Document Formatting API",
    description=(
        "Extract formatting, styling, and structure from Word/PDF documents "
        "into JSON, and apply styling from JSON to generate formatted documents."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
    }


# ---------------------------------------------------------------------------
# Extract Word
# ---------------------------------------------------------------------------

@app.post("/extract/word", tags=["Extraction"], response_model=ExtractionResult)
async def extract_word(file: UploadFile = File(..., description="A .docx file to extract")):
    """
    Upload a Word (.docx) file and receive structured content + styling as JSON.

    The response contains:
    - **content**: Structured document content (headings, paragraphs, tables, images, lists)
    - **styling**: Reusable style definitions (fonts, sizes, colors, spacing, page setup)
    """
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=400,
            detail="File must be a .docx Word document.",
        )

    try:
        file_bytes = await file.read()
        file_stream = io.BytesIO(file_bytes)
        content, styling = extract_word_document(
            file_stream=file_stream,
            filename=file.filename,
        )
        return ExtractionResult(content=content, styling=styling)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract Word document: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Extract PDF
# ---------------------------------------------------------------------------

@app.post("/extract/pdf", tags=["Extraction"], response_model=ExtractionResult)
async def extract_pdf(file: UploadFile = File(..., description="A .pdf file to extract")):
    """
    Upload a PDF file and receive structured content + styling as JSON.

    The response contains:
    - **content**: Structured document content (headings, paragraphs, tables, images)
    - **styling**: Inferred style definitions (fonts, sizes, colors, page setup)

    Note: PDF extraction uses heuristics for structural inference and may not be
    100% accurate for all document layouts.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="File must be a .pdf document.",
        )

    try:
        file_bytes = await file.read()
        file_stream = io.BytesIO(file_bytes)
        content, styling = extract_pdf_document(
            file_stream=file_stream,
            filename=file.filename,
        )
        return ExtractionResult(content=content, styling=styling)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract PDF document: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Apply styling → generate .docx
# ---------------------------------------------------------------------------

@app.post("/apply/docx", tags=["Application"])
async def apply_docx(request: ApplyRequest):
    """
    Send content JSON + styling JSON and receive a formatted .docx file.

    The content and styling can come from:
    - A previously extracted document
    - Manually constructed JSON
    - Content from one document + styling from another (style transfer)
    """
    try:
        docx_stream = apply_styling(request.content, request.styling)

        filename = request.content.metadata.source_file or "document"
        if not filename.endswith(".docx"):
            filename = os.path.splitext(filename)[0] + "_formatted.docx"

        return StreamingResponse(
            docx_stream,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply styling: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Apply styling from JSON file uploads
# ---------------------------------------------------------------------------

@app.post("/apply/from-json-files", tags=["Application"])
async def apply_from_json_files(
    content_file: UploadFile = File(..., description="Content JSON file"),
    styling_file: UploadFile = File(..., description="Styling JSON file"),
):
    """
    Upload content JSON and styling JSON files, receive a formatted .docx.

    This is useful when you have previously saved JSON files and want to
    regenerate a document from them.
    """
    try:
        content_bytes = await content_file.read()
        styling_bytes = await styling_file.read()

        content_data = json.loads(content_bytes)
        styling_data = json.loads(styling_bytes)

        content = DocumentContent(**content_data)
        styling = DocumentStyling(**styling_data)

        docx_stream = apply_styling(content, styling)

        return StreamingResponse(
            docx_stream,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": 'attachment; filename="output.docx"'
            },
        )

    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON file: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply styling from JSON files: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Style transfer
# ---------------------------------------------------------------------------

@app.post("/apply/style-transfer", tags=["Application"])
async def apply_style_transfer(
    style_source: UploadFile = File(..., description="Document whose look to copy (.docx or .pdf)"),
    content_source: UploadFile = File(..., description="Document whose content to keep (.docx)"),
    normalize_fonts: bool = Form(True, description="Force the whole body onto the source's font"),
    promote_headings: bool = Form(True, description="Detect & restyle pseudo-headings as real headings"),
):
    """
    Transfer the *visual identity* of one document onto the *content* of another.

    - **style_source**: the document whose formatting/look to adopt (.docx or .pdf)
    - **content_source**: the document whose text/structure to keep (.docx)

    This operates directly on the content document's OOXML package — it
    transplants/derives the style layer in place rather than rebuilding from
    scratch, so **every paragraph, table and image is preserved**. When the
    style source is a .docx its styles + theme are transplanted wholesale;
    when it is a .pdf a style profile (fonts, sizes, colours, alignment,
    margins, heading scale) is inferred and applied.

    Returns a new .docx with content_source's content wearing style_source's look.
    """
    from style_engine import transfer_style

    try:
        style_bytes = await style_source.read()
        style_filename = style_source.filename or "style_source"
        content_bytes = await content_source.read()
        content_filename = content_source.filename or "content_source.docx"

        if not content_filename.lower().endswith(".docx"):
            raise HTTPException(
                status_code=400,
                detail="content_source must be a .docx (its OOXML is edited in place).",
            )
        if not (style_filename.lower().endswith(".docx") or style_filename.lower().endswith(".pdf")):
            raise HTTPException(
                status_code=400,
                detail="style_source must be a .docx or .pdf file.",
            )

        result_bytes = transfer_style(
            content_bytes,
            content_filename,
            style_bytes,
            style_filename,
            normalize_fonts=normalize_fonts,
            promote_headings=promote_headings,
        )

        return StreamingResponse(
            io.BytesIO(result_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": 'attachment; filename="style_transferred.docx"'
            },
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Style transfer failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Extract and download JSON files
# ---------------------------------------------------------------------------

@app.post("/extract/word/content-json", tags=["Extraction"])
async def extract_word_content_json(file: UploadFile = File(...)):
    """Upload a .docx and download just the content JSON file."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="File must be .docx")

    try:
        file_bytes = await file.read()
        content, _ = extract_word_document(
            file_stream=io.BytesIO(file_bytes),
            filename=file.filename,
        )
        json_str = content.model_dump_json(exclude_none=True, indent=2)
        return StreamingResponse(
            io.BytesIO(json_str.encode("utf-8")),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{os.path.splitext(file.filename)[0]}_content.json"'
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract/word/styling-json", tags=["Extraction"])
async def extract_word_styling_json(file: UploadFile = File(...)):
    """Upload a .docx and download just the styling JSON file."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="File must be .docx")

    try:
        file_bytes = await file.read()
        _, styling = extract_word_document(
            file_stream=io.BytesIO(file_bytes),
            filename=file.filename,
        )
        json_str = styling.model_dump_json(exclude_none=True, indent=2)
        return StreamingResponse(
            io.BytesIO(json_str.encode("utf-8")),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{os.path.splitext(file.filename)[0]}_styling.json"'
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract/pdf/content-json", tags=["Extraction"])
async def extract_pdf_content_json(file: UploadFile = File(...)):
    """Upload a .pdf and download just the content JSON file."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be .pdf")

    try:
        file_bytes = await file.read()
        content, _ = extract_pdf_document(
            file_stream=io.BytesIO(file_bytes),
            filename=file.filename,
        )
        json_str = content.model_dump_json(exclude_none=True, indent=2)
        return StreamingResponse(
            io.BytesIO(json_str.encode("utf-8")),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{os.path.splitext(file.filename)[0]}_content.json"'
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract/pdf/styling-json", tags=["Extraction"])
async def extract_pdf_styling_json(file: UploadFile = File(...)):
    """Upload a .pdf and download just the styling JSON file."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be .pdf")

    try:
        file_bytes = await file.read()
        _, styling = extract_pdf_document(
            file_stream=io.BytesIO(file_bytes),
            filename=file.filename,
        )
        json_str = styling.model_dump_json(exclude_none=True, indent=2)
        return StreamingResponse(
            io.BytesIO(json_str.encode("utf-8")),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{os.path.splitext(file.filename)[0]}_styling.json"'
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Template fingerprinting + draft structuring (hackathon spec)
# ---------------------------------------------------------------------------

@app.post(
    "/fingerprint/template",
    tags=["Template Pipeline"],
    response_model=TemplateFingerprint,
)
async def fingerprint_template_endpoint(
    file: UploadFile = File(..., description="Template (.docx or .pdf)"),
    include_bytes: bool = False,
):
    """Extract a structural fingerprint from a template document.

    The fingerprint captures heading hierarchy, styles, numbering, tables,
    headers/footers, and the TOC location — everything the emitter needs to
    render a new draft into the same shell.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".docx", ".pdf"):
        raise HTTPException(
            status_code=400, detail="Template must be a .docx or .pdf file."
        )

    try:
        raw = await file.read()
        if ext == ".docx":
            fp = fingerprint_word_template(
                file_stream=io.BytesIO(raw),
                filename=file.filename,
                include_template_bytes=include_bytes,
            )
        else:
            fp = fingerprint_pdf_template(
                file_stream=io.BytesIO(raw), filename=file.filename
            )
        return fp
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Fingerprint failed: {e}"
        )


@app.post(
    "/structure/draft",
    tags=["Template Pipeline"],
    response_model=DraftStructure,
)
async def structure_draft_endpoint(
    file: UploadFile = File(..., description="Draft (.docx, .pdf, or .txt)"),
):
    """Convert a draft document into a section-tagged JSON tree."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    try:
        raw = await file.read()
        return structure_draft(raw, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Draft structuring failed: {e}"
        )


@app.post(
    "/process",
    tags=["Template Pipeline"],
    response_model=ProcessJobResult,
)
async def process_endpoint(
    template_file: UploadFile = File(..., description="Target template (.docx/.pdf)"),
    draft_file: UploadFile = File(..., description="Inbound draft (.docx/.pdf/.txt)"),
    domain_profile_id: str = Form("pharma"),
    output_format: str = Form("docx", description="docx | pdf | pdfa"),
):
    """End-to-end: template + draft → reformatted draft + diff for HITL."""
    if output_format not in ("docx", "pdf", "pdfa"):
        raise HTTPException(
            status_code=400,
            detail="output_format must be one of: docx, pdf, pdfa.",
        )
    try:
        template_bytes = await template_file.read()
        draft_bytes = await draft_file.read()
        result = run_pipeline(
            template_bytes=template_bytes,
            template_name=template_file.filename or "template.docx",
            draft_bytes=draft_bytes,
            draft_name=draft_file.filename or "draft.docx",
            domain_profile_id=domain_profile_id,
            output_format=output_format,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {e}")


@app.post("/review/diff", tags=["Template Pipeline"])
async def review_diff_endpoint(
    template_file: UploadFile = File(..., description="The same template used for /process"),
    decisions_json: str = Form(..., description="JSON-encoded ReviewDecisions payload"),
):
    """Apply reviewer accept/edit/reject decisions and emit the final artifact."""
    try:
        payload = json.loads(decisions_json)
        decisions = ReviewDecisions(**payload)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid decisions payload: {e}"
        )

    try:
        template_bytes = await template_file.read()
        docx_bytes, pdf_bytes, warnings = apply_review_decisions(
            template_bytes=template_bytes,
            template_name=template_file.filename or "template.docx",
            decisions=decisions.decisions,
            output_format=decisions.output_format,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Review apply failed: {e}")

    artifact = pdf_bytes if decisions.output_format in ("pdf", "pdfa") and pdf_bytes else docx_bytes
    media = (
        "application/pdf"
        if decisions.output_format in ("pdf", "pdfa") and pdf_bytes
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    ext = "pdf" if decisions.output_format in ("pdf", "pdfa") and pdf_bytes else "docx"

    headers = {
        "Content-Disposition": f'attachment; filename="reviewed_{decisions.job_id}.{ext}"',
        "X-Pipeline-Warnings": "|".join(warnings)[:500],
    }
    return StreamingResponse(io.BytesIO(artifact), media_type=media, headers=headers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )

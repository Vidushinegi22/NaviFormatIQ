"""Stateless utility endpoints (ported from tests/main.py) reused by the chat
agent and external callers."""
from __future__ import annotations

import io
import re

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.agents.nodes.common import DOCX_MIME
from app.core.concurrency import run_sync
from app.core.exceptions import BadRequestError
from app.schemas.document_model import ApplyRequest
from app.storage.base import content_disposition

_DOC_EXT_RE = re.compile(r"\.(docx|pdf|doc|txt)$", re.IGNORECASE)


def _stem(filename: str | None, fallback: str = "document") -> str:
    """Original name minus its extension, for building a download filename."""
    return _DOC_EXT_RE.sub("", (filename or "").strip()) or fallback

router = APIRouter(prefix="/api/v1", tags=["Utilities"])


@router.post("/extract/word")
async def extract_word(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".docx"):
        raise BadRequestError("file must be a .docx")
    data = await file.read()
    from app.services.extraction.word_ext import extract_word_document

    def _do():
        return extract_word_document(file_stream=io.BytesIO(data), filename=file.filename)

    content, styling = await run_sync(_do)
    return {
        "content": content.model_dump(exclude_none=True),
        "styling": styling.model_dump(exclude_none=True),
    }


@router.post("/extract/pdf")
async def extract_pdf(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise BadRequestError("file must be a .pdf")
    data = await file.read()
    from app.services.extraction.pdf_ext import extract_pdf_document

    def _do():
        return extract_pdf_document(file_stream=io.BytesIO(data), filename=file.filename)

    content, styling = await run_sync(_do)
    return {
        "content": content.model_dump(exclude_none=True),
        "styling": styling.model_dump(exclude_none=True),
    }


@router.post("/apply/docx")
async def apply_docx(req: ApplyRequest):
    from app.services.formatting.formater_apply import apply_styling

    stream = await run_sync(apply_styling, req.content, req.styling)
    return StreamingResponse(
        stream,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": content_disposition("document.docx")},
    )


@router.post("/apply/style-transfer")
async def style_transfer_ep(
    style_source: UploadFile = File(...),
    content_source: UploadFile = File(...),
    normalize_fonts: bool = Form(True),
    promote_headings: bool = Form(True),
):
    from app.services.style.style_engine import transfer_style_smart

    style_bytes = await style_source.read()
    content_bytes = await content_source.read()
    if not (content_source.filename or "").lower().endswith(".docx"):
        raise BadRequestError("content_source must be a .docx")

    def _do():
        return transfer_style_smart(
            content_bytes,
            content_source.filename or "content.docx",
            style_bytes,
            style_source.filename or "style",
            mode="auto",
            normalize_fonts=normalize_fonts,
            promote_headings=promote_headings,
        ).docx_bytes

    out = await run_sync(_do)
    download_name = f"{_stem(content_source.filename)}-styled.docx"
    return StreamingResponse(
        io.BytesIO(out),
        media_type=DOCX_MIME,
        headers={"Content-Disposition": content_disposition(download_name)},
    )


@router.post("/fingerprint/template")
async def fingerprint_template_ep(file: UploadFile = File(...)):
    from app.services.orchestration.pipeline_steps import fingerprint_template

    data = await file.read()

    def _do():
        return fingerprint_template(data, file.filename or "template.docx")

    fp = await run_sync(_do)
    d = fp.model_dump(exclude_none=True)
    d.pop("template_b64", None)
    return JSONResponse(d)


@router.post("/structure/draft")
async def structure_draft_ep(file: UploadFile = File(...)):
    from app.services.orchestration.pipeline_steps import structure_draft

    data = await file.read()

    def _do():
        return structure_draft(data, file.filename or "draft.docx")

    ds = await run_sync(_do)
    return JSONResponse(ds.model_dump(exclude_none=True))

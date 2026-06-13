"""Flow runs: start (Flow 1/2/3), SSE stream, HITL resume, export, cancel."""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runner import execute_run, resume_run
from app.core.concurrency import run_sync
from app.core.events import get_event_bus
from app.core.exceptions import BadRequestError, NotFoundError
from app.deps import get_db, resolve_artifact_uri
from app.models.artifact import Artifact
from app.models.compliance import ComplianceFinding, ComplianceReport
from app.models.project import Project
from app.models.run import Run
from app.schemas.api import (
    ArtifactRead,
    ComplianceRequest,
    RegenerateRequest,
    ResumeRequest,
    RevisionSuggestion,
    RunDetail,
    RunStarted,
    StyleInterpretRequest,
    StyleInterpretResponse,
    StyleRequest,
    SuggestionsResponse,
    SuggestRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Flows"])


async def _new_run(db, project_id, flow, mode, domain_id, input_refs) -> Run:
    if not await db.get(Project, project_id):
        raise NotFoundError("project not found")
    run = Run(
        project_id=project_id, flow=flow, mode=mode, domain_id=domain_id,
        status="pending", input_refs=input_refs, state={},
    )
    db.add(run)
    await db.flush()
    run.langgraph_thread_id = str(run.id)
    # Commit now so the row is durable before we schedule the background task
    # (FastAPI may run the task before the request-dependency teardown commit).
    await db.commit()
    return run


@router.post("/projects/{project_id}/flows/regenerate", response_model=RunStarted)
async def start_regenerate(
    project_id: uuid.UUID,
    body: RegenerateRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    draft_uri, _, draft_aid = await resolve_artifact_uri(
        db, artifact_id=body.draft_artifact_id, document_id=body.draft_document_id
    )
    if body.template_artifact_id or body.template_document_id:
        tpl_uri, _, _ = await resolve_artifact_uri(
            db, artifact_id=body.template_artifact_id, document_id=body.template_document_id
        )
    else:
        tpl_uri = draft_uri  # prior version is both structure donor and content

    # Other uploaded versions of the document — loaded read-only so the
    # generator can reason about what changed across revisions. Best-effort:
    # a missing/removed context doc is skipped rather than failing the run.
    context_uris: list[str] = []
    context_names: list[str] = []
    for cid in body.context_document_ids:
        try:
            c_uri, c_name, _ = await resolve_artifact_uri(db, document_id=cid)
        except NotFoundError:
            continue
        if c_uri != draft_uri:  # never feed the base doc back as its own context
            context_uris.append(c_uri)
            context_names.append(c_name)

    run = await _new_run(db, project_id, "regenerate", None, body.domain_id, {"draft": str(draft_aid)})
    state = {
        "run_id": str(run.id), "project_id": str(project_id), "flow": "regenerate",
        "template_file_uri": tpl_uri, "draft_file_uri": draft_uri,
        "domain_id": body.domain_id, "output_format": body.output_format,
        "user_suggestions": body.user_suggestions,
        "skip_ai_rewrite": body.skip_ai_rewrite,
        "version_bump": body.version_bump,
        "target_version": body.target_version,
        "context_file_uris": context_uris,
        "context_file_names": context_names,
        "status": "pending", "warnings": [], "traces": [],
    }
    background.add_task(execute_run, str(run.id), "regenerate", state)
    return RunStarted(run_id=run.id, status="pending")


@router.post(
    "/projects/{project_id}/flows/regenerate/suggest",
    response_model=SuggestionsResponse,
)
async def suggest_regenerate(
    project_id: uuid.UUID,
    body: SuggestRequest,
    db: AsyncSession = Depends(get_db),
):
    """Smart, domain-aware change suggestions for the new version (pre-run).

    Substantive, content-level ideas (add a point, expand or add a section)
    grounded in the document and its prior versions — never grammar/spelling.
    Best-effort: returns an empty list when the LLM is unavailable.
    """
    if not await db.get(Project, project_id):
        raise NotFoundError("project not found")
    draft_uri, draft_name, _ = await resolve_artifact_uri(
        db, artifact_id=body.draft_artifact_id, document_id=body.draft_document_id
    )
    context: list[tuple[str, str]] = []
    for cid in body.context_document_ids:
        try:
            c_uri, c_name, _ = await resolve_artifact_uri(db, document_id=cid)
        except NotFoundError:
            continue
        if c_uri != draft_uri:  # never feed the base doc back as its own context
            context.append((c_name, c_uri))

    def _work() -> list[dict]:
        from app.agents.nodes.common import load_bytes
        from app.services.generation.doc_profile import build_profile_and_updates
        from app.services.generation.suggestions import suggest_revision_changes
        from app.services.orchestration.pipeline_steps import structure_draft

        draft = structure_draft(load_bytes(draft_uri), draft_name)
        profile = build_profile_and_updates(draft, version_bump="none", use_llm=False)
        doc_type = (profile.get("profile") or {}).get("doc_type") or "document"
        priors: list[tuple[str, object]] = []
        for label, uri in context[:3]:
            try:
                priors.append((label, structure_draft(load_bytes(uri), label)))
            except Exception:  # noqa: BLE001 — context is best-effort
                continue
        return suggest_revision_changes(
            draft, priors, doc_type=doc_type, domain=body.domain_id or "", max_suggestions=3
        )

    suggestions = await run_sync(_work)
    return SuggestionsResponse(suggestions=[RevisionSuggestion(**s) for s in suggestions])


@router.post("/projects/{project_id}/flows/style", response_model=RunStarted)
async def start_style(
    project_id: uuid.UUID,
    body: StyleRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    content_uri, _, content_aid = await resolve_artifact_uri(
        db, artifact_id=body.content_artifact_id, document_id=body.content_document_id
    )
    style_uri, _, style_aid = await resolve_artifact_uri(
        db, artifact_id=body.style_artifact_id, document_id=body.style_document_id
    )
    run = await _new_run(
        db, project_id, "style", None, None, {"content": str(content_aid), "style": str(style_aid)}
    )
    state = {
        "run_id": str(run.id), "project_id": str(project_id), "flow": "style",
        "template_file_uri": style_uri,   # style source
        "draft_file_uri": content_uri,    # content target
        "normalize_fonts": body.normalize_fonts, "promote_headings": body.promote_headings,
        "style_source_mode": body.style_source_mode,
        "output_format": body.output_format, "status": "pending", "warnings": [], "traces": [],
    }
    background.add_task(execute_run, str(run.id), "style", state)
    return RunStarted(run_id=run.id, status="pending")


@router.post("/projects/{project_id}/style/interpret", response_model=StyleInterpretResponse)
async def interpret_style(
    project_id: uuid.UUID,
    body: StyleInterpretRequest,
    db: AsyncSession = Depends(get_db),
):
    """Preview how the style source will be interpreted (classify + extract
    rules) without producing a document. Powers the Style page's detection
    card and lets the user confirm or override the mode before applying."""
    if not await db.get(Project, project_id):
        raise NotFoundError("project not found")
    style_uri, style_name, _ = await resolve_artifact_uri(
        db, artifact_id=body.style_artifact_id, document_id=body.style_document_id
    )

    def _interpret() -> StyleInterpretResponse:
        from app.agents.nodes.common import load_bytes
        from app.services.style.guideline_interpreter import (
            decide_style_mode,
            extract_style_source_text,
            interpret_guideline,
            style_spec_summary,
        )

        data = load_bytes(style_uri)
        digest = extract_style_source_text(data, style_name)
        effective, cls = decide_style_mode(digest, body.mode)
        spec_dump: dict | None = None
        notes: list[str] = []
        palette: dict = {}
        if effective == "guideline":
            spec = interpret_guideline(digest)
            spec_dump = spec.model_dump(exclude_none=True)
            summary = style_spec_summary(spec)
            notes = spec.notes
            palette = spec.colors
        else:
            summary = "We'll transplant this document's own visual identity onto your target."
        return StyleInterpretResponse(
            effective_kind=effective,
            detected_kind=cls.kind,
            confidence=cls.confidence,
            reason=cls.reason,
            method=cls.method,
            summary=summary,
            spec=spec_dump,
            notes=notes,
            palette=palette,
        )

    return await run_sync(_interpret)


@router.post("/projects/{project_id}/flows/compliance", response_model=RunStarted)
async def start_compliance(
    project_id: uuid.UUID,
    body: ComplianceRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Audit a document against a pre-loaded guideline (read-only, multi-dimensional)."""
    if not body.guideline_id:
        raise BadRequestError("guideline_id is required for a compliance audit")
    draft_uri, _, draft_aid = await resolve_artifact_uri(
        db, artifact_id=body.draft_artifact_id, document_id=body.draft_document_id
    )
    run = await _new_run(
        db,
        project_id,
        "compliance",
        "check",
        body.domain_id,
        {"draft": str(draft_aid), "guideline_id": str(body.guideline_id)},
    )
    state = {
        "run_id": str(run.id),
        "project_id": str(project_id),
        "flow": "compliance",
        "domain_id": body.domain_id,
        "guideline_id": str(body.guideline_id),
        "draft_file_uri": draft_uri,
        "output_format": body.output_format,
        "dimensions": body.dimensions,
        "status": "pending",
        "warnings": [],
        "traces": [],
    }
    background.add_task(execute_run, str(run.id), "compliance", state)
    return RunStarted(run_id=run.id, status="pending")


def _finding_dict(r: ComplianceFinding) -> dict:
    """Serialize a finding row into the UI shape (frontend uses `section`)."""
    return {
        "id": str(r.id),
        "section": r.section_no,
        "section_title": r.section_title,
        "requirement_title": r.requirement_title,
        "dimension": r.dimension,
        "status": r.status,
        "severity": r.severity,
        "confidence": r.confidence,
        "evidence": r.evidence,
        "doc_location": r.doc_location,
        "rationale": r.rationale,
        "citation": r.citation,
        "suggested_fix": r.suggested_fix,
    }


_SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


async def _compliance_block(db: AsyncSession, run: Run) -> Optional[dict]:
    """Assemble the rich ComplianceResult: summary from Run.state, findings from the table."""
    st = run.state or {}
    summary = st.get("compliance")
    if not summary:
        rep = (
            await db.execute(select(ComplianceReport).where(ComplianceReport.run_id == run.id))
        ).scalars().first()
        if not rep:
            return None
        summary = {
            "overall_score": rep.overall_score,
            "status_label": rep.status_label,
            "per_dimension": rep.per_dimension or {},
            "per_section": rep.per_section or [],
            "severity_counts": rep.severity_counts or {},
            "summary": rep.summary,
            "guideline": None,
        }
    rows = (
        await db.execute(select(ComplianceFinding).where(ComplianceFinding.run_id == run.id))
    ).scalars().all()
    findings = sorted(
        (_finding_dict(r) for r in rows),
        key=lambda f: (_SEV_RANK.get(f["severity"], 9), f.get("section") or ""),
    )
    return {**summary, "findings": findings}


@router.get("/flows/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise NotFoundError("run not found")
    st = run.state or {}
    arts = (
        await db.execute(select(Artifact).where(Artifact.run_id == run_id))
    ).scalars().all()

    compliance = None
    compliance_score = st.get("compliance_score")
    flags = st.get("flags") or []
    if run.flow == "compliance":
        compliance = await _compliance_block(db, run)
        if compliance:
            compliance_score = compliance.get("overall_score")
            # Back-compat: surface issue findings as legacy flags too.
            flags = [
                {"slot_id": f.get("section") or "", "kind": f.get("dimension", ""), "note": f.get("requirement_title", "")}
                for f in compliance.get("findings", [])
                if f.get("status") in ("non_compliant", "partial")
            ]

    return RunDetail(
        id=run.id, flow=run.flow, mode=run.mode, status=run.status, domain_id=run.domain_id,
        diff=st.get("diff") or [], flags=flags, coverage=st.get("coverage"),
        compliance_score=compliance_score,
        traces=st.get("traces") or [], rendered_docx_uri=st.get("rendered_docx_uri"),
        rendered_pdf_uri=st.get("rendered_pdf_uri"), warnings=st.get("warnings") or [],
        error=run.error, artifacts=[ArtifactRead.model_validate(a) for a in arts],
        style_interpretation=st.get("style_interpretation"),
        doc_profile=st.get("doc_profile"), field_updates=st.get("field_updates"),
        compliance=compliance,
    )


@router.get("/flows/{run_id}/findings")
async def get_findings(
    run_id: uuid.UUID,
    dimension: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    section: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Filterable compliance findings for a run (chat + drill-down)."""
    if not await db.get(Run, run_id):
        raise NotFoundError("run not found")
    stmt = select(ComplianceFinding).where(ComplianceFinding.run_id == run_id)
    if dimension:
        stmt = stmt.where(ComplianceFinding.dimension == dimension)
    if severity:
        stmt = stmt.where(ComplianceFinding.severity == severity)
    if status:
        stmt = stmt.where(ComplianceFinding.status == status)
    if section:
        stmt = stmt.where(ComplianceFinding.section_no.startswith(section))
    rows = (await db.execute(stmt)).scalars().all()
    findings = sorted(
        (_finding_dict(r) for r in rows),
        key=lambda f: (_SEV_RANK.get(f["severity"], 9), f.get("section") or ""),
    )
    return {"findings": findings, "count": len(findings)}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/flows/{run_id}/stream")
async def stream_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise NotFoundError("run not found")
    status0 = run.status
    state0 = run.state or {}
    bus = get_event_bus()

    async def gen():
        yield _sse({"agent": "system", "status": status0, "message": "connected"})
        if status0 in ("done", "error", "cancelled"):
            yield _sse({
                "agent": "system", "status": status0,
                "payload": {
                    "rendered_docx_uri": state0.get("rendered_docx_uri"),
                    "rendered_pdf_uri": state0.get("rendered_pdf_uri"),
                    "diff": state0.get("diff"), "flags": state0.get("flags"),
                },
            })
            return
        if status0 == "hitl":
            yield _sse({"agent": "review_gate", "status": "hitl",
                        "payload": {"diff": state0.get("diff"), "flags": state0.get("flags")}})
            return
        async for ev in bus.subscribe(str(run_id)):
            yield _sse(ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/flows/{run_id}/resume", response_model=RunStarted)
async def resume(
    run_id: uuid.UUID,
    body: ResumeRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(Run, run_id)
    if not run:
        raise NotFoundError("run not found")
    if run.status != "hitl":
        raise BadRequestError("run is not awaiting review")
    decisions = [d.model_dump() for d in body.decisions]
    background.add_task(resume_run, str(run_id), decisions)
    return RunStarted(run_id=run_id, status="running")


@router.post("/flows/{run_id}/export")
async def export(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise NotFoundError("run not found")
    st = run.state or {}
    arts = (
        await db.execute(select(Artifact).where(Artifact.run_id == run_id))
    ).scalars().all()
    return {
        "rendered_docx_uri": st.get("rendered_docx_uri"),
        "rendered_pdf_uri": st.get("rendered_pdf_uri"),
        "artifacts": [str(a.id) for a in arts],
    }


@router.post("/flows/{run_id}/cancel")
async def cancel(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise NotFoundError("run not found")
    run.status = "cancelled"
    await db.flush()
    await get_event_bus().publish(str(run_id), {"agent": "system", "status": "cancelled"})
    return {"ok": True}

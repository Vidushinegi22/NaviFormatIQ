"""Drives flow graphs: streams node events to the EventBus (SSE) and persists
run status/traces + output artifacts/versions to Neon."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import select

from app.agents.graphs import get_compiled_graph
from app.agents.nodes.common import DOCX_MIME, PDF_MIME
from app.core.db import get_sessionmaker
from app.core.events import get_event_bus
from app.core.logging import get_logger
from app.models.artifact import Artifact
from app.models.document import Document, DocumentVersion
from app.models.run import Run

log = get_logger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _jsonable(x: Any) -> Any:
    try:
        return json.loads(json.dumps(x, default=str))
    except Exception:  # noqa: BLE001
        return {}


def _summary(update: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (update or {}).items():
        if v is None or isinstance(v, (int, float, bool)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v if len(v) <= 200 else v[:200] + "…"
        elif isinstance(v, list):
            out[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            out[k] = f"{{{len(v)} keys}}"
        else:
            out[k] = type(v).__name__
    return out


async def _set_run(run_id: str, **fields: Any) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        run = await s.get(Run, uuid.UUID(run_id))
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        await s.commit()


async def _get_run(run_id: str) -> Run | None:
    sm = get_sessionmaker()
    async with sm() as s:
        return await s.get(Run, uuid.UUID(run_id))


async def _drive(run_id: str, flow: str, graph_input: Any):
    bus = get_event_bus()
    graph = get_compiled_graph(flow)
    config = {"configurable": {"thread_id": run_id}}
    new_traces: list[dict] = []
    async for chunk in graph.astream(graph_input, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                continue
            summary = _summary(update if isinstance(update, dict) else {})
            new_traces.append({"agent": node, "ts": _utcnow().isoformat(), "summary": summary})
            await bus.publish(run_id, {"agent": node, "status": "running", "payload": summary})
    snap = await graph.aget_state(config)
    return snap, new_traces


async def _handle_snapshot(run_id: str, snap, new_traces: list[dict]) -> None:
    bus = get_event_bus()
    full = dict(snap.values)
    prev = await _get_run(run_id)
    prior_traces = (prev.state or {}).get("traces", []) if prev else []
    full["traces"] = list(prior_traces) + new_traces

    if snap.next:  # paused at an interrupt → HITL
        await _set_run(run_id, status="hitl", state=_jsonable(full))
        await bus.publish(
            run_id,
            {
                "agent": "review_gate",
                "status": "hitl",
                "payload": {
                    "diff": full.get("diff"),
                    "flags": full.get("flags"),
                    "coverage": full.get("coverage"),
                },
            },
        )
    elif full.get("flow") == "compliance":
        await _finalize_compliance(run_id, full)
        compliance = full.get("compliance") or {}
        # Drop heavy intermediates so the persisted Run.state stays small —
        # findings live in compliance_findings; RunDetail rehydrates from there.
        for k in ("findings", "scores", "guideline_ref", "alignment", "doc_sections", "full_text"):
            full.pop(k, None)
        await _set_run(run_id, status="done", state=_jsonable(full), finished_at=_utcnow())
        await bus.publish(
            run_id,
            {
                "agent": "system",
                "status": "done",
                "payload": {
                    "overall_score": compliance.get("overall_score"),
                    "severity_counts": compliance.get("severity_counts"),
                    "rendered_pdf_uri": full.get("rendered_pdf_uri"),
                },
            },
        )
    else:
        await _finalize(run_id, full)
        await _set_run(run_id, status="done", state=_jsonable(full), finished_at=_utcnow())
        await bus.publish(
            run_id,
            {
                "agent": "system",
                "status": "done",
                "payload": {
                    "rendered_docx_uri": full.get("rendered_docx_uri"),
                    "rendered_pdf_uri": full.get("rendered_pdf_uri"),
                },
            },
        )


async def _finalize(run_id: str, full: dict[str, Any]) -> None:
    docx_uri = full.get("rendered_docx_uri")
    pdf_uri = full.get("rendered_pdf_uri")
    pid = full.get("project_id")
    if not pid or not (docx_uri or pdf_uri):
        return
    sm = get_sessionmaker()
    async with sm() as s:
        first_artifact_id = None
        if docx_uri:
            a = Artifact(
                project_id=uuid.UUID(pid), run_id=uuid.UUID(run_id), uri=docx_uri,
                kind="rendered_docx", filename="output.docx", mime=DOCX_MIME,
            )
            s.add(a)
            await s.flush()
            first_artifact_id = a.id
        if pdf_uri:
            s.add(Artifact(
                project_id=uuid.UUID(pid), run_id=uuid.UUID(run_id), uri=pdf_uri,
                kind="rendered_pdf", filename="output.pdf", mime=PDF_MIME,
            ))
        doc = (
            await s.execute(
                select(Document).where(
                    Document.project_id == uuid.UUID(pid), Document.kind == "output"
                )
            )
        ).scalars().first()
        if not doc:
            doc = Document(
                project_id=uuid.UUID(pid), kind="output",
                display_name="Generated output", current_version=0,
            )
            s.add(doc)
            await s.flush()
        doc.current_version += 1
        s.add(DocumentVersion(
            document_id=doc.id,
            version=doc.current_version,
            artifact_id=first_artifact_id,
            structure_json={
                "rewritten": full.get("rewritten"),
                "diff": full.get("diff"),
                "flags": full.get("flags"),
                "coverage": full.get("coverage"),
            },
            created_by_run_id=uuid.UUID(run_id),
        ))
        await s.commit()


async def _finalize_compliance(run_id: str, full: dict[str, Any]) -> None:
    """Persist compliance findings + the report rollup + report artifacts."""
    from app.models.compliance import ComplianceFinding, ComplianceReport

    pid = full.get("project_id")
    findings = full.get("findings") or []
    compliance = full.get("compliance") or {}
    report_uris = full.get("report_uris") or {}
    gid = full.get("guideline_id")

    _KINDS = {
        "docx": ("compliance_report_docx", DOCX_MIME),
        "pdf": ("compliance_report_pdf", PDF_MIME),
        "json": ("compliance_report_json", "application/json"),
        "csv": ("compliance_report_csv", "text/csv"),
    }
    sm = get_sessionmaker()
    async with sm() as s:
        for f in findings:
            req_id = f.get("requirement_id")
            s.add(
                ComplianceFinding(
                    run_id=uuid.UUID(run_id),
                    requirement_id=uuid.UUID(req_id) if req_id else None,
                    section_no=f.get("section_no"),
                    section_title=f.get("section_title"),
                    requirement_title=f.get("requirement_title", ""),
                    dimension=f.get("dimension", "content"),
                    status=f.get("status", "non_compliant"),
                    severity=f.get("severity", "major"),
                    confidence=float(f.get("confidence") or 0.0),
                    evidence=f.get("evidence"),
                    doc_location=f.get("doc_location"),
                    rationale=f.get("rationale"),
                    citation=f.get("citation"),
                    suggested_fix=f.get("suggested_fix"),
                )
            )
        art_ids: dict[str, Any] = {}
        for fmt, uri in (report_uris or {}).items():
            if not uri or fmt not in _KINDS:
                continue
            kind, mime = _KINDS[fmt]
            a = Artifact(
                project_id=uuid.UUID(pid) if pid else None,
                run_id=uuid.UUID(run_id),
                uri=uri,
                kind=kind,
                filename=f"compliance-report.{fmt}",
                mime=mime,
            )
            s.add(a)
            await s.flush()
            art_ids[fmt] = a.id
        s.add(
            ComplianceReport(
                run_id=uuid.UUID(run_id),
                guideline_id=uuid.UUID(gid) if gid else None,
                overall_score=float(compliance.get("overall_score") or 0.0),
                status_label=compliance.get("status_label"),
                per_dimension=compliance.get("per_dimension") or {},
                per_section=compliance.get("per_section") or [],
                severity_counts=compliance.get("severity_counts") or {},
                summary=compliance.get("summary"),
                docx_artifact_id=art_ids.get("docx"),
                pdf_artifact_id=art_ids.get("pdf"),
                json_artifact_id=art_ids.get("json"),
                csv_artifact_id=art_ids.get("csv"),
            )
        )
        await s.commit()


async def execute_run(run_id: str, flow: str, initial_state: dict[str, Any]) -> None:
    bus = get_event_bus()
    await _set_run(run_id, status="running", started_at=_utcnow())
    await bus.publish(run_id, {"agent": "system", "status": "running", "message": "started"})
    try:
        snap, traces = await _drive(run_id, flow, initial_state)
        await _handle_snapshot(run_id, snap, traces)
    except Exception as e:  # noqa: BLE001
        log.exception("run %s failed", run_id)
        await _set_run(run_id, status="error", error=str(e)[:2000], finished_at=_utcnow())
        await bus.publish(run_id, {"agent": "system", "status": "error", "message": str(e)[:300]})


async def resume_run(run_id: str, decisions: list[dict]) -> None:
    bus = get_event_bus()
    run = await _get_run(run_id)
    if not run:
        return
    flow = run.flow
    config = {"configurable": {"thread_id": run_id}}
    graph = get_compiled_graph(flow)
    await graph.aupdate_state(config, {"hitl_feedback": decisions})
    await _set_run(run_id, status="running")
    await bus.publish(run_id, {"agent": "system", "status": "running", "message": "resumed"})
    try:
        snap, traces = await _drive(run_id, flow, None)
        await _handle_snapshot(run_id, snap, traces)
    except Exception as e:  # noqa: BLE001
        log.exception("resume %s failed", run_id)
        await _set_run(run_id, status="error", error=str(e)[:2000], finished_at=_utcnow())
        await bus.publish(run_id, {"agent": "system", "status": "error", "message": str(e)[:300]})

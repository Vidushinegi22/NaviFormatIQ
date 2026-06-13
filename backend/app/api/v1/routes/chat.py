"""Doc-chat sessions + messages (runs the tool-calling agent)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.agent import run_chat_agent
from app.core.concurrency import run_sync
from app.core.exceptions import NotFoundError
from app.deps import get_db, resolve_artifact_uri
from app.models.chat import ChatMessage, ChatSession
from app.models.compliance import ComplianceFinding, Guideline
from app.models.run import Run
from app.schemas.api import (
    ChatMessageCreate,
    ChatMessageRead,
    ChatSessionCreate,
    ChatSessionRead,
    ChatTurnResponse,
)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

_SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


async def _compliance_context(db: AsyncSession, run_id: uuid.UUID) -> str:
    """A compact digest of an audit run's scores + open issues for chat grounding."""
    run = await db.get(Run, run_id)
    if not run:
        return ""
    comp = (run.state or {}).get("compliance") or {}
    lines: list[str] = []
    if comp:
        lines.append(
            f"Overall: {round((comp.get('overall_score') or 0) * 100)}% "
            f"({comp.get('status_label')}). Severity counts: {comp.get('severity_counts')}."
        )
        weak = [s for s in (comp.get("per_section") or []) if (s.get("findings_count") or 0) > 0]
        if weak:
            lines.append("Sections with issues: " + ", ".join(
                f"{s.get('section')} {s.get('title','')} ({s.get('findings_count')})" for s in weak[:12]
            ))
    rows = (
        await db.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.run_id == run_id,
                ComplianceFinding.status.in_(["non_compliant", "partial"]),
            )
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda r: _SEV_RANK.get(r.severity, 9))[:60]
    if rows:
        lines.append("\nTop open findings (section | severity/dimension | requirement → fix):")
        for r in rows:
            fix = (r.suggested_fix or "").strip()
            lines.append(
                f"- [{r.section_no}] {r.severity}/{r.dimension}: {r.requirement_title}"
                + (f" → {fix[:160]}" if fix else "")
            )
    return "\n".join(lines)[:6000]


@router.post("/sessions", response_model=ChatSessionRead)
async def create_session(body: ChatSessionCreate, db: AsyncSession = Depends(get_db)):
    cs = ChatSession(
        project_id=body.project_id,
        title=body.title,
        subject_document_id=body.subject_document_id,
        subject_run_id=body.subject_run_id,
        guideline_id=body.guideline_id,
    )
    db.add(cs)
    await db.flush()
    return cs


@router.get("/sessions/{session_id}")
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    cs = await db.get(ChatSession, session_id)
    if not cs:
        raise NotFoundError("session not found")
    msgs = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    return {
        "session": ChatSessionRead.model_validate(cs),
        "messages": [ChatMessageRead.model_validate(m) for m in msgs],
    }


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageRead])
async def list_messages(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResponse)
async def post_message(
    session_id: uuid.UUID, body: ChatMessageCreate, db: AsyncSession = Depends(get_db)
):
    cs = await db.get(ChatSession, session_id)
    if not cs:
        raise NotFoundError("session not found")

    subject_uri = None
    subj_doc = body.subject_document_id or cs.subject_document_id
    if body.subject_artifact_id or subj_doc:
        subject_uri, _, _ = await resolve_artifact_uri(
            db, artifact_id=body.subject_artifact_id, document_id=subj_doc
        )

    # Compliance grounding: resolve the guideline code + an audit findings digest.
    guideline_code = None
    guideline_note = None
    gid = body.guideline_id or cs.guideline_id
    if gid:
        g = await db.get(Guideline, gid)
        guideline_code = g.code if g else None
        # An unpublished guideline has no usable search index — tell the agent
        # so it answers honestly instead of citing empty search results.
        if g and g.status != "ready":
            guideline_note = (
                f"NOTE: guideline {g.code} is not available yet (status: {g.status}); "
                "searching it will return nothing. Tell the user the guideline is "
                "still being prepared rather than citing it."
            )
    compliance_context = None
    run_id = body.subject_run_id or cs.subject_run_id
    if run_id:
        compliance_context = await _compliance_context(db, run_id)
    if guideline_note:
        compliance_context = (
            f"{guideline_note}\n\n{compliance_context}" if compliance_context else guideline_note
        )

    prior = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    history = [{"role": m.role, "content": m.content} for m in prior]

    db.add(ChatMessage(session_id=session_id, role="user", content=body.message))
    await db.flush()

    result = await run_sync(
        run_chat_agent,
        body.message,
        subject_uri=subject_uri,
        history=history,
        guideline_code=guideline_code,
        compliance_context=compliance_context,
    )
    answer = result.get("answer", "")
    steps = result.get("steps", [])

    db.add(
        ChatMessage(
            session_id=session_id,
            role="assistant",
            content=answer,
            tool_result_ref={"steps": steps} if steps else None,
        )
    )
    await db.flush()
    return ChatTurnResponse(answer=answer, steps=steps)

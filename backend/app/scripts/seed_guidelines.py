"""Ingest a pre-loaded guideline (default: ICH E3) into the registry + Qdrant.

    python -m app.scripts.seed_guidelines                       # ICH-E3 from repo root
    python -m app.scripts.seed_guidelines --pdf /path/to.pdf --code ICH-E9 --title "..."

Stores status="extracted"; flip to "ready" after a human review pass (admin
PATCH /api/v1/guidelines/{id} {"status":"ready"}). Idempotent per code — re-runs
replace the requirements and re-index the Qdrant collection.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys

# Known pre-loaded guidelines (code → friendly metadata).
_PRESETS = {
    "ICH-E3": {
        "title": "Structure and Content of Clinical Study Reports",
        "version": "E3 (Step 4, 1995)",
        "description": (
            "ICH Harmonised Tripartite Guideline E3 — the structure and content "
            "expected of an integrated full clinical study report (CSR)."
        ),
    },
}


def _default_pdf() -> str:
    # repo-root ICH_E3_Guideline.pdf (one level above backend/)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(os.path.dirname(here), "ICH_E3_Guideline.pdf")


async def _amain(pdf_path: str, code: str, title: str | None, reindex: bool) -> int:
    from app.core.db import get_sessionmaker
    from app.models.artifact import Artifact
    from app.services.compliance.embed_index import drop_guideline_index
    from app.services.compliance.ingest import ingest_guideline
    from app.services.compliance.registry import upsert_guideline
    from app.storage import get_storage

    if not os.path.exists(pdf_path):
        print(f"ERROR: PDF not found: {pdf_path}")
        return 2

    with open(pdf_path, "rb") as fh:
        pdf = fh.read()
    preset = _PRESETS.get(code, {})
    title = title or preset.get("title")

    print(f"Ingesting {code} from {pdf_path} ({len(pdf)} bytes) …")
    if reindex:
        await asyncio.to_thread(drop_guideline_index, code)

    # Upload source PDF to storage (R2/local) as an artifact.
    storage = get_storage()
    key = storage.make_key(project_id="_guidelines", kind="guideline_source", filename=os.path.basename(pdf_path))
    obj = await asyncio.to_thread(storage.put, pdf, key=key, content_type="application/pdf")

    # Heavy work (PDF parse + LLM extraction + embeddings) off the event loop.
    result = await asyncio.to_thread(ingest_guideline, code, pdf)
    print(
        f"  parsed {len(result['sections'])} sections, "
        f"{len(result['requirements'])} requirements, "
        f"indexed {result['indexed_chunks']} chunks → {result['collection']}"
    )

    sm = get_sessionmaker()
    async with sm() as session:
        art = Artifact(
            uri=obj.uri,
            r2_key=obj.key if obj.bucket else None,
            bucket=obj.bucket,
            kind="guideline_source",
            filename=os.path.basename(pdf_path),
            mime="application/pdf",
            size_bytes=len(pdf),
            sha256=hashlib.sha256(pdf).hexdigest(),
        )
        session.add(art)
        await session.flush()
        g = await upsert_guideline(
            session,
            code=code,
            result=result,
            title=title,
            version=preset.get("version"),
            description=preset.get("description"),
            domain="pharma",
            source_artifact_id=art.id,
            status="extracted",
        )
        await session.commit()
        print(f"  saved guideline {g.code} (id={g.id}) status={g.status}")

    # Quick dimension/severity histogram for a sanity check.
    import collections

    dims = collections.Counter(r["dimension"] for r in result["requirements"])
    sevs = collections.Counter(r["severity_default"] for r in result["requirements"])
    print(f"  dimensions: {dict(dims)}")
    print(f"  severities: {dict(sevs)}")
    print("  → review the requirements, then mark the guideline 'ready' to publish it.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Seed a compliance guideline.")
    ap.add_argument("--pdf", default=None, help="Path to the guideline PDF.")
    ap.add_argument("--code", default="ICH-E3", help="Guideline code (e.g. ICH-E3).")
    ap.add_argument("--title", default=None, help="Override the guideline title.")
    ap.add_argument("--reindex", action="store_true", help="Drop the Qdrant collection first.")
    args = ap.parse_args(argv[1:])
    pdf_path = args.pdf or _default_pdf()
    return asyncio.run(_amain(pdf_path, args.code, args.title, args.reindex))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

# Backend

Multi-agent document-reformatting backend: **FastAPI + LangGraph** over **Neon
Postgres** (relational + run history), **Qdrant Cloud** (vectors), **Cloudflare
R2** (object storage), and **Azure OpenAI** (chat + embeddings). The proven
extraction / formatting / style services from `../tests/` are ported verbatim
into `app/services/` (kept sync, run off the event loop via `run_sync`).

## What works now (first pass)
- **Flow 1 — Regenerate** (end-to-end, with HITL diff review + resume).
- **Doc-chat agent** (tool-calling over the ported services).
- **Flow 2 — Style transfer** and **Flow 3 — Domain compliance**: graphs wired
  over the same nodes; happy-path scaffold (hardening is the next pass).
- Projects / documents / versions / runs persisted to Neon; uploads + rendered
  outputs stored (R2 when configured, else local disk); SSE agent timeline.



## Run
```bash
# from repo root; uses the repo .venv
.venv/bin/python -m pip install -r backend/requirements/dev.txt   # base+services+dev

cd backend
../.venv/bin/python -m alembic upgrade head          # create Neon tables
../.venv/bin/python -m app.scripts.smoke_flows       # import-smoke every module
../.venv/bin/python -m app.scripts.e2e_flow1         # in-process Flow 1 happy-path
../.venv/bin/python -m app.scripts.seed_domains      # index pharma corpus → Qdrant (needs embeddings)
../.venv/bin/python -m uvicorn app.main:app --port 8000
# docs at http://127.0.0.1:8000/docs ; health at /healthz
```

## API (prefix `/api/v1`, no auth)
- **Projects**: `POST/GET /projects`, `GET/PATCH /projects/{id}`,
  `/projects/{id}/documents|runs`, `/documents/{doc}/versions`.
- **Documents**: `POST /projects/{id}/uploads`, `GET /artifacts/{id}/download`,
  `GET /documents/{doc}/versions/{v}/{content,styling}.json`.
- **Flows**: `POST /projects/{id}/flows/{regenerate|style|compliance}`,
  `GET /flows/{run_id}`, `GET /flows/{run_id}/stream` (SSE),
  `POST /flows/{run_id}/{resume|export|cancel}`.
- **Chat**: `POST /chat/sessions`, `GET /chat/sessions/{sid}`,
  `POST/GET /chat/sessions/{sid}/messages`.
- **Domains**: `GET /domains`, `POST /domains/{slug}/index`.
- **Utilities** (ported): `POST /extract/{word,pdf}`, `POST /apply/{docx,style-transfer}`,
  `POST /fingerprint/template`, `POST /structure/draft`.

## Layout
```
app/
  core/        config, db (Neon/asyncpg), concurrency (run_sync), events (SSE bus)
  schemas/     document_model.py (ported contract) + api.py (HTTP models)
  services/    PORTED sync engine: extraction, formatting, style, mapping, generation, office, orchestration
  llm/         Azure provider (async) + sync adapters for ported code + router
  vectorstore/ Qdrant + memory + factory          rag/  embedder, chunker, indexer, retriever (BM25 fallback)
  storage/     R2 + local + get_storage()          models/ SQLAlchemy (Neon)
  agents/      state, nodes/*, graphs/{flow1,flow2,flow3}, runner, chat/{agent,tools}
  api/v1/      routes/{projects,documents,flows,chat,domains,utils}, errors
  scripts/     smoke_flows, e2e_flow1, seed_domains
alembic/       migrations
```

## Notes
- HITL uses LangGraph `interrupt_before=["docx_writer"]` with an in-process
  MemorySaver (resume within the running process); durable run history lives in
  Neon (`runs.state`). For cross-restart HITL, swap `get_checkpointer()` in
  `app/agents/graphs/__init__.py` for an `AsyncPostgresSaver` opened in lifespan.
- Two Neon DSNs: SQLAlchemy uses `postgresql+asyncpg://` (SSL via connect_args,
  statement cache off); Alembic uses `postgresql+psycopg://`.

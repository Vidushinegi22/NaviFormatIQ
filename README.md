# Navi FormatiQ

An AI-driven, multi-agent document reformatting workflow. It takes a **template** (the shape you want) and a **draft** (the content you have) and emits a fully reformatted report. The template's styling is preserved, content is mapped into the right sections, gaps are filled from a reference corpus (RAG), and a side-by-side diff is surfaced for human review.

The architecture is domain-agnostic and includes specific flows for Document Generation, Style Transfer, and Compliance Checking.

---

## Architecture overview

- **Frontend**: A React SPA built with Vite and Tailwind CSS. It provides a multi-step wizard UI with a project dashboard, interactive document review, and an embedded chat agent.
- **Backend**: A FastAPI application using LangGraph for multi-agent workflows. It uses Neon Postgres for relational data and run history, Qdrant Cloud for vectors, Cloudflare R2/local disk for artifacts, and Azure OpenAI for LLMs.

---

## User Flow

The platform supports multiple workflows, primarily:

1. **Second Version (Regenerate)**
   - **Upload**: Provide a source document and a template.
   - **Extract**: Analyzes the structure, headings, and formatting.
   - **Review**: The AI proposes section-by-section rewrites. Users can accept, reject, or edit inline (Human-In-The-Loop).
   - **Generate**: Renders the final document applying the reviewed changes.
   - **Chat**: An interactive agent allows users to ask questions grounded in the document context.
   - **Export**: Download the final `.docx` or `.pdf`.

2. **Style Update**
   - Applies the visual formatting (fonts, margins, heading scales) of a template document onto a content document without changing the text.

3. **Compliance Check**
   - Evaluates a document against domain-specific GxP rules and flags missing required sections or format violations.

---

## Quick Start

### 1. Run the Backend

The backend requires Python 3.9+.

```bash
cd backend

# Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt

# Create the SQLite dev database (or run Alembic for Postgres)
python3 -c "from app.core.db import create_all; import asyncio; asyncio.run(create_all())"

# Start the FastAPI server
uvicorn app.main:app --reload --port 8000
```
*API documentation is available at `http://localhost:8000/docs`*

### 2. Run the Frontend

The frontend requires Node.js 18+.

```bash
cd frontend

# Install dependencies
npm install

# Start the Vite development server
npm run dev
```
*Open `http://localhost:5173` to view the application.*

---

## Core API Endpoints

The backend exposes a REST API at `/api/v1` with no auth required for local development.

- **Projects & Documents**
  - `GET /projects` - List recent projects
  - `POST /projects` - Create a new project
  - `PATCH /projects/{id}` - Update project state and wizard progress
  - `POST /projects/{id}/uploads` - Upload source and template documents
  - `GET /documents/{doc}/versions/{v}/content.json` - Get cached extraction results

- **Workflows (LangGraph)**
  - `POST /projects/{id}/flows/{regenerate|style|compliance}` - Start a background pipeline run
  - `GET /flows/{run_id}/stream` - SSE endpoint for streaming agent progress
  - `POST /flows/{run_id}/resume` - Submit HITL review decisions to resume generation

- **Chat**
  - `POST /chat/sessions` - Start a new context-aware chat session
  - `POST /chat/sessions/{sid}/messages` - Send a message to the agent

- **Utilities (Legacy)**
  - `POST /fingerprint/template`, `POST /structure/draft` - Direct extraction services

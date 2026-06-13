# Navi FormatiQ — Frontend

Enterprise-grade React UI for the Navi FormatiQ document processing platform.
This module contains **Page 1 (Workflow Selection Dashboard)** only.

## Stack

- React 18 + TypeScript (strict mode)
- Vite 5 (dev server + build)
- React Router v6
- Tailwind CSS 3 (Deep Teal `#0A5A66` brand palette)
- Inter (Google Fonts)
- Hand-built SVG icon set — no icon library dependency

## Quick start

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

`npm run build` runs `tsc -b` for type-checking and produces a production
bundle in `dist/`.

The dev server proxies `/api` to `http://localhost:8000` (FastAPI backend),
so frontend code can `fetch('/api/extract/word')` etc. without CORS in dev.

## Page 1 — Workflow Selection Dashboard

Route: `/`

Composition:

- `components/layout/TopNavBar.tsx` — sticky Deep Teal navigation
- `components/dashboard/WorkflowCard.tsx` — three responsive selection cards
- `components/dashboard/QuickStats.tsx` — KPI tiles
- `components/dashboard/RecentDocuments.tsx` — recents list with quick-open
- `pages/WorkflowSelectionDashboard.tsx` — page composition

Selecting a workflow:

1. Persists the workflow type in `WorkflowContext` (mirrored to sessionStorage).
2. Navigates to `/upload?workflow=<type>`.
3. The wizard rail on subsequent pages is generated from
   `config/wizardSteps.ts` — adding a workflow there is the only change
   needed for the whole flow to pick it up.

## Folder structure

```
frontend/
├── index.html
├── package.json
├── postcss.config.js
├── tailwind.config.js
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── public/
│   └── favicon.svg
└── src/
    ├── main.tsx
    ├── router.tsx
    ├── index.css
    ├── components/
    │   ├── icons/Icons.tsx
    │   ├── layout/{Logo,TopNavBar}.tsx
    │   └── dashboard/{WorkflowCard,QuickStats,RecentDocuments}.tsx
    ├── config/wizardSteps.ts
    ├── context/WorkflowContext.tsx
    ├── pages/{WorkflowSelectionDashboard,UploadPlaceholder}.tsx
    └── types/workflow.ts
```

## Accessibility

- All interactive surfaces are real `<button>` / `<a>` elements with
  visible focus rings.
- Cards announce both title and description via `aria-label`.
- Notification badge count is surfaced to assistive tech in its label.
- Page uses a single `<h1>` and a hierarchical heading order.

## Next module

- Upload page (drop-zone, validation, progress, API wiring to
  `/extract/word` and `/extract/pdf`).

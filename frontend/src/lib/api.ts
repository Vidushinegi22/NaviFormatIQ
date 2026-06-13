/**
 * Typed HTTP client for the DocuMorph / Navi FormatiQ backend.
 *
 * All paths are mounted under `/api/v1` on the FastAPI app and reach it
 * through the Vite dev proxy (see vite.config.ts). In production the same
 * relative paths work when the API is served behind `/api`.
 */
import type {
  ChatMessageRead,
  ChatSessionRead,
  ChatTurnResponse,
  ComplianceRequest,
  DocumentContent,
  DocumentStyling,
  DomainRead,
  ExportItem,
  ExportManifest,
  Guideline,
  GuidelineDetail,
  GuidelineIngestResult,
  ProjectDetail,
  ProjectRead,
  RegenerateRequest,
  ReviewDecisionItem,
  RunDetail,
  RunStarted,
  StyleInterpretRequest,
  StyleInterpretResponse,
  StyleRequest,
  SuggestionsResponse,
  SuggestRequest,
  UploadRead,
  VersionRead,
} from "@/types/api";

const API_BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
  } catch (e) {
    throw new ApiError(0, `Network error: ${(e as Error).message}`);
  }
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail ?? body?.message ?? detail;
      if (typeof detail !== "string") detail = JSON.stringify(detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ── projects ──────────────────────────────────────────────────────────── */

export function createProject(
  name: string,
  opts?: { flow_hint?: string; meta?: Record<string, unknown> }
): Promise<ProjectRead> {
  return postJson<ProjectRead>("/projects", {
    name,
    flow_hint: opts?.flow_hint,
    meta: opts?.meta ?? {},
  });
}

export function listProjects(limit = 50, offset = 0): Promise<ProjectRead[]> {
  return request<ProjectRead[]>(`/projects?limit=${limit}&offset=${offset}`);
}

export function getProject(projectId: string): Promise<ProjectDetail> {
  return request<ProjectDetail>(`/projects/${projectId}`);
}

export function deleteProject(projectId: string): Promise<void> {
  return request<void>(`/projects/${projectId}`, {
    method: "DELETE",
  });
}

export function updateProject(
  projectId: string,
  body: {
    name?: string;
    status?: string;
    current_step?: string;
    completion?: Record<string, boolean>;
    meta?: Record<string, unknown>;
  }
): Promise<ProjectRead> {
  return request<ProjectRead>(`/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getDocumentVersions(
  projectId: string,
  documentId: string
): Promise<VersionRead[]> {
  return request<VersionRead[]>(
    `/projects/${projectId}/documents/${documentId}/versions`
  );
}

/* ── uploads ───────────────────────────────────────────────────────────── */

export function uploadDocument(
  projectId: string,
  file: File,
  kind = "source"
): Promise<UploadRead> {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  return request<UploadRead>(`/projects/${projectId}/uploads`, {
    method: "POST",
    body: form,
  });
}

/* ── extraction (content + styling JSON) ───────────────────────────────── */

/* ── client-side result caches ─────────────────────────────────────────────
 * Extraction and suggestion results never change for a given document version,
 * so they're memoized for the lifetime of the tab. Navigating back/forward
 * through the wizard (or reopening a step) renders instantly from these
 * instead of re-fetching — and never re-runs extraction. `force` (the
 * "Rerun Extraction" action) bypasses and refreshes the cache.
 */
const contentCache = new Map<string, DocumentContent>();
const stylingCache = new Map<string, DocumentStyling>();
const extractCache = new Map<
  string,
  { content: DocumentContent; styling: DocumentStyling }
>();
const suggestionsCache = new Map<string, SuggestionsResponse>();

export function getContent(
  documentId: string,
  version: number
): Promise<DocumentContent> {
  const key = `${documentId}:${version}`;
  const hit = contentCache.get(key);
  if (hit) return Promise.resolve(hit);
  return request<DocumentContent>(
    `/documents/${documentId}/versions/${version}/content.json`
  ).then((res) => {
    contentCache.set(key, res);
    return res;
  });
}

export function getStyling(
  documentId: string,
  version: number
): Promise<DocumentStyling> {
  const key = `${documentId}:${version}`;
  const hit = stylingCache.get(key);
  if (hit) return Promise.resolve(hit);
  return request<DocumentStyling>(
    `/documents/${documentId}/versions/${version}/styling.json`
  ).then((res) => {
    stylingCache.set(key, res);
    return res;
  });
}

/**
 * Content + styling in a single request. The backend parses the document once
 * and returns both, so the Extract page no longer triggers two concurrent
 * extractions of the same file (which doubled the "Reading document…" wait).
 */
export function getExtract(
  documentId: string,
  version: number,
  force = false
): Promise<{ content: DocumentContent; styling: DocumentStyling }> {
  const key = `${documentId}:${version}`;
  if (!force) {
    const hit = extractCache.get(key);
    if (hit) return Promise.resolve(hit);
  }
  const qs = force ? "?force=true" : "";
  return request<{ content: DocumentContent; styling: DocumentStyling }>(
    `/documents/${documentId}/versions/${version}/extract.json${qs}`
  ).then((res) => {
    extractCache.set(key, res);
    contentCache.set(key, res.content);
    stylingCache.set(key, res.styling);
    return res;
  });
}

/* ── flows ─────────────────────────────────────────────────────────────── */

export function startRegenerate(
  projectId: string,
  body: RegenerateRequest
): Promise<RunStarted> {
  return postJson<RunStarted>(`/projects/${projectId}/flows/regenerate`, body);
}

export function startStyle(
  projectId: string,
  body: StyleRequest
): Promise<RunStarted> {
  return postJson<RunStarted>(`/projects/${projectId}/flows/style`, body);
}

/**
 * Smart, domain-aware change suggestions for the new version (pre-run, Revise
 * step). Substantive content-level ideas grounded in the document + its prior
 * versions — never grammar/spelling. Returns an empty list when unavailable.
 */
export function getRevisionSuggestions(
  projectId: string,
  body: SuggestRequest
): Promise<SuggestionsResponse> {
  // LLM-generated; cache per (project, draft, context) so revisiting the
  // Review step doesn't re-run the suggestion model.
  const key = `${projectId}:${body.draft_document_id}:${(body.context_document_ids ?? []).join(",")}`;
  const hit = suggestionsCache.get(key);
  if (hit) return Promise.resolve(hit);
  return postJson<SuggestionsResponse>(
    `/projects/${projectId}/flows/regenerate/suggest`,
    body
  ).then((res) => {
    suggestionsCache.set(key, res);
    return res;
  });
}

/**
 * Preview how the uploaded style template will be interpreted — whether it's a
 * formatting guideline (rules are read and extracted) or an example document
 * (its own look is copied), plus the extracted rules for a guideline. Used by
 * the Style page to show a detection card and let the user override the mode.
 */
export function interpretStyleSource(
  projectId: string,
  body: StyleInterpretRequest
): Promise<StyleInterpretResponse> {
  return postJson<StyleInterpretResponse>(
    `/projects/${projectId}/style/interpret`,
    body
  );
}

export function startCompliance(
  projectId: string,
  body: ComplianceRequest
): Promise<RunStarted> {
  return postJson<RunStarted>(`/projects/${projectId}/flows/compliance`, body);
}

/** Filterable compliance findings for a run (drill-down / chat). */
export function getFindings(
  runId: string,
  filters?: { dimension?: string; severity?: string; status?: string; section?: string }
): Promise<{ findings: import("@/types/api").ComplianceFinding[]; count: number }> {
  const q = new URLSearchParams(
    Object.entries(filters ?? {}).filter(([, v]) => v) as [string, string][]
  ).toString();
  return request(`/flows/${runId}/findings${q ? `?${q}` : ""}`);
}

export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/flows/${runId}`);
}

export function resumeRun(
  runId: string,
  decisions: ReviewDecisionItem[]
): Promise<RunStarted> {
  return postJson<RunStarted>(`/flows/${runId}/resume`, { decisions });
}

export function exportRun(runId: string): Promise<{
  rendered_docx_uri: string | null;
  rendered_pdf_uri: string | null;
  artifacts: string[];
}> {
  return postJson(`/flows/${runId}/export`, {});
}

export function cancelRun(runId: string): Promise<{ ok: boolean }> {
  return postJson(`/flows/${runId}/cancel`, {});
}

/** Absolute URL for the run's Server-Sent-Events stream (use with EventSource). */
export function runStreamUrl(runId: string): string {
  return `${API_BASE}/flows/${runId}/stream`;
}

/* ── artifacts / downloads ─────────────────────────────────────────────── */

/**
 * Download an artifact to the user's machine. Tries a presigned URL first
 * (object storage); falls back to streaming the bytes through the API.
 */
export async function downloadArtifact(
  artifactId: string,
  filename?: string
): Promise<void> {
  // Pass the desired filename so it survives the download: presigned (cross-
  // origin) URLs ignore the <a download> attribute and would otherwise save
  // the file under the storage object key (random text).
  const params = new URLSearchParams({ presign: "true" });
  if (filename) params.set("filename", filename);
  // Try presign — returns { url } as JSON when storage supports it.
  try {
    const res = await fetch(
      `${API_BASE}/artifacts/${artifactId}/download?${params.toString()}`
    );
    const ct = res.headers.get("content-type") ?? "";
    if (res.ok && ct.includes("application/json")) {
      const { url } = (await res.json()) as { url?: string };
      if (url) {
        triggerDownload(url, filename);
        return;
      }
    }
    if (res.ok) {
      // Streamed bytes — turn into a blob download.
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      triggerDownload(objUrl, filename);
      setTimeout(() => URL.revokeObjectURL(objUrl), 10_000);
      return;
    }
    throw new ApiError(res.status, `Download failed (${res.status})`);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, `Download error: ${(e as Error).message}`);
  }
}

function triggerDownload(url: string, filename?: string) {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.rel = "noopener";
  a.target = "_blank";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ── exports (download hub) ─────────────────────────────────────────────── */

/** List every deliverable the backend can produce for a finished run. */
export function getExports(runId: string): Promise<ExportManifest> {
  return request<ExportManifest>(`/flows/${runId}/exports`);
}

/**
 * Download one export. Deliverables backed by a stored artifact use the
 * presign-capable artifact path; the rest are generated + streamed on demand.
 */
export async function downloadExport(
  runId: string,
  item: ExportItem
): Promise<void> {
  if (item.artifact_id) {
    return downloadArtifact(item.artifact_id, item.filename);
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/flows/${runId}/exports/${item.id}`, {
      headers: { Accept: "*/*" },
    });
  } catch (e) {
    throw new ApiError(0, `Download error: ${(e as Error).message}`);
  }
  if (!res.ok) {
    let detail = `Download failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  triggerDownload(objUrl, item.filename);
  setTimeout(() => URL.revokeObjectURL(objUrl), 10_000);
}

/* ── chat ──────────────────────────────────────────────────────────────── */

export function createChatSession(body: {
  project_id?: string;
  title?: string;
  subject_document_id?: string;
  subject_run_id?: string;
  guideline_id?: string;
}): Promise<ChatSessionRead> {
  return postJson<ChatSessionRead>("/chat/sessions", {
    title: "Chat",
    ...body,
  });
}

export function getChatSession(sessionId: string): Promise<{
  session: ChatSessionRead;
  messages: ChatMessageRead[];
}> {
  return request(`/chat/sessions/${sessionId}`);
}

export function postChatMessage(
  sessionId: string,
  body: {
    message: string;
    subject_document_id?: string;
    subject_artifact_id?: string;
    subject_run_id?: string;
    guideline_id?: string;
  }
): Promise<ChatTurnResponse> {
  return postJson<ChatTurnResponse>(
    `/chat/sessions/${sessionId}/messages`,
    body
  );
}

/* ── domains + guidelines ──────────────────────────────────────────────── */

export function listDomains(): Promise<DomainRead[]> {
  return request<DomainRead[]>("/domains");
}

/** Pre-loaded compliance guidelines (published/ready) for a domain. */
export function listGuidelines(domain?: string): Promise<Guideline[]> {
  const q = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  return request<Guideline[]>(`/guidelines${q}`);
}

export function getGuideline(id: string): Promise<GuidelineDetail> {
  return request<GuidelineDetail>(`/guidelines/${id}`);
}

/**
 * Manually ingest a guideline PDF. Parses the PDF and extracts its requirement
 * tree (one or two minutes of LLM work). `publish` defaults to true so the new
 * guideline appears in the selector right away.
 */
export function uploadGuideline(
  file: File,
  opts: { code: string; title?: string; domain?: string; publish?: boolean }
): Promise<GuidelineIngestResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("code", opts.code);
  if (opts.title) form.append("title", opts.title);
  form.append("domain", opts.domain ?? "pharma");
  form.append("publish", String(opts.publish ?? true));
  return request<GuidelineIngestResult>("/guidelines", {
    method: "POST",
    body: form,
  });
}

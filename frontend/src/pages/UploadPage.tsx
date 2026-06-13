import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import { useNavigate } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import {
  CloudUploadIcon,
  FileTextIcon,
  XCircleIcon,
  TrashIcon,
  InfoIcon,
  RefreshIcon,
  PlusCircleIcon,
} from "@/components/icons/Icons";
import { useDocument, type DocRef } from "@/context/DocumentContext";
import { useWorkflow } from "@/context/WorkflowContext";
import { formatBytes } from "@/lib/format";
import { pathForStep } from "@/lib/stepRouting";
import {
  createProject,
  uploadDocument,
  listDomains,
  listGuidelines,
  uploadGuideline,
  ApiError,
} from "@/lib/api";
import { ClipboardListIcon } from "@/components/icons/Icons";
import type { FileKind } from "@/types/workflow";
import type { DomainRead, Guideline } from "@/types/api";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];
const ACCEPTED_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
];
const MAX_BYTES = 100 * 1024 * 1024; // matches backend MAX_UPLOAD_MB

/** A file that has been uploaded or is uploading. */
interface UploadedFile {
  id: string;
  /** Raw bytes to upload; null for entries restored from a saved project. */
  file: File | null;
  /** Display fields — kept independent of `file` so restored entries render. */
  name: string;
  size: number;
  kind: FileKind;
  ref: DocRef | null; // null while uploading
  version: string;
  uploading: boolean;
  error: string | null;
}

function validate(raw: File): string | null {
  const ext = raw.name.toLowerCase().slice(raw.name.lastIndexOf("."));
  const isExtOk = ACCEPTED_EXTENSIONS.includes(ext);
  const isMimeOk = ACCEPTED_MIME.includes(raw.type) || raw.type === "";
  if (!isExtOk || !isMimeOk) return "Only PDF and DOCX files are supported.";
  if (raw.size > MAX_BYTES) return `File exceeds the ${formatBytes(MAX_BYTES)} limit.`;
  return null;
}

function kindFor(raw: File): FileKind {
  return raw.name.toLowerCase().endsWith(".pdf") ? "pdf" : "docx";
}

/** Parse a user-entered version label ("9", "v2", "Version 3") to a number. */
function versionNum(label: string): number {
  const m = String(label).match(/\d+/);
  const n = m ? parseInt(m[0], 10) : 0;
  return Number.isFinite(n) && n > 0 ? n : 0;
}

let nextId = 1;

/**
 * Page 1 — Upload.
 *
 * Supports multiple file uploads. Each file can be tagged with a document
 * version (1, 2, 3, etc.). Files are uploaded to the backend and their
 * artifact IDs stored in context.
 *
 * The file marked as "Version 1" is set as the primary document in context.
 */
export function UploadPage() {
  const navigate = useNavigate();
  const { selectedWorkflow } = useWorkflow();
  const {
    project,
    source,
    secondary,
    domainId,
    guidelineId,
    targetVersion,
    setProject,
    setSource,
    setSecondary,
    setContextDocs,
    setTargetVersion,
    setRunId,
    setDomainId,
    setGuidelineId,
    markComplete,
    setCurrentStep,
    reset,
  } = useDocument();

  const isCompliance = selectedWorkflow === "compliance-check";
  const isSecondVersion = selectedWorkflow === "second-version";

  // Track current step for resume-on-reopen
  useEffect(() => {
    setCurrentStep("upload");
  }, [setCurrentStep]);

  // Compliance: load domains + pre-loaded guidelines for the selector.
  const [domains, setDomains] = useState<DomainRead[]>([]);
  const [guidelines, setGuidelines] = useState<Guideline[]>([]);
  useEffect(() => {
    if (!isCompliance) return;
    listDomains().then(setDomains).catch(() => {});
    if (!domainId) setDomainId("pharma");
  }, [isCompliance, domainId, setDomainId]);
  useEffect(() => {
    if (!isCompliance || !domainId) return;
    listGuidelines(domainId)
      .then((gs) => {
        setGuidelines(gs);
        if (gs.length && !guidelineId) {
          const e3 = gs.find((g) => g.code === "ICH-E3");
          setGuidelineId((e3 ?? gs[0]).id);
        }
      })
      .catch(() => {});
  }, [isCompliance, domainId, guidelineId, setGuidelineId]);

  // Refresh the selector after a manual guideline upload and select the new one.
  const handleGuidelineUploaded = useCallback(
    async (newId: string) => {
      if (!domainId) return;
      try {
        const gs = await listGuidelines(domainId);
        setGuidelines(gs);
        setGuidelineId(newId);
      } catch {
        /* ignore — the upload itself surfaced any error */
      }
    },
    [domainId, setGuidelineId]
  );

  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);

  // Re-populate the file list when resuming a saved project: the documents
  // were uploaded in a previous session, so show them (with their server
  // refs) instead of an empty drop zone. Runs once, only when no local files
  // exist yet — fresh uploads set `source` themselves and must not re-seed.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current || files.length > 0) return;
    if (!source && !secondary) return;
    const seeded: UploadedFile[] = [];
    if (source) {
      seeded.push({
        id: `file-${nextId++}`,
        file: null,
        name: source.filename,
        size: source.size ?? 0,
        kind: source.kind,
        ref: source,
        version: "1",
        uploading: false,
        error: null,
      });
    }
    if (secondary) {
      seeded.push({
        id: `file-${nextId++}`,
        file: null,
        name: secondary.filename,
        size: secondary.size ?? 0,
        kind: secondary.kind,
        ref: secondary,
        version: "2",
        uploading: false,
        error: null,
      });
    }
    if (seeded.length > 0) {
      setFiles(seeded);
      hydratedRef.current = true;
    }
  }, [source, secondary, files.length]);

  // Generate-New-Version: derive the base document, context versions and target
  // version from ALL uploads. The highest-numbered version is the one we
  // revise; the rest are read-only context (what changed across revisions); the
  // new document is numbered max(version) + 1. Re-runs whenever a file is added,
  // removed, or has its version edited.
  useEffect(() => {
    if (!isSecondVersion) return;
    const uploaded = files.filter((f) => f.ref != null);
    if (uploaded.length === 0) return; // removeFile clears state when emptied
    const ranked = [...uploaded].sort(
      (a, b) => versionNum(b.version) - versionNum(a.version)
    );
    const base = ranked[0];
    const maxV = versionNum(base.version);
    setSource({ ...(base.ref as DocRef), versionLabel: base.version });
    setContextDocs(
      ranked.slice(1).map((f) => ({ ...(f.ref as DocRef), versionLabel: f.version }))
    );
    setTargetVersion(maxV > 0 ? maxV + 1 : null);
    markComplete("upload");
    // A fresh upload supersedes any earlier run for this project.
    if (uploaded.some((f) => f.file != null)) setRunId(null);
  }, [
    files,
    isSecondVersion,
    setSource,
    setContextDocs,
    setTargetVersion,
    markComplete,
    setRunId,
  ]);

  // Create the project once, lazily, and reuse it for all uploads. When several
  // files are dropped at once, each schedules its own uploadFile → ensureProject;
  // guarding on `project` alone races because setProject is async, so the first
  // caller to miss the guard caches its in-flight promise and the rest await it.
  // A failed creation clears the ref so it can be retried instead of caching the
  // rejection forever.
  const projectPromiseRef = useRef<Promise<{ id: string; name: string }> | null>(null);
  const ensureProject = useCallback(async (): Promise<{ id: string; name: string }> => {
    if (project) return project;
    if (!projectPromiseRef.current) {
      projectPromiseRef.current = (async () => {
        const name = `Document workflow · ${new Date().toLocaleDateString()}`;
        const p = await createProject(name, { flow_hint: selectedWorkflow ?? undefined });
        const created = { id: p.id, name: p.name };
        setProject(created);
        return created;
      })().catch((e) => {
        projectPromiseRef.current = null;
        throw e;
      });
    }
    return projectPromiseRef.current;
  }, [project, selectedWorkflow, setProject]);

  const uploadFile = useCallback(
    async (entry: UploadedFile) => {
      if (!entry.file) return; // restored entry — already on the backend
      // Mark as uploading
      setFiles((prev) =>
        prev.map((f) =>
          f.id === entry.id ? { ...f, uploading: true, error: null } : f
        )
      );

      try {
        const proj = await ensureProject();
        const vTrim = entry.version.trim().toLowerCase();
        const isV1 = vTrim === "1" || vTrim === "v1" || vTrim === "version 1";
        const isV2 = vTrim === "2" || vTrim === "v2" || vTrim === "version 2";
        const backendKind = isV2 ? "style" : "source";

        const up = await uploadDocument(proj.id, entry.file, backendKind);
        const ref: DocRef = {
          documentId: up.document_id,
          artifactId: up.artifact_id,
          version: up.version,
          filename: up.filename,
          kind: entry.kind,
          size: entry.size,
          uploadedAt: new Date().toISOString(),
        };

        setFiles((prev) =>
          prev.map((f) =>
            f.id === entry.id ? { ...f, ref, uploading: false, error: null } : f
          )
        );

        // Assign roles. The Generate-New-Version flow has no primary/secondary
        // split — its source, context versions and target version are derived
        // from all uploads together (see the effect below), so skip per-file
        // role assignment here.
        if (!isSecondVersion) {
          if (isV1) {
            setSource(ref);
            setRunId(null);
            markComplete("upload");
          } else if (isV2) {
            setSecondary(ref);
          }
        }
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? `Upload failed: ${e.message}`
            : `Upload failed: ${(e as Error).message}`;
        setFiles((prev) =>
          prev.map((f) =>
            f.id === entry.id ? { ...f, uploading: false, error: msg } : f
          )
        );
      }
    },
    [ensureProject, isSecondVersion, markComplete, setRunId, setSecondary, setSource]
  );

  const handleFiles = useCallback(
    (rawFiles: FileList | File[]) => {
      setGlobalError(null);
      const fileArray = Array.from(rawFiles);

      setFiles((prev) => {
        const nextEntries = [...prev];
        let currentLength = prev.length;

        for (const raw of fileArray) {
          const v = validate(raw);
          if (v) {
            setGlobalError(v);
            continue;
          }

          const entry: UploadedFile = {
            id: `file-${nextId++}`,
            file: raw,
            name: raw.name,
            size: raw.size,
            kind: kindFor(raw),
            ref: null,
            version: String(currentLength + 1),
            uploading: false,
            error: null,
          };
          nextEntries.push(entry);
          currentLength++;
          // Trigger upload outside state setter
          setTimeout(() => uploadFile(entry), 0);
        }
        return nextEntries;
      });
    },
    [uploadFile]
  );

  const removeFile = (id: string) => {
    const file = files.find((f) => f.id === id);
    setFiles((prev) => prev.filter((f) => f.id !== id));
    if (isSecondVersion) {
      // The derive-effect re-ranks the remaining uploads; only the "removed the
      // last file" case needs explicit clearing (the effect no-ops when empty).
      const remaining = files.filter((f) => f.id !== id && f.ref != null);
      if (remaining.length === 0) {
        setSource(null);
        setContextDocs([]);
        setTargetVersion(null);
        setRunId(null);
      }
      return;
    }
    // Clear context if removing
    if (file?.ref) {
      const vTrim = file.version.trim().toLowerCase();
      const isV1 = vTrim === "1" || vTrim === "v1" || vTrim === "version 1";
      const isV2 = vTrim === "2" || vTrim === "v2" || vTrim === "version 2";
      if (isV1 && source?.documentId === file.ref.documentId) {
        setSource(null);
        setRunId(null);
      }
      if (isV2 && secondary?.documentId === file.ref.documentId) {
        setSecondary(null);
      }
    }
  };

  const updateVersion = (id: string, version: string) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === id ? { ...f, version } : f))
    );
    // Generate-New-Version re-ranks via the derive-effect on this files change.
    if (isSecondVersion) return;
    // Re-assign context refs based on new version
    const file = files.find((f) => f.id === id);
    if (file?.ref) {
      const vTrim = version.trim().toLowerCase();
      const isV1 = vTrim === "1" || vTrim === "v1" || vTrim === "version 1";
      const isV2 = vTrim === "2" || vTrim === "v2" || vTrim === "version 2";
      if (isV1) {
        setSource(file.ref);
        markComplete("upload");
      } else if (isV2) {
        setSecondary(file.ref);
      }
    }
  };

  const onSelect = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) handleFiles(e.target.files);
    if (inputRef.current) inputRef.current.value = "";
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files?.length) handleFiles(e.dataTransfer.files);
  };

  const hasSource = files.some((f) => {
    const v = f.version.trim().toLowerCase();
    return (v === "1" || v === "v1" || v === "version 1") && f.ref != null;
  });
  const hasTemplate = files.some((f) => {
    const v = f.version.trim().toLowerCase();
    return (v === "2" || v === "v2" || v === "version 2") && f.ref != null;
  });
  const anyUploading = files.some((f) => f.uploading);
  const uploadedCount = files.filter((f) => f.ref != null).length;
  // Highest version among uploaded files — that file is the one we revise.
  const maxVersionNum = Math.max(
    0,
    ...files.filter((f) => f.ref != null).map((f) => versionNum(f.version))
  );

  return (
    <WizardLayout
      activeKey="upload"
      title="Upload Documents"
      subtitle={
        selectedWorkflow === "style-update"
          ? "Upload your Target Document (Raw) and your Style Template for formatting."
          : isCompliance
            ? "Upload the document to audit and choose the guideline to check it against."
            : "Upload the version you want to revise. Add older versions too (optional) — they're used as context for what changed."
      }
      primaryAction={{
        label: "Extract",
        onClick: () => navigate(pathForStep("extract")),
        disabled: isSecondVersion
          ? uploadedCount === 0 || anyUploading
          : !hasSource ||
            (selectedWorkflow === "style-update" && !hasTemplate) ||
            (isCompliance && !guidelineId) ||
            anyUploading,
      }}
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* Left: Upload area */}
        <div className="space-y-5 lg:col-span-3">
          {/* Drop zone */}
          <div className="nf-card overflow-hidden">
            <div
              onDrop={onDrop}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              className={`flex flex-col items-center justify-center gap-3 border-2 border-dashed p-10 text-center transition-colors ${
                dragging
                  ? "border-brand-400 bg-brand-50/60"
                  : "border-ink-200 bg-white"
              }`}
              role="region"
              aria-label="Document upload drop zone"
            >
              <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-50 text-brand-500">
                <CloudUploadIcon className="h-7 w-7" />
              </span>
              <h2 className="text-lg font-bold tracking-tight text-ink-800">
                Drop your documents here
              </h2>
              <p className="max-w-md text-sm text-ink-500">
                PDF or DOCX, up to {formatBytes(MAX_BYTES)} per file. You can upload multiple files.
              </p>
              <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
                <button
                  type="button"
                  onClick={() => inputRef.current?.click()}
                  disabled={anyUploading}
                  className="nf-btn-primary"
                >
                  <CloudUploadIcon className="h-4 w-4" />
                  Browse Files
                </button>
              </div>
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(",")}
                onChange={onSelect}
                className="sr-only"
                aria-label="Choose files"
                multiple
              />
            </div>
          </div>

          {/* Add more files button */}
          {files.length > 0 && (
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              disabled={anyUploading}
              className="flex items-center gap-2 text-sm font-semibold text-brand-500 transition-colors hover:text-brand-700"
            >
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-500 text-white">
                <PlusCircleIcon className="h-4 w-4" />
              </span>
              Add More Files
            </button>
          )}

          {globalError && (
            <p
              role="alert"
              className="inline-flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-700"
            >
              <XCircleIcon className="h-4 w-4" />
              {globalError}
            </p>
          )}
        </div>

        {/* Right: Files Added panel — version selector lives here */}
        <aside className="space-y-5 lg:col-span-2">
          <section className="nf-card overflow-hidden">
            <header className="border-b border-ink-100 px-5 py-4">
              <h3 className="text-base font-bold text-ink-800">Files Added</h3>
              <p className="mt-0.5 text-xs text-ink-500">
                {selectedWorkflow === "style-update"
                  ? "Label the raw target and the style template — the template can be an example document to copy, or a formatting guideline that describes the rules"
                  : isSecondVersion
                    ? "Tag each file with its version number. The highest is revised; the rest add context."
                    : "Specify the document version for each uploaded file"}
              </p>
            </header>

            {files.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 px-5 py-12 text-center">
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-ink-100 text-ink-400">
                  <FileTextIcon className="h-5 w-5" />
                </span>
                <p className="text-sm font-medium text-ink-600">No files uploaded yet</p>
                <p className="text-xs text-ink-500">
                  Upload documents to see them here
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-ink-100">
                {files.map((f) => (
                  <li key={f.id} className="px-5 py-4">
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-500">
                        {f.uploading ? (
                          <RefreshIcon className="h-4 w-4 animate-spin" />
                        ) : (
                          <FileTextIcon className="h-4 w-4" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-ink-800">
                          {f.name}
                        </p>
                        <div className="flex flex-wrap items-center gap-2 mt-0.5">
                          <span className="text-xs text-ink-500">
                            {f.kind.toUpperCase()} · {formatBytes(f.size)}
                          </span>
                          {f.ref && (
                            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700">
                              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />
                              Uploaded
                            </span>
                          )}
                          {f.uploading && (
                            <span className="text-xs text-brand-500 font-medium">Uploading…</span>
                          )}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeFile(f.id)}
                        disabled={f.uploading}
                        className="shrink-0 text-ink-400 hover:text-rose-500 transition-colors disabled:opacity-40"
                        aria-label={`Remove ${f.name}`}
                      >
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    </div>

                    {f.error && (
                      <p className="mt-2 pl-12 inline-flex items-center gap-1 text-xs text-rose-600 font-medium">
                        <XCircleIcon className="h-3.5 w-3.5" />
                        {f.error}
                      </p>
                    )}

                    {/* Version selector - only show if uploaded successfully */}
                    {f.ref && (
                      <div className="mt-3 pl-12">
                        <label className="block text-[11px] font-semibold uppercase tracking-wider text-ink-500 mb-1">
                          {selectedWorkflow === "style-update" ? "File Type" : "Document Version"}
                        </label>
                        {selectedWorkflow === "style-update" ? (
                          <div className="flex gap-2 mt-1.5 flex-wrap">
                            <label
                              className={`flex items-center justify-center px-3 py-1.5 rounded-md border text-xs font-semibold cursor-pointer transition-colors ${
                                f.version === "1"
                                  ? "bg-brand-50 border-brand-300 text-brand-700 shadow-sm"
                                  : "bg-white border-ink-200 text-ink-600 hover:bg-ink-50"
                              }`}
                            >
                              <input
                                type="radio"
                                name={`version-${f.id}`}
                                value="1"
                                checked={f.version === "1"}
                                onChange={() => updateVersion(f.id, "1")}
                                className="sr-only"
                              />
                              Target File (Raw)
                            </label>
                            <label
                              className={`flex items-center justify-center px-3 py-1.5 rounded-md border text-xs font-semibold cursor-pointer transition-colors ${
                                f.version === "2"
                                  ? "bg-brand-50 border-brand-300 text-brand-700 shadow-sm"
                                  : "bg-white border-ink-200 text-ink-600 hover:bg-ink-50"
                              }`}
                            >
                              <input
                                type="radio"
                                name={`version-${f.id}`}
                                value="2"
                                checked={f.version === "2"}
                                onChange={() => updateVersion(f.id, "2")}
                                className="sr-only"
                              />
                              Style Template
                            </label>
                          </div>
                        ) : null}
                        {selectedWorkflow === "style-update" && f.version === "2" && (
                          <p className="mt-1.5 text-[11px] leading-snug text-ink-400">
                            Can be an example document (its look is copied) or a
                            formatting guideline (its rules are read and applied).
                            We auto-detect which on the next step.
                          </p>
                        )}
                        {selectedWorkflow !== "style-update" && (
                          <div className="max-w-[220px]">
                            <input
                              type="text"
                              value={f.version}
                              onChange={(e) => updateVersion(f.id, e.target.value)}
                              placeholder="e.g. 1, 2, 3"
                              className="w-full rounded-lg border border-ink-200 bg-white py-1.5 px-3 text-sm font-medium text-ink-700 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                            />
                            {isSecondVersion && maxVersionNum > 0 && (
                              <p className="mt-1.5 text-[11px] leading-snug text-ink-400">
                                {versionNum(f.version) === maxVersionNum ? (
                                  <>
                                    Latest version — this is the one we'll revise
                                    {targetVersion != null
                                      ? `. New document will be version ${targetVersion}.`
                                      : "."}
                                  </>
                                ) : (
                                  "Earlier version — used as context only."
                                )}
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Compliance: domain + pre-loaded guideline selector */}
          {isCompliance && (
            <ComplianceTargetCard
              domains={domains}
              guidelines={guidelines}
              domainId={domainId ?? "pharma"}
              guidelineId={guidelineId}
              onDomain={setDomainId}
              onGuideline={setGuidelineId}
              onUploaded={handleGuidelineUploaded}
            />
          )}

          {/* Status info */}
          <section className="nf-card p-5">
            <header className="mb-3">
              <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">
                Upload Status
              </p>
            </header>
            <ul className="space-y-2.5">
              {isSecondVersion ? (
                <>
                  <StatusRow
                    ok={uploadedCount > 0}
                    label="Document"
                    detail={
                      uploadedCount > 0
                        ? `${uploadedCount} version${uploadedCount === 1 ? "" : "s"} uploaded`
                        : "Upload the version to revise"
                    }
                  />
                  <StatusRow
                    ok={targetVersion != null}
                    label="New version"
                    detail={
                      targetVersion != null
                        ? `Will be generated as version ${targetVersion}`
                        : "Tag each file with a version number"
                    }
                  />
                </>
              ) : (
                <>
                  <StatusRow
                    ok={hasSource}
                    label={selectedWorkflow === "style-update" ? "Target File (Raw)" : "Document"}
                    detail={
                      hasSource
                        ? files.find((f) => {
                            const v = f.version.trim().toLowerCase();
                            return v === "1" || v === "v1" || v === "version 1";
                          })?.name ?? "Uploaded"
                        : selectedWorkflow === "style-update"
                          ? "Label a file as Target File"
                          : "Upload a document"
                    }
                  />
                  {selectedWorkflow === "style-update" && (
                    <StatusRow
                      ok={hasTemplate}
                      label="Style Template"
                      detail={
                        hasTemplate
                          ? files.find((f) => {
                              const v = f.version.trim().toLowerCase();
                              return v === "2" || v === "v2" || v === "version 2";
                            })?.name ?? "Uploaded"
                          : "Label the template file as Style Template"
                      }
                    />
                  )}
                  {isCompliance && (
                    <StatusRow
                      ok={!!guidelineId}
                      label="Guideline"
                      detail={
                        guidelineId
                          ? guidelines.find((g) => g.id === guidelineId)?.code ?? "Selected"
                          : "Pick a guideline to audit against"
                      }
                    />
                  )}
                </>
              )}
              <StatusRow
                ok={!!project}
                label="Workspace"
                detail={project ? "Project created" : "Created on first upload"}
              />
            </ul>
            <p className="mt-3 inline-flex items-start gap-1.5 text-[11px] text-ink-500">
              <InfoIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Accepted: PDF, DOCX · up to {formatBytes(MAX_BYTES)} per file.
            </p>
          </section>

          {files.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setFiles([]);
                projectPromiseRef.current = null;
                reset();
              }}
              className="nf-btn-ghost w-full justify-center text-rose-600 hover:bg-rose-50 hover:text-rose-700"
            >
              <TrashIcon className="h-4 w-4" />
              Start over
            </button>
          )}
        </aside>
      </div>
    </WizardLayout>
  );
}

/* ── Status row ─────────────────────────────────────────────────────────── */

function StatusRow({
  ok,
  label,
  detail,
}: {
  ok: boolean;
  label: string;
  detail: string;
}) {
  return (
    <li className="flex items-start gap-3 rounded-lg bg-ink-50/60 px-3 py-2">
      <span
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
          ok ? "bg-emerald-100" : "bg-ink-200"
        }`}
      >
        <span
          className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-600" : "bg-ink-400"}`}
          aria-hidden
        />
      </span>
      <div className="min-w-0">
        <p className="text-[13px] font-semibold text-ink-800">{label}</p>
        <p className="truncate text-[12px] text-ink-500">{detail}</p>
      </div>
    </li>
  );
}

/* ── Compliance: domain + pre-loaded guideline selector ─────────────────── */

function ComplianceTargetCard({
  domains,
  guidelines,
  domainId,
  guidelineId,
  onDomain,
  onGuideline,
  onUploaded,
}: {
  domains: DomainRead[];
  guidelines: Guideline[];
  domainId: string;
  guidelineId: string | null;
  onDomain: (id: string) => void;
  onGuideline: (id: string) => void;
  onUploaded: (id: string) => void;
}) {
  const selected = guidelines.find((g) => g.id === guidelineId) ?? null;
  const selectCls =
    "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-800 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100";
  const labelCls = "mb-1.5 block text-[12px] font-semibold uppercase tracking-wider text-ink-500";
  return (
    <section className="nf-card p-5">
      <header className="mb-4 flex items-center gap-2">
        <ClipboardListIcon className="h-4 w-4 text-brand-500" />
        <h3 className="text-sm font-bold text-ink-800">Audit target</h3>
      </header>

      <label htmlFor="cmp-domain" className={labelCls}>Domain</label>
      <select id="cmp-domain" value={domainId} onChange={(e) => onDomain(e.target.value)} className={selectCls}>
        {domains.length === 0 && <option value="pharma">Pharma</option>}
        {domains.map((d) => (
          <option key={d.slug} value={d.slug}>{d.name}</option>
        ))}
      </select>

      <label htmlFor="cmp-guideline" className={`${labelCls} mt-4`}>Guideline</label>
      <select
        id="cmp-guideline"
        value={guidelineId ?? ""}
        onChange={(e) => onGuideline(e.target.value)}
        className={selectCls}
        disabled={guidelines.length === 0}
      >
        {guidelines.length === 0 && <option value="">No guidelines available</option>}
        {guidelines.map((g) => (
          <option key={g.id} value={g.id}>
            {g.code} — {g.title}
          </option>
        ))}
      </select>

      {selected && (
        <div className="mt-3 space-y-2">
          {selected.version && (
            <p className="text-[12px] text-ink-500">{selected.version}</p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {(selected.dimension_coverage ?? []).map((d) => (
              <span
                key={d}
                className="rounded-full bg-ink-50 px-2 py-0.5 text-[10px] font-semibold capitalize text-ink-600 ring-1 ring-inset ring-ink-100"
              >
                {d}
              </span>
            ))}
            {selected.requirement_count != null && (
              <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[10px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-100">
                {selected.requirement_count} requirements
              </span>
            )}
          </div>
        </div>
      )}

      <GuidelineUploader
        domainId={domainId}
        empty={guidelines.length === 0}
        onUploaded={onUploaded}
      />
    </section>
  );
}

/* ── Compliance: manual guideline upload ─────────────────────────────────── */

function GuidelineUploader({
  domainId,
  empty,
  onUploaded,
}: {
  domainId: string;
  empty: boolean;
  onUploaded: (id: string) => void;
}) {
  const [open, setOpen] = useState(empty);
  const [file, setFile] = useState<File | null>(null);
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const inputCls =
    "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-800 shadow-sm placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 disabled:bg-ink-50 disabled:text-ink-400";

  function pick(f: File | null) {
    if (!f) return;
    if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") {
      setError("Guidelines must be PDF files.");
      return;
    }
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.pdf$/i, ""));
    if (!code) {
      const m = f.name.toUpperCase().match(/ICH[ _-]?[A-Z]?\d+[A-Z]?/);
      if (m) setCode(m[0].replace(/[ _]/g, "-"));
    }
    setError(null);
  }

  async function submit() {
    if (busy) return;
    if (!file) return setError("Choose a PDF to upload.");
    if (!code.trim()) return setError("Enter a short guideline code (e.g. ICH-E3).");
    setBusy(true);
    setError(null);
    try {
      const res = await uploadGuideline(file, {
        code: code.trim(),
        title: title.trim() || undefined,
        domain: domainId,
        publish: true,
      });
      setFile(null);
      setCode("");
      setTitle("");
      if (fileRef.current) fileRef.current.value = "";
      setOpen(false);
      onUploaded(res.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message || "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-ink-200 bg-ink-50/40 px-3 py-2.5 text-[12.5px] font-semibold text-ink-600 transition-colors hover:border-brand-300 hover:bg-brand-50/40 hover:text-brand-700"
      >
        <PlusCircleIcon className="h-4 w-4" />
        Add a guideline
      </button>
    );
  }

  return (
    <div className="mt-4 space-y-3 rounded-xl border border-ink-100 bg-ink-50/30 p-4">
      <div className="flex items-center justify-between">
        <p className="text-[12.5px] font-bold text-ink-800">Add a guideline</p>
        {!busy && (
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setError(null);
            }}
            className="text-[12px] font-medium text-ink-400 hover:text-ink-600"
          >
            Cancel
          </button>
        )}
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".pdf,application/pdf"
        className="hidden"
        disabled={busy}
        onChange={(e) => pick(e.target.files?.[0] ?? null)}
      />

      {file ? (
        <div className="flex items-center gap-3 rounded-lg border border-ink-200 bg-white px-3 py-2.5 shadow-sm">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-500">
            <FileTextIcon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[12.5px] font-semibold text-ink-800">{file.name}</p>
            <p className="text-[11px] text-ink-400">{formatBytes(file.size)}</p>
          </div>
          {!busy && (
            <button
              type="button"
              onClick={() => {
                setFile(null);
                if (fileRef.current) fileRef.current.value = "";
              }}
              className="rounded p-1 text-ink-300 hover:text-rose-500"
              aria-label="Remove file"
            >
              <XCircleIcon className="h-4 w-4" />
            </button>
          )}
        </div>
      ) : (
        <button
          type="button"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            pick(e.dataTransfer.files?.[0] ?? null);
          }}
          className={`flex w-full flex-col items-center gap-1.5 rounded-lg border-2 border-dashed px-3 py-5 text-center transition-colors ${
            dragging
              ? "border-brand-400 bg-brand-50/60"
              : "border-ink-200 bg-white hover:border-brand-300 hover:bg-brand-50/30"
          }`}
        >
          <CloudUploadIcon className="h-6 w-6 text-brand-400" />
          <span className="text-[12.5px] font-semibold text-ink-700">
            Drop a PDF or <span className="text-brand-600">browse</span>
          </span>
          <span className="text-[11px] text-ink-400">
            Its requirements are extracted automatically.
          </span>
        </button>
      )}

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-400">Code</label>
          <input
            type="text"
            value={code}
            disabled={busy}
            onChange={(e) => setCode(e.target.value)}
            placeholder="ICH-E3"
            className={inputCls}
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-400">
            Title <span className="font-normal normal-case text-ink-300">(optional)</span>
          </label>
          <input
            type="text"
            value={title}
            disabled={busy}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Structure and Content of CSRs"
            className={inputCls}
          />
        </div>
      </div>

      {error && (
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[12px] font-medium text-rose-700 ring-1 ring-inset ring-rose-100">
          {error}
        </p>
      )}

      {busy ? (
        <div className="space-y-2 rounded-lg bg-white px-3 py-3 ring-1 ring-inset ring-ink-100">
          <div className="flex items-center gap-2 text-[12.5px] font-semibold text-ink-700">
            <RefreshIcon className="h-4 w-4 animate-spin text-brand-500" />
            Parsing &amp; extracting requirements…
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
            <span className="block h-full w-1/3 animate-pulse rounded-full bg-brand-500" />
          </div>
          <p className="text-[11px] text-ink-400">
            This takes one to two minutes. Keep this tab open.
          </p>
        </div>
      ) : (
        <button type="button" onClick={submit} className="nf-btn-primary w-full justify-center">
          Upload &amp; extract
        </button>
      )}
    </div>
  );
}


import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TopNavBar } from "@/components/layout/TopNavBar";
import { RecentDocuments } from "@/components/dashboard/RecentDocuments";
import { NewWorkflowModal } from "@/components/dashboard/NewWorkflowModal";
import {
  PlusCircleIcon,
  ArrowRightIcon,
  DocumentDuplicateIcon,
  PaintBrushIcon,
  ClipboardListIcon,
} from "@/components/icons/Icons";
import { Card } from "@/components/ui/card";
import { WORKFLOW_DEFINITIONS } from "@/config/wizardSteps";
import { useWorkflow } from "@/context/WorkflowContext";
import { useDocument } from "@/context/DocumentContext";
import { listProjects, getProject, getDocumentVersions, createProject, deleteProject, updateProject } from "@/lib/api";
import { pathForStep } from "@/lib/stepRouting";
import type {
  RecentDocument,
  WorkflowType,
  WizardStepKey,
} from "@/types/workflow";

function isWorkflow(v: string | null | undefined): v is WorkflowType {
  return (
    v === "second-version" || v === "style-update" || v === "compliance-check"
  );
}

function kindFromName(name: string): "pdf" | "docx" {
  return name.toLowerCase().endsWith(".pdf") ? "pdf" : "docx";
}

/** All known wizard step keys for validation. */
const VALID_STEPS = new Set<string>([
  "upload", "extract", "review", "compliance", "style", "generate", "chat", "export",
]);

function isValidStep(step: string | null | undefined): step is WizardStepKey {
  return typeof step === "string" && VALID_STEPS.has(step);
}

/** The interactive "run" step for each workflow. */
function runStepFor(workflow: WorkflowType | null): WizardStepKey {
  if (workflow === "style-update") return "style";
  if (workflow === "compliance-check") return "compliance";
  return "review";
}

/**
 * Compute the set of wizard steps a project has provably reached.
 *
 * Combines persisted completion flags with hard signals — an uploaded source
 * document and a started/finished run — so progress is recovered even for
 * projects saved before completion was persisted (older rows have an empty
 * completion map). Used both to light up the step rail and to choose a resume
 * step.
 */
function reachedSteps(
  completion: Record<string, boolean> | undefined,
  workflow: WorkflowType | null,
  signals: { hasSource: boolean; runStatus?: string | null }
): Set<WizardStepKey> {
  const reached = new Set<WizardStepKey>();
  const comp = completion ?? {};
  for (const k of Object.keys(comp)) {
    if (comp[k] && isValidStep(k)) reached.add(k);
  }
  if (signals.hasSource) {
    reached.add("upload");
    reached.add("extract"); // extraction is deterministic from the stored artifact
  }
  if (signals.runStatus) {
    // A run exists → the flow's interactive step was reached.
    reached.add(runStepFor(workflow));
  }
  if (signals.runStatus === "done") {
    // Output produced → render + terminal steps are ready to view.
    reached.add("generate"); // regenerate renders here (ignored by flows without it)
    reached.add("export");
  }
  return reached;
}

/**
 * Pick the page to resume on: the furthest step the project reached that
 * belongs to this workflow. Landing on the last *reached* step (rather than
 * the one after) keeps a finished project on its output instead of overshooting
 * into an empty downstream step.
 */
function inferResumeStep(
  reached: Set<WizardStepKey>,
  workflow: WorkflowType | null
): WizardStepKey {
  const def = WORKFLOW_DEFINITIONS[workflow ?? "second-version"];
  const stepKeys = def.steps.map((s) => s.key);
  let lastIdx = -1;
  for (let i = 0; i < stepKeys.length; i++) {
    if (reached.has(stepKeys[i])) lastIdx = i;
  }
  return lastIdx >= 0 ? stepKeys[lastIdx] : "upload";
}

/** Tool capabilities surfaced as highlights near the top of the dashboard. */
const CAPABILITIES = [
  {
    icon: DocumentDuplicateIcon,
    title: "Generate New Version",
    desc: "Revise a document while keeping its structure intact.",
  },
  {
    icon: PaintBrushIcon,
    title: "Update Style",
    desc: "Apply a template or formatting guideline.",
  },
  {
    icon: ClipboardListIcon,
    title: "Compliance Check",
    desc: "Flag gaps and deviations before submission.",
  },
] as const;

/**
 * Workflow Selection Dashboard — GSK QA Assist style.
 *
 * Layout:
 *   1. TopNavBar (orange)
 *   2. Welcome section
 *   3. "Start New Analysis" CTA box
 *   4. OR divider
 *   5. Recent Analyses card grid
 *   6. Footer
 */
export function WorkflowSelectionDashboard() {
  const navigate = useNavigate();
  const { setSelectedWorkflow, setWorkflowName } = useWorkflow();
  const {
    setProject,
    setSource,
    setSecondary,
    setRunId,
    hydrateCompletion,
    reset,
  } = useDocument();

  const [recent, setRecent] = useState<RecentDocument[]>([]);
  const [loadingRecent, setLoadingRecent] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listProjects(8)
      .then((projects) => {
        if (cancelled) return;
        setRecent(
          projects.map((p) => ({
            id: p.id,
            name: p.name,
            lastModified: p.updated_at ?? p.created_at ?? new Date().toISOString(),
            workflow: isWorkflow(p.flow_hint) ? p.flow_hint : null,
            progress: p.progress ?? "not_started",
          }))
        );
      })
      .catch(() => {
        /* backend offline — show empty state */
      })
      .finally(() => !cancelled && setLoadingRecent(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const handleStartNew = async (name: string, type: WorkflowType) => {
    reset(); // fresh pipeline
    setSelectedWorkflow(type);
    setWorkflowName(name);

    try {
      // Create the project on the backend immediately so we store the name
      const p = await createProject(name, {
        flow_hint: type,
        meta: {
          started_at: new Date().toISOString(),
          workflow_type: type,
          workflow_title: WORKFLOW_DEFINITIONS[type].title,
        },
      });
      setProject({ id: p.id, name: p.name });
    } catch {
      // If backend is offline, project will be created on upload
    }

    setModalOpen(false);
    navigate("/upload");
  };

  const handleOpenRecent = async (doc: RecentDocument) => {
    try {
      const detail = await getProject(doc.id);
      reset();

      // ── 1. Restore workflow type + name ────────────────────────────
      const wf = isWorkflow(detail.flow_hint) ? detail.flow_hint : null;
      if (wf) setSelectedWorkflow(wf);
      setWorkflowName(detail.name);
      setProject({ id: detail.id, name: detail.name });

      // ── 2. Restore source document (uploaded file) ─────────────────
      // (completion is hydrated below, once progress signals are known)
      const srcDoc = detail.documents.find((d) => d.kind === "source") ?? detail.documents[0];
      let sourceRestored = false;
      if (srcDoc) {
        try {
          const versions = await getDocumentVersions(detail.id, srcDoc.id);
          const latest = versions[0]; // ordered newest-first
          if (latest?.artifact_id) {
            setSource({
              documentId: srcDoc.id,
              artifactId: latest.artifact_id,
              version: latest.version,
              filename: srcDoc.display_name,
              kind: kindFromName(srcDoc.display_name),
            });
            sourceRestored = true;
          }
        } catch {
          /* doc versions not available — leave source null */
        }
      }

      // ── 4. Restore secondary/style document ────────────────────────
      const styleDocs = detail.documents.filter(
        (d) => d.kind === "style" || d.kind === "template"
      );
      if (styleDocs.length > 0) {
        const styleDoc = styleDocs[0];
        try {
          const versions = await getDocumentVersions(detail.id, styleDoc.id);
          const latest = versions[0];
          if (latest?.artifact_id) {
            setSecondary({
              documentId: styleDoc.id,
              artifactId: latest.artifact_id,
              version: latest.version,
              filename: styleDoc.display_name,
              kind: kindFromName(styleDoc.display_name),
            });
          }
        } catch {
          /* style doc versions not available */
        }
      }

      // ── 4. Restore run ID from latest non-cancelled run ────────────
      const activeRun = detail.runs.find((r) => r.status !== "cancelled") ?? null;
      if (activeRun) setRunId(activeRun.id);

      // ── 5. Recover progress + choose the resume step ───────────────
      // Persisted completion flags are authoritative, but older projects
      // saved them as {}, so fall back to hard signals (source doc + run).
      const reached = reachedSteps(detail.completion, wf, {
        hasSource: sourceRestored,
        runStatus: activeRun?.status,
      });
      const mergedCompletion = Object.fromEntries(
        [...reached].map((k) => [k, true])
      );
      hydrateCompletion(mergedCompletion);

      // Resume on the page the user last visited if it was persisted;
      // otherwise the furthest reached step.
      const targetStep: WizardStepKey = isValidStep(detail.current_step)
        ? detail.current_step
        : inferResumeStep(reached, wf);

      // Backfill the DB so the next reopen is exact (no-op if already set).
      if (reached.size > 0) {
        updateProject(detail.id, {
          completion: mergedCompletion,
          current_step: targetStep,
        }).catch(() => {
          /* backend offline — sessionStorage still carries the pipeline */
        });
      }

      navigate(pathForStep(targetStep));
    } catch {
      navigate("/upload");
    }
  };

  const handleDeleteRecent = (docId: string) => {
    // Optimistic: drop the card from the UI immediately, then delete on the
    // server. If the request fails, restore the card at its original spot.
    const index = recent.findIndex((doc) => doc.id === docId);
    if (index === -1) return;
    const removed = recent[index];
    setRecent((cur) => cur.filter((doc) => doc.id !== docId));
    deleteProject(docId).catch((err) => {
      console.error("Failed to delete project", err);
      setRecent((cur) => {
        if (cur.some((doc) => doc.id === docId)) return cur; // already present
        const next = [...cur];
        next.splice(Math.min(index, next.length), 0, removed);
        return next;
      });
    });
  };

  return (
    <div className="min-h-screen bg-ink-50">
      <TopNavBar />

      <main
        id="main"
        className="mx-auto w-full max-w-[1280px] px-4 pb-16 pt-8 sm:px-6 lg:px-8"
      >
        {/* Welcome section */}
        <section className="animate-fade-up">
          <h1 className="text-2xl font-extrabold tracking-tight text-ink-800 sm:text-3xl">
            Welcome back!
          </h1>
          <p className="mt-1 text-sm text-ink-500 sm:text-base">
            Your AI workspace for clinical &amp; regulatory document formatting.
          </p>
        </section>

        {/* What you can do — capability highlights */}
        <section
          className="mt-6 grid grid-cols-1 gap-4 animate-fade-up sm:grid-cols-3"
          style={{ animationDelay: "40ms" }}
          aria-label="What you can do"
        >
          {CAPABILITIES.map(({ icon: Icon, title, desc }) => (
            <Card key={title} className="flex items-start gap-3 p-4">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
                <Icon className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-bold text-ink-800">{title}</p>
                <p className="mt-0.5 text-[12.5px] leading-snug text-ink-500">{desc}</p>
              </div>
            </Card>
          ))}
        </section>

        {/* Start New Project CTA */}
        <section className="mt-6 animate-fade-up" style={{ animationDelay: "60ms" }}>
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className="group flex w-full items-center gap-4 rounded-2xl border-2 border-dashed border-brand-300 bg-brand-50/40 px-6 py-5 text-left transition-all duration-200 hover:border-brand-500 hover:bg-brand-50 hover:shadow-card-hover focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
            id="start-new-analysis"
          >
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-brand-500 text-white shadow-sm transition-transform duration-200 group-hover:scale-105">
              <PlusCircleIcon className="h-6 w-6" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-bold tracking-tight text-ink-800">
                Start New Project
              </h2>
              <p className="text-sm text-ink-500">
                Upload your documents and begin formatting
              </p>
            </div>
            <ArrowRightIcon className="h-5 w-5 shrink-0 text-brand-400 transition-all duration-200 group-hover:translate-x-1 group-hover:text-brand-600" />
          </button>
        </section>

        {/* OR Divider */}
        <div
          className="my-8 flex items-center gap-4 animate-fade-up"
          style={{ animationDelay: "120ms" }}
        >
          <span className="h-px flex-1 bg-ink-200" />
          <span className="text-sm font-medium text-ink-400">OR</span>
          <span className="h-px flex-1 bg-ink-200" />
        </div>

        {/* Recent Analyses */}
        <div className="animate-fade-up" style={{ animationDelay: "180ms" }}>
          <RecentDocuments
            documents={recent}
            onOpen={handleOpenRecent}
            onDelete={handleDeleteRecent}
            loading={loadingRecent}
          />
        </div>

        {/* Footer */}
        <footer className="mt-16 flex flex-col items-center justify-between gap-2 border-t border-ink-200 pt-6 text-xs text-ink-400 sm:flex-row">
          <span>&copy; {new Date().getFullYear()} Navikenz. All rights reserved.</span>
          <span>Navi FormatiQ &middot; v0.1.0</span>
        </footer>
      </main>

      {/* New Workflow Modal */}
      <NewWorkflowModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onStart={handleStartNew}
      />
    </div>
  );
}

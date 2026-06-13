import { useMemo } from "react";
import { RefreshIcon } from "@/components/icons/Icons";
import type { RunDetail, RunEvent } from "@/types/api";

/** Friendly labels for the LangGraph node names the backend streams. */
const NODE_LABELS: Record<string, string> = {
  ingestion_router: "Ingesting documents",
  template_parser: "Parsing template structure",
  draft_parser: "Parsing draft content",
  structure_mapper: "Mapping sections",
  rag_retriever: "Retrieving domain knowledge",
  content_generator: "Generating content",
  rules_compliance: "Checking compliance rules",
  coverage_validator: "Validating coverage",
  diff_builder: "Building change diff",
  style_apply: "Applying styles",
  docx_writer: "Rendering document",
  review_gate: "Awaiting review",
  // Compliance audit nodes
  extract_user_doc: "Reading your document",
  load_guideline: "Loading the guideline",
  align_sections: "Aligning sections",
  check_requirements: "Auditing requirements",
  deterministic_checks: "Checking structure & limits",
  verify_findings: "Verifying critical findings",
  aggregate_scores: "Scoring compliance",
  report_build: "Building the report",
  system: "Run",
};

function label(node: string): string {
  return NODE_LABELS[node] ?? node.replace(/_/g, " ");
}

/**
 * Live progress indicator for a flow run.
 *
 * Shows a single, modern indeterminate progress bar while the pipeline runs
 * (rather than a checklist of internal node names). A subtle caption surfaces
 * the current stage — driven by the real `run.traces` + live SSE `events` —
 * so the user gets honest feedback without a noisy fake step list.
 */
export function RunProgress({
  run,
  events,
  title = "Working…",
}: {
  run: RunDetail | null;
  events: RunEvent[];
  title?: string;
}) {
  // The latest real, non-system stage — used only for a single status line.
  const current = useMemo(() => {
    let last = "";
    for (const t of run?.traces ?? []) {
      if (t.agent && t.agent !== "system") last = t.agent;
    }
    for (const e of events) {
      if (e.agent && e.agent !== "system") last = e.agent;
    }
    return last;
  }, [run, events]);

  // Treat "no run object yet" as active so the bar shows while starting.
  const active = !run || run.status === "running" || run.status === "pending";

  return (
    <div className="nf-card overflow-hidden p-6">
      <header className="mb-5 flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
          <RefreshIcon className={`h-5 w-5 ${active ? "animate-spin" : ""}`} />
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-ink-800">{title}</h2>
          <p className="text-[12px] text-ink-500">
            {active ? "This usually takes under a minute." : "Pipeline finished."}
          </p>
        </div>
      </header>

      {/* Modern progress bar — indeterminate sweep while active, full on done. */}
      <div
        className="relative h-2 w-full overflow-hidden rounded-full bg-ink-100"
        role="progressbar"
        aria-label={title}
        aria-busy={active}
      >
        {active ? (
          <span className="absolute inset-y-0 w-2/5 animate-progress-loop rounded-full bg-gradient-to-r from-brand-300 via-brand-500 to-brand-600" />
        ) : (
          <span className="absolute inset-0 rounded-full bg-emerald-500" />
        )}
      </div>

      {/* Single honest status line (real backend stage). */}
      {active && (
        <p className="mt-3 flex items-center gap-2 text-[12.5px] font-medium text-ink-600">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-500" />
          </span>
          {current ? `${label(current)}…` : "Starting the pipeline…"}
        </p>
      )}

      {run?.warnings && run.warnings.length > 0 && (
        <ul className="mt-4 space-y-1.5">
          {run.warnings.map((w, i) => (
            <li
              key={i}
              className="rounded-lg bg-amber-50 px-3 py-2 text-[12px] text-amber-800"
            >
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

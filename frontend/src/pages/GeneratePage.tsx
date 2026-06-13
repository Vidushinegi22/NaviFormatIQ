import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import { RunProgress } from "@/components/wizard/RunProgress";
import { DocumentRenderer } from "@/components/wizard/DocumentRenderer";
import { NeedsSourcePanel, ErrorPanel, WarningsList } from "@/components/wizard/States";
import {
  MagicWandIcon,
  ArrowRightIcon,
  ChevronRightIcon,
  FileTextIcon,
  ExpandIcon,
  ToggleIcon,
} from "@/components/icons/Icons";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { useDocument } from "@/context/DocumentContext";
import { useRun } from "@/lib/useRun";
import { pathForStep } from "@/lib/stepRouting";
import { sectionChanged } from "@/lib/docBlocks";

/** Element-id prefixes for the two comparison panels' section anchors. */
const SECTION_ID_PREFIXES = ["nf-sec-original", "nf-sec-proposed"] as const;

/**
 * Scroll both comparison panels to a section without moving the page itself —
 * each panel is its own scroll container (marked with `data-doc-scroll`).
 */
function scrollToSlot(slotId: string) {
  for (const prefix of SECTION_ID_PREFIXES) {
    const el = document.getElementById(`${prefix}-${slotId}`);
    const box = el?.closest<HTMLElement>("[data-doc-scroll]");
    if (!el || !box) continue;
    const top =
      el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 12;
    box.scrollTo({ top: Math.max(top, 0), behavior: "smooth" });
  }
}

/**
 * Result page (formerly "Generate").
 *
 * When the run completes, shows a side-by-side comparison of the
 * original document (V1) and the updated/generated document with
 * formatted rendering and diff highlighting.
 */
export function GeneratePage() {
  const navigate = useNavigate();
  const { source, targetVersion, markComplete, setCurrentStep, runId } = useDocument();
  const { run, events, status } = useRun(runId);

  const [showHighlights, setShowHighlights] = useState(true);
  const [expandedPanel, setExpandedPanel] = useState<
    "original" | "proposed" | null
  >(null);
  /** Index into the changed-sections list (null until the user navigates). */
  const [activeChange, setActiveChange] = useState<number | null>(null);

  // Track current step for resume-on-reopen
  useEffect(() => {
    setCurrentStep("generate");
  }, [setCurrentStep]);

  useEffect(() => {
    if (status === "done") markComplete("generate");
  }, [status, markComplete]);

  if (!source) {
    return (
      <WizardLayout activeKey="generate" title="Result">
        <NeedsSourcePanel />
      </WizardLayout>
    );
  }

  // No run started yet — the user needs to go through Review first.
  if (!runId) {
    return (
      <WizardLayout activeKey="generate" title="Result">
        <div className="mx-auto max-w-xl">
          <div className="nf-card flex flex-col items-center gap-3 p-8 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
              <MagicWandIcon className="h-6 w-6" />
            </span>
            <h2 className="text-lg font-bold text-ink-800">Nothing to show yet</h2>
            <p className="max-w-md text-sm text-ink-500">
              Review and approve the proposed changes first — results appear
              once you submit your review.
            </p>
            <Link to={pathForStep("review")} className="nf-btn-primary">
              Go to Revise
              <ArrowRightIcon className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </WizardLayout>
    );
  }

  // Build the primary action for the footer.
  const primaryAction =
    status === "done"
      ? {
          label: "Export",
          onClick: () => navigate(pathForStep("export")),
        }
      : { label: "Continue", onClick: () => {}, disabled: true };

  // ── Non-done states ──────────────────────────────────────────────
  if (status === "error") {
    return (
      <WizardLayout
        activeKey="generate"
        title="Result"
        subtitle="Compare your original and updated documents side by side."
        primaryAction={primaryAction}
      >
        <ErrorPanel
          title="Rendering failed"
          message={run?.error ?? "The document could not be rendered."}
          onRetry={() => navigate(pathForStep("review"))}
        />
      </WizardLayout>
    );
  }

  if (status === "hitl") {
    return (
      <WizardLayout
        activeKey="generate"
        title="Result"
        subtitle="Compare your original and updated documents side by side."
        primaryAction={primaryAction}
      >
        <div className="mx-auto max-w-xl">
          <div className="nf-card flex flex-col items-center gap-3 p-8 text-center">
            <h2 className="text-lg font-bold text-ink-800">Awaiting your review</h2>
            <p className="max-w-md text-sm text-ink-500">
              This document is paused at the review gate. Approve the changes to
              render it.
            </p>
            <Link to={pathForStep("review")} className="nf-btn-primary">
              Go to Revise
              <ArrowRightIcon className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </WizardLayout>
    );
  }

  if (status !== "done" || !run) {
    return (
      <WizardLayout
        activeKey="generate"
        title="Result"
        subtitle="Rendering your reviewed revision into a formatted document."
        primaryAction={primaryAction}
      >
        <div className="mx-auto max-w-2xl">
          <RunProgress run={run} events={events} title="Rendering document…" />
        </div>
      </WizardLayout>
    );
  }

  // ── Done — show the comparison view ──────────────────────────────

  const sectionCount = run.diff.length;
  const changedDiffs = run.diff.filter((d) =>
    sectionChanged(d.original, d.proposed)
  );
  const changeCount = changedDiffs.length;
  const hasContent = sectionCount > 0;

  // Prev/next stepping through changed sections; both panels scroll along.
  const activeSlotId =
    activeChange != null ? changedDiffs[activeChange]?.slot_id ?? null : null;
  const stepChange = (delta: 1 | -1) => {
    if (changeCount === 0) return;
    const next =
      activeChange == null
        ? delta === 1
          ? 0
          : changeCount - 1
        : (activeChange + delta + changeCount) % changeCount;
    setActiveChange(next);
    scrollToSlot(changedDiffs[next].slot_id);
  };

  // Version-aware labels (Generate-New-Version flow). Fall back to the old
  // "Version 1" / "_v2" wording when no version labels were supplied.
  const origVersionLabel = source.versionLabel
    ? `Version ${source.versionLabel}`
    : "Version 1";
  const newVersionLabel =
    targetVersion != null ? `Version ${targetVersion}` : "Updated";
  const newFileName = source.filename.replace(
    /(\.[^.]+)$/,
    `_v${targetVersion ?? 2}$1`
  );

  return (
    <WizardLayout
      activeKey="generate"
      title="Result"
      subtitle="Compare your original and updated documents side by side."
      primaryAction={primaryAction}
      headerActions={
        <div className="flex items-center gap-2">
          {/* Change navigation — steps through changed sections in both panels */}
          {changeCount > 0 && (
            <div className="flex items-center gap-0.5 rounded-lg border border-ink-200 bg-white px-1 py-0.5 shadow-sm">
              <button
                type="button"
                onClick={() => stepChange(-1)}
                className="nf-btn-ghost p-1"
                aria-label="Previous change"
              >
                <ChevronRightIcon className="h-3.5 w-3.5 rotate-180" />
              </button>
              <span className="min-w-[88px] text-center text-[12px] font-semibold tabular-nums text-ink-600">
                {activeChange == null
                  ? `${changeCount} change${changeCount === 1 ? "" : "s"}`
                  : `Change ${activeChange + 1} of ${changeCount}`}
              </span>
              <button
                type="button"
                onClick={() => stepChange(1)}
                className="nf-btn-ghost p-1"
                aria-label="Next change"
              >
                <ChevronRightIcon className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          {/* Highlight toggle */}
          <button
            type="button"
            onClick={() => setShowHighlights((v) => !v)}
            className={`nf-btn-ghost gap-1.5 ${
              showHighlights ? "text-brand-600" : ""
            }`}
            aria-pressed={showHighlights}
            title={showHighlights ? "Hide highlights" : "Show highlights"}
          >
            <ToggleIcon
              className="h-5 w-5"
              on={showHighlights}
            />
            <span className="text-[13px]">
              {showHighlights ? "Highlights On" : "Highlights Off"}
            </span>
          </button>
        </div>
      }
    >
      {/* Summary bar */}
      <div className="mb-5 flex flex-col gap-2 rounded-xl bg-brand-50/60 px-4 py-3 text-[13px] text-brand-800 ring-1 ring-inset ring-brand-100 sm:flex-row sm:items-center sm:justify-between">
        <span className="font-semibold">
          Document generated successfully — {changeCount} section
          {changeCount !== 1 ? "s" : ""} updated
        </span>
        <span className="text-brand-700/80 text-[12px]">
          {showHighlights ? (
            <>
              <span className="inline-block w-3 h-3 rounded-sm bg-amber-200/80 mr-1 align-middle" />
              Modified in original
              <span className="mx-2 text-brand-300">|</span>
              <span className="inline-block w-3 h-3 rounded-sm bg-emerald-200/80 mr-1 align-middle" />
              New in updated
            </>
          ) : (
            "Highlights off — showing clean documents"
          )}
        </span>
      </div>

      <WarningsList warnings={run.warnings} />

      {/* Side-by-side panels */}
      <div
        className={`grid gap-5 ${
          expandedPanel
            ? "grid-cols-1"
            : "grid-cols-1 lg:grid-cols-2"
        }`}
      >
        {/* LEFT: Original document */}
        {expandedPanel !== "proposed" && (
          <div className="nf-card overflow-hidden flex flex-col">
            {/* Panel header */}
            <div className="flex items-center justify-between border-b border-ink-100 bg-ink-50/40 px-4 py-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
                  <FileTextIcon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-ink-800 truncate">
                    {source.filename}
                  </p>
                  <p className="text-[11px] text-ink-500">
                    Original Document ({origVersionLabel})
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <span className="rounded-md bg-brand-100 px-2 py-0.5 text-[11px] font-bold text-brand-700 uppercase">
                  {source.kind.toUpperCase()}
                </span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedPanel(
                          expandedPanel === "original" ? null : "original"
                        )
                      }
                      className="nf-btn-ghost p-1.5"
                      aria-label={
                        expandedPanel === "original"
                          ? "Exit fullscreen"
                          : "Expand panel"
                      }
                    >
                      <ExpandIcon className="h-4 w-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {expandedPanel === "original" ? "Exit fullscreen" : "Expand panel"}
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>

            {/* Document content */}
            <div
              data-doc-scroll
              className="flex-1 overflow-auto"
              style={{ maxHeight: expandedPanel ? "70vh" : "65vh" }}
            >
              {hasContent ? (
                <DocumentRenderer
                  diffs={run.diff}
                  mode="original"
                  showHighlights={showHighlights}
                  filename={source.filename}
                  sectionIdPrefix="nf-sec-original"
                  activeSlotId={activeSlotId}
                />
              ) : (
                <div className="flex items-center justify-center py-20">
                  <p className="text-sm text-ink-400 italic">
                    No content changes were made
                  </p>
                </div>
              )}
            </div>

            {/* Panel footer */}
            <div className="border-t border-ink-100 bg-ink-50/30 px-4 py-2 flex items-center justify-between">
              <span className="text-[11px] text-ink-500">
                {sectionCount} section{sectionCount !== 1 ? "s" : ""}
              </span>
              {showHighlights && (
                <div className="flex items-center gap-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-200/80" />
                  <span className="text-[11px] text-ink-500">
                    Modified content
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* RIGHT: Updated document */}
        {expandedPanel !== "original" && (
          <div className="nf-card overflow-hidden flex flex-col">
            {/* Panel header */}
            <div className="flex items-center justify-between border-b border-ink-100 bg-ink-50/40 px-4 py-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100">
                  <FileTextIcon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-ink-800 truncate">
                    {newFileName}
                  </p>
                  <p className="text-[11px] text-ink-500">
                    Updated Document ({newVersionLabel})
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <span className="rounded-md bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-700">
                  {changeCount} CHANGES
                </span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedPanel(
                          expandedPanel === "proposed" ? null : "proposed"
                        )
                      }
                      className="nf-btn-ghost p-1.5"
                      aria-label={
                        expandedPanel === "proposed"
                          ? "Exit fullscreen"
                          : "Expand panel"
                      }
                    >
                      <ExpandIcon className="h-4 w-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {expandedPanel === "proposed" ? "Exit fullscreen" : "Expand panel"}
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>

            {/* Document content */}
            <div
              data-doc-scroll
              className="flex-1 overflow-auto"
              style={{ maxHeight: expandedPanel ? "70vh" : "65vh" }}
            >
              {hasContent ? (
                <DocumentRenderer
                  diffs={run.diff}
                  mode="proposed"
                  showHighlights={showHighlights}
                  filename={newFileName}
                  sectionIdPrefix="nf-sec-proposed"
                  activeSlotId={activeSlotId}
                />
              ) : (
                <div className="flex items-center justify-center py-20">
                  <p className="text-sm text-ink-400 italic">
                    No content changes were made
                  </p>
                </div>
              )}
            </div>

            {/* Panel footer */}
            <div className="border-t border-ink-100 bg-ink-50/30 px-4 py-2 flex items-center justify-between">
              <span className="text-[11px] text-ink-500">
                {sectionCount} section{sectionCount !== 1 ? "s" : ""}
              </span>
              {showHighlights && (
                <div className="flex items-center gap-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-sm bg-emerald-200/80" />
                  <span className="text-[11px] text-ink-500">
                    New content
                  </span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </WizardLayout>
  );
}

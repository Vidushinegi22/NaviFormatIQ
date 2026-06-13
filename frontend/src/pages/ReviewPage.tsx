import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import { RunProgress } from "@/components/wizard/RunProgress";
import { NeedsSourcePanel, ErrorPanel, WarningsList } from "@/components/wizard/States";
import { SectionDiffView } from "@/components/wizard/DocumentRenderer";
import {
  SparklesIcon,
  MagicWandIcon,
  RefreshIcon,
  ArrowRightIcon,
  PencilIcon,
  EyeIcon,
  XIcon,
  TrashIcon,
  PlusCircleIcon,
  ClockIcon,
  InfoIcon,
  FilterIcon,
} from "@/components/icons/Icons";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useDocument } from "@/context/DocumentContext";
import { startRegenerate, resumeRun, getRevisionSuggestions, ApiError } from "@/lib/api";
import { useRun } from "@/lib/useRun";
import { pathForStep } from "@/lib/stepRouting";
import { sectionChanged } from "@/lib/docBlocks";
import type {
  DocProfile,
  FieldUpdates,
  ReviewDecisionItem,
  ReviewDiff,
  RevisionSuggestion,
} from "@/types/api";

/** Per-section reviewer decision for an EXISTING template section. */
interface Decision {
  accepted: boolean; // false → the section is removed from the new version
  text: string; // the final body text (AI proposal, or the reviewer's edit)
  title: string; // the heading text (editable)
}

/** A brand-new section the reviewer adds. */
interface NewSection {
  id: string;
  title: string;
  level: number;
  text: string;
}

/**
 * Slice of review work persisted to sessionStorage so navigating away
 * mid-review (and back) loses nothing. Keyed per run — and, pre-run, per
 * source document for the change-requests textarea.
 */
interface StoredReview {
  suggestions?: string;
  decisions?: Record<string, Decision>;
  newSections?: NewSection[];
}

const REVIEW_STORE_PREFIX = "nf.review.";

/**
 * Page 3 (second-version flow) — Review.
 *
 * Pauses at the human-in-the-loop gate. Each proposed section is shown as a
 * formatted preview with the AI's changes highlighted; the reviewer can accept,
 * edit (heading + body, add/remove bullets), or remove it, and can add entirely
 * new sections. Every change is applied — with formatting — to the rendered doc.
 */
export function ReviewPage() {
  const navigate = useNavigate();
  const { project, source, contextDocs, targetVersion, domainId, runId, setRunId, markComplete, setCurrentStep } =
    useDocument();
  const { run, events, status } = useRun(runId);

  const [suggestions, setSuggestions] = useState("");
  /** Which kick-off is in flight: AI rewrite, manual (skip-AI), or none. */
  const [startMode, setStartMode] = useState<"ai" | "manual" | null>(null);
  /** Sticky for the whole run so the review screen knows it was a manual start. */
  const [manualMode, setManualMode] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [newSections, setNewSections] = useState<NewSection[]>([]);
  /** Hide sections the AI left untouched (original === proposed). */
  const [changedOnly, setChangedOnly] = useState(false);
  // AI change suggestions for the new version (pre-run side panel).
  const [suggItems, setSuggItems] = useState<RevisionSuggestion[]>([]);
  const [suggLoading, setSuggLoading] = useState(false);
  const [appliedSugg, setAppliedSugg] = useState<Set<number>>(new Set());
  const startedRef = useRef(false);
  const suggFetchedRef = useRef(false);
  const newIdRef = useRef(0);
  /** Which storage key has been restored — gates writes until restore ran. */
  const restoredKeyRef = useRef<string | null>(null);

  useEffect(() => {
    setCurrentStep("review");
  }, [setCurrentStep]);

  /* ── Review-work persistence (sessionStorage, survives navigation) ──── */

  // Where this page's in-progress work lives: per run once one exists; the
  // pre-run change requests are keyed by the source document instead.
  const storeKey = runId
    ? `${REVIEW_STORE_PREFIX}${runId}`
    : source
    ? `${REVIEW_STORE_PREFIX}draft.${source.documentId}`
    : null;

  // Restore once per key, so coming back mid-review picks up where you left off.
  useEffect(() => {
    if (!storeKey || restoredKeyRef.current === storeKey) return;
    restoredKeyRef.current = storeKey;
    try {
      const raw = window.sessionStorage.getItem(storeKey);
      if (!raw) return;
      const saved = JSON.parse(raw) as StoredReview;
      if (typeof saved.suggestions === "string") setSuggestions(saved.suggestions);
      if (saved.decisions && typeof saved.decisions === "object") {
        setDecisions(saved.decisions);
      }
      if (Array.isArray(saved.newSections)) {
        setNewSections(saved.newSections);
        // Keep the id counter ahead of the restored ids so new ones never collide.
        newIdRef.current = saved.newSections.reduce(
          (max, s) => Math.max(max, Number(s.id.replace("new_", "")) || 0),
          newIdRef.current
        );
      }
    } catch {
      /* corrupted entry — start fresh */
    }
  }, [storeKey]);

  // Persist on change (lightly debounced); an all-empty state removes the entry.
  useEffect(() => {
    if (!storeKey || restoredKeyRef.current !== storeKey) return;
    const t = window.setTimeout(() => {
      try {
        if (!suggestions && Object.keys(decisions).length === 0 && newSections.length === 0) {
          window.sessionStorage.removeItem(storeKey);
        } else {
          const payload: StoredReview = { suggestions, decisions, newSections };
          window.sessionStorage.setItem(storeKey, JSON.stringify(payload));
        }
      } catch {
        /* storage unavailable/full — persistence is best-effort */
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [storeKey, suggestions, decisions, newSections]);

  /** Drop both persisted entries (run + pre-run draft) for this review. */
  const clearStoredReview = useCallback(() => {
    try {
      if (runId) window.sessionStorage.removeItem(`${REVIEW_STORE_PREFIX}${runId}`);
      if (source) {
        window.sessionStorage.removeItem(`${REVIEW_STORE_PREFIX}draft.${source.documentId}`);
      }
    } catch {
      /* ignore */
    }
  }, [runId, source]);

  // Fetch smart, substantive change suggestions once, before any run starts.
  useEffect(() => {
    if (!project || !source || runId || suggFetchedRef.current) return;
    suggFetchedRef.current = true;
    setSuggLoading(true);
    getRevisionSuggestions(project.id, {
      draft_document_id: source.documentId,
      context_document_ids: contextDocs.map((d) => d.documentId),
      domain_id: domainId,
    })
      .then((res) => setSuggItems(res.suggestions ?? []))
      .catch(() => setSuggItems([]))
      .finally(() => setSuggLoading(false));
  }, [project, source, runId, contextDocs, domainId]);

  const applySuggestion = useCallback((idx: number, s: RevisionSuggestion) => {
    const line = `- ${s.detail.trim()}`;
    setSuggestions((prev) =>
      prev.trim() ? `${prev.replace(/\s+$/, "")}\n${line}` : line
    );
    setAppliedSugg((prev) => new Set(prev).add(idx));
  }, []);

  const decisionFor = useCallback(
    (d: ReviewDiff): Decision =>
      decisions[d.slot_id] ?? { accepted: true, text: d.proposed, title: d.title || "" },
    [decisions]
  );
  const setDecision = useCallback((slotId: string, patch: Partial<Decision>, base: Decision) => {
    setDecisions((prev) => ({ ...prev, [slotId]: { ...base, ...patch } }));
  }, []);

  const startRun = useCallback(
    async (skipAi: boolean) => {
      if (!project || !source || startedRef.current) return;
      startedRef.current = true;
      setStartMode(skipAi ? "manual" : "ai");
      setManualMode(skipAi);
      setError(null);
      try {
        const { run_id } = await startRegenerate(project.id, {
          draft_document_id: source.documentId,
          context_document_ids: contextDocs.map((d) => d.documentId),
          target_version: targetVersion != null ? String(targetVersion) : null,
          // Manual path: ignore any typed suggestions — the AI stays out of it.
          user_suggestions: skipAi ? null : suggestions.trim() || null,
          skip_ai_rewrite: skipAi,
          version_bump: "minor", // fallback when no explicit target_version
        });
        setRunId(run_id);
      } catch (e) {
        startedRef.current = false;
        setError(e instanceof ApiError ? e.message : (e as Error).message);
      } finally {
        setStartMode(null);
      }
    },
    [project, source, contextDocs, targetVersion, suggestions, setRunId]
  );

  const submitReview = useCallback(async () => {
    if (!runId || !run) return;
    setResuming(true);
    setError(null);
    try {
      const existing: ReviewDecisionItem[] = run.diff.map((d) => {
        const dec = decisions[d.slot_id] ?? { accepted: true, text: d.proposed, title: d.title || "" };
        if (!dec.accepted) return { slot_id: d.slot_id, accepted: false };
        return {
          slot_id: d.slot_id,
          accepted: true,
          reviewer_edit: dec.text,
          title: dec.title && dec.title !== d.title ? dec.title : undefined,
        };
      });
      const added: ReviewDecisionItem[] = newSections
        .filter((ns) => ns.title.trim() || ns.text.trim())
        .map((ns) => ({
          slot_id: ns.id,
          is_new: true,
          accepted: true,
          title: ns.title.trim() || "New Section",
          level: ns.level,
          reviewer_edit: ns.text,
        }));
      await resumeRun(runId, [...existing, ...added]);
      clearStoredReview(); // review is submitted — drop the saved work
      markComplete("review");
      navigate(pathForStep("generate"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
      setResuming(false);
    }
  }, [runId, run, decisions, newSections, clearStoredReview, markComplete, navigate]);

  const restart = () => {
    clearStoredReview(); // saved decisions belong to the abandoned run
    startedRef.current = false;
    setRunId(null);
    setManualMode(false);
    setDecisions({});
    setNewSections([]);
    setEditing(null);
    setChangedOnly(false);
    setError(null);
  };

  /** Bulk accept/reject every existing section (keeps any text/title edits). */
  const setAllAccepted = useCallback(
    (accepted: boolean) => {
      if (!run) return;
      setDecisions((prev) => {
        const next = { ...prev };
        for (const d of run.diff) {
          const base =
            prev[d.slot_id] ?? { accepted: true, text: d.proposed, title: d.title || "" };
          next[d.slot_id] = { ...base, accepted };
        }
        return next;
      });
    },
    [run]
  );

  const addNewSection = () => {
    newIdRef.current += 1;
    const id = `new_${newIdRef.current}`;
    setNewSections((prev) => [...prev, { id, title: "", level: 1, text: "" }]);
    setEditing(id);
  };

  if (!source) {
    return (
      <WizardLayout activeKey="review" title="Revise document">
        <NeedsSourcePanel />
      </WizardLayout>
    );
  }

  const includedExisting = run
    ? run.diff.filter((d) => (decisions[d.slot_id] ?? { accepted: true }).accepted).length
    : 0;
  const includedNew = newSections.filter((ns) => ns.title.trim() || ns.text.trim()).length;
  const changedCount = run
    ? run.diff.filter((d) => sectionChanged(d.original, d.proposed)).length
    : 0;
  // Sections shown in the review list — "Changed only" hides untouched ones.
  const visibleDiff = run
    ? changedOnly
      ? run.diff.filter((d) => sectionChanged(d.original, d.proposed))
      : run.diff
    : [];

  const primaryAction =
    status === "hitl"
      ? {
          label: resuming ? "Generating…" : `Approve`,
          onClick: submitReview,
          disabled: resuming,
        }
      : status === "done"
      ? { label: "View generated document", onClick: () => navigate(pathForStep("generate")) }
      : { label: "Continue", onClick: () => {}, disabled: true };

  return (
    <WizardLayout
      activeKey="review"
      title="Revise document"
      subtitle="Update the document with AI or edit it by hand — every change is applied, with formatting, to the new version."
      primaryAction={primaryAction}
    >
      {/* Pre-run: choose an AI revision or a manual edit, with smart
          suggestions in a side panel. */}
      {!runId && (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="nf-card p-6 lg:p-7">
            <header className="mb-5 flex items-center gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
                <MagicWandIcon className="h-6 w-6" />
              </span>
              <div>
                <h2 className="text-base font-bold text-ink-800">Generate a revised version</h2>
                <p className="text-[13px] text-ink-500">
                  We'll rewrite <span className="font-medium">{source.filename}</span> section by
                  section, preserving its structure.
                </p>
              </div>
            </header>

            {/* Inputs side-by-side on wide screens so the card fills the width. */}
            <div className="grid items-start gap-5 lg:grid-cols-[1.4fr_1fr]">
              <div className="flex flex-col">
                <label
                  htmlFor="suggestions"
                  className="mb-1.5 block text-[12px] font-semibold uppercase tracking-wider text-ink-500"
                >
                  Change requests{" "}
                  <span className="font-normal normal-case text-ink-400">· optional</span>
                </label>
                <textarea
                  id="suggestions"
                  value={suggestions}
                  onChange={(e) => setSuggestions(e.target.value)}
                  placeholder="e.g. Add a bullet to the Quality Assurance section about completing expiry checks on schedule, and tighten the executive summary."
                  className="min-h-[156px] w-full resize-y rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-800 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
                />
              </div>

              {/* Version + prior-version context summary */}
              <div className="rounded-lg bg-ink-50/60 p-4 ring-1 ring-inset ring-ink-100">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[12px] font-semibold uppercase tracking-wider text-ink-500">
                    New version
                  </p>
                  <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-[12px] font-bold text-brand-700 ring-1 ring-inset ring-brand-100">
                    {targetVersion != null ? `Version ${targetVersion}` : "Auto-bumped"}
                  </span>
                </div>
                {contextDocs.length > 0 && (
                  <p className="mt-2.5 text-[11.5px] text-ink-600">
                    Using {contextDocs.length} earlier version
                    {contextDocs.length === 1 ? "" : "s"} as context:{" "}
                    <span className="font-medium text-ink-700">
                      {contextDocs
                        .map((d) => (d.versionLabel ? `v${d.versionLabel}` : d.filename))
                        .join(", ")}
                    </span>
                    .
                  </p>
                )}
                <p className="mt-2.5 text-[11.5px] leading-relaxed text-ink-400">
                  We auto-set the version, refresh the effective date, and add a revision-history
                  row — so you don't have to.
                </p>
              </div>
            </div>

            {error && (
              <p className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>
            )}

            {/* Actions: AI rewrite (primary) or manual edit. */}
            <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-stretch">
              <button
                type="button"
                onClick={() => startRun(false)}
                disabled={startMode !== null}
                className="nf-btn-primary w-full justify-center py-3 sm:flex-[2]"
              >
                {startMode === "ai" ? "Starting…" : "Generate revised draft"}
              </button>
              <button
                type="button"
                onClick={() => startRun(true)}
                disabled={startMode !== null}
                className="inline-flex w-full items-center justify-center rounded-lg border border-ink-200 bg-white px-4 py-3 text-sm font-semibold text-ink-700 shadow-sm transition-all duration-200 hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700 disabled:cursor-not-allowed disabled:opacity-50 sm:flex-1"
              >
                {startMode === "manual" ? "Opening editor…" : "Edit manually"}
              </button>
            </div>
            <p className="mt-2.5 text-[11.5px] text-ink-400">
              <span className="font-semibold text-ink-500">Generate revised draft</span> rewrites the
              content with AI. <span className="font-semibold text-ink-500">Edit manually</span> jumps
              straight to the editor — either way, the version, date &amp; revision row update
              automatically.
            </p>
          </div>

          <SuggestionsPanel
            loading={suggLoading}
            items={suggItems}
            applied={appliedSugg}
            onApply={applySuggestion}
          />
        </div>
      )}

      {/* Running (before the review gate). */}
      {runId && status !== "hitl" && status !== "done" && status !== "error" && (
        <div className="mx-auto max-w-2xl">
          <RunProgress run={run} events={events} title="Drafting revised version…" />
        </div>
      )}

      {/* Error. */}
      {runId && status === "error" && (
        <ErrorPanel
          title="Generation failed"
          message={run?.error ?? error ?? "The run did not complete."}
          onRetry={restart}
        />
      )}

      {/* Already generated (returning to this step). */}
      {runId && status === "done" && (
        <div className="mx-auto max-w-2xl">
          <Card className="flex flex-col items-center gap-3 p-8 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100">
              <SparklesIcon className="h-6 w-6" />
            </span>
            <h2 className="text-lg font-bold text-ink-800">Revision generated</h2>
            <p className="max-w-md text-sm text-ink-500">
              Your reviewed changes have been applied and the document was rendered.
            </p>
            <Button onClick={() => navigate(pathForStep("generate"))}>
              View generated document
              <ArrowRightIcon className="h-4 w-4" />
            </Button>
          </Card>
        </div>
      )}

      {/* HITL — the diff review. */}
      {runId && status === "hitl" && run && (
        <div className="space-y-5">
          <div className="flex flex-col gap-2.5 rounded-xl bg-brand-50/70 px-4 py-3 text-[13px] text-brand-800 ring-1 ring-inset ring-brand-100">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <span className="inline-flex items-center gap-2 font-semibold">
                {manualMode ? <PencilIcon className="h-4 w-4" /> : <SparklesIcon className="h-4 w-4" />}
                {manualMode
                  ? "Manual edit — your content is unchanged; edit any section, then approve"
                  : changedCount > 0
                  ? `${changedCount} of ${run.diff.length} section${run.diff.length === 1 ? "" : "s"} changed by AI`
                  : "No AI changes — review, edit, and approve to render"}
              </span>
              <span className="text-brand-700/80">
                {includedExisting + includedNew} section
                {includedExisting + includedNew === 1 ? "" : "s"} will be included.
              </span>
            </div>

            {/* Bulk actions + changed-only filter */}
            <div className="flex flex-wrap items-center gap-1.5 border-t border-brand-100 pt-2">
              <button
                type="button"
                onClick={() => setAllAccepted(true)}
                disabled={includedExisting === run.diff.length}
                className="rounded-md px-2 py-1 text-[12px] font-semibold text-emerald-700 transition-colors hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Accept all
              </button>
              <button
                type="button"
                onClick={() => setAllAccepted(false)}
                disabled={includedExisting === 0}
                className="rounded-md px-2 py-1 text-[12px] font-semibold text-rose-600 transition-colors hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Reject all
              </button>
              <span className="mx-1 h-4 w-px bg-brand-100" aria-hidden />
              <button
                type="button"
                onClick={() => setChangedOnly((v) => !v)}
                aria-pressed={changedOnly}
                className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-semibold transition-colors ${
                  changedOnly
                    ? "bg-white text-brand-700 shadow-sm ring-1 ring-inset ring-brand-200"
                    : "text-brand-700/80 hover:bg-white/60"
                }`}
              >
                <FilterIcon className="h-3.5 w-3.5" /> Changed only
              </button>
              {changedOnly && (
                <span className="text-[12px] text-brand-700/80">
                  showing {visibleDiff.length} of {run.diff.length}
                </span>
              )}
            </div>
          </div>

          <WarningsList warnings={run.warnings} />

          <DocumentInsights profile={run.doc_profile} fieldUpdates={run.field_updates} />

          <ul className="space-y-4">
            {changedOnly && visibleDiff.length === 0 && (
              <li className="nf-card p-5 text-center text-[13px] text-ink-500">
                No sections were changed — turn off "Changed only" to review the rest.
              </li>
            )}
            {visibleDiff.map((d) => {
              const dec = decisionFor(d);
              return (
                <DiffCard
                  key={d.slot_id}
                  diff={d}
                  decision={dec}
                  isEditing={editing === d.slot_id}
                  onEdit={(on) => setEditing(on ? d.slot_id : null)}
                  onChangeText={(text) => setDecision(d.slot_id, { text }, dec)}
                  onChangeTitle={(title) => setDecision(d.slot_id, { title }, dec)}
                  onToggleAccept={() => setDecision(d.slot_id, { accepted: !dec.accepted }, dec)}
                  onUseOriginal={() => setDecision(d.slot_id, { text: d.original }, dec)}
                  onResetProposed={() =>
                    setDecision(d.slot_id, { text: d.proposed, title: d.title || "" }, dec)
                  }
                />
              );
            })}

            {newSections.map((ns) => (
              <NewSectionCard
                key={ns.id}
                section={ns}
                onChange={(patch) =>
                  setNewSections((prev) =>
                    prev.map((s) => (s.id === ns.id ? { ...s, ...patch } : s))
                  )
                }
                onRemove={() => {
                  setNewSections((prev) => prev.filter((s) => s.id !== ns.id));
                  if (editing === ns.id) setEditing(null);
                }}
              />
            ))}
          </ul>

          <button
            type="button"
            onClick={addNewSection}
            className="flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-ink-200 py-3 text-[13px] font-semibold text-ink-500 transition-colors hover:border-brand-300 hover:text-brand-600"
          >
            <PlusCircleIcon className="h-4 w-4" /> Add a new section
          </button>

          {error && (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>
          )}
        </div>
      )}
    </WizardLayout>
  );
}

/* ── Document insights: profile + automatic field updates ─────────────── */

function DocumentInsights({
  profile,
  fieldUpdates,
}: {
  profile?: DocProfile | null;
  fieldUpdates?: FieldUpdates | null;
}) {
  const reps = fieldUpdates?.replacements ?? [];
  const revision = fieldUpdates?.revision;
  const hasUpdates = reps.length > 0 || !!revision;
  if (!profile && !hasUpdates) return null;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {profile && (
        <div className="nf-card p-4">
          <header className="mb-2 flex items-center gap-2">
            <InfoIcon className="h-4 w-4 text-brand-500" />
            <h3 className="text-[13px] font-bold text-ink-800">Understood your document</h3>
          </header>
          <div className="flex flex-wrap gap-1.5">
            {profile.doc_type && <Chip>{profile.doc_type}</Chip>}
            {profile.tone && <Chip muted>{profile.tone}</Chip>}
            {profile.document_number && <Chip muted>{profile.document_number}</Chip>}
          </div>
          {profile.summary && (
            <p className="mt-2 text-[12.5px] leading-relaxed text-ink-600">{profile.summary}</p>
          )}
        </div>
      )}

      {hasUpdates && (
        <div className="nf-card border-l-2 border-l-emerald-300 p-4">
          <header className="mb-2 flex items-center gap-2">
            <ClockIcon className="h-4 w-4 text-emerald-600" />
            <h3 className="text-[13px] font-bold text-ink-800">Automatic updates</h3>
          </header>
          <ul className="space-y-1.5 text-[12.5px] text-ink-700">
            {reps.map((r, i) => (
              <li key={i} className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden />
                <span className="font-medium text-ink-500">{r.label}:</span>
                <span className="text-ink-400 line-through">{r.find}</span>
                <ArrowRightIcon className="h-3 w-3 text-ink-300" />
                <span className="font-semibold text-ink-800">{r.replace}</span>
              </li>
            ))}
            {revision && (
              <li className="flex items-start gap-1.5">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden />
                <span>
                  <span className="font-medium text-ink-500">Revision history:</span> added row{" "}
                  <span className="font-semibold text-ink-800">
                    {revision.row.filter(Boolean).join(" · ")}
                  </span>
                </span>
              </li>
            )}
          </ul>
          <p className="mt-2 text-[11px] text-ink-400">
            These redundant fields are updated for you on the new version.
          </p>
        </div>
      )}
    </div>
  );
}

function Chip({ children, muted }: { children: ReactNode; muted?: boolean }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
        muted ? "bg-ink-100 text-ink-600" : "bg-brand-50 text-brand-700"
      }`}
    >
      {children}
    </span>
  );
}

/* ── Smart change suggestions (pre-run side panel) ────────────────────── */

function kindLabel(kind: string): string {
  if (kind === "add_section") return "New section";
  if (kind === "expand_section") return "Expand";
  return "Revise";
}

/**
 * Subtle side panel of 2-3 AI-suggested, substantive changes for the new
 * version. Each can be appended to the change-requests box with one click.
 * Always rendered (loading / empty / list) so the layout stays balanced.
 */
function SuggestionsPanel({
  loading,
  items,
  applied,
  onApply,
}: {
  loading: boolean;
  items: RevisionSuggestion[];
  applied: Set<number>;
  onApply: (idx: number, s: RevisionSuggestion) => void;
}) {
  return (
    <aside className="h-fit rounded-2xl border border-ink-100 bg-ink-50/40 p-4 lg:sticky lg:top-4">
      <header className="mb-3 flex items-center gap-2">
        <SparklesIcon className="h-4 w-4 text-brand-500" />
        <h3 className="text-[12px] font-bold uppercase tracking-wider text-ink-600">
          Suggested changes
        </h3>
      </header>

      {loading ? (
        <div className="space-y-2.5">
          {[0, 1].map((i) => (
            <div key={i} className="h-[68px] animate-pulse rounded-lg bg-ink-100/70" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="text-[12px] leading-relaxed text-ink-400">
          No suggestions to show — write your own change requests, or edit the
          document by hand.
        </p>
      ) : (
        <ul className="space-y-2.5">
          {items.map((s, i) => {
            const isApplied = applied.has(i);
            return (
              <li
                key={i}
                className="rounded-lg border border-ink-100 bg-white p-3 shadow-sm"
              >
                <p className="text-[12.5px] font-semibold leading-snug text-ink-800">
                  {s.title}
                </p>
                {s.section && (
                  <p className="mt-0.5 text-[11px] text-ink-400">
                    {kindLabel(s.kind)} · {s.section}
                  </p>
                )}
                <p className="mt-1 text-[12px] leading-relaxed text-ink-600">{s.detail}</p>
                <button
                  type="button"
                  onClick={() => onApply(i, s)}
                  disabled={isApplied}
                  className={`mt-2 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold transition-colors ${
                    isApplied
                      ? "cursor-default text-emerald-600"
                      : "text-brand-600 hover:bg-brand-50"
                  }`}
                >
                  {isApplied ? (
                    "Added to requests"
                  ) : (
                    <>
                      <PlusCircleIcon className="h-3.5 w-3.5" /> Add to requests
                    </>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-3 text-[10.5px] leading-relaxed text-ink-400">
        AI-generated from this document and its previous versions.
      </p>
    </aside>
  );
}

/* ── Existing-section review card ─────────────────────────────────────── */

function DiffCard({
  diff,
  decision,
  isEditing,
  onEdit,
  onChangeText,
  onChangeTitle,
  onToggleAccept,
  onUseOriginal,
  onResetProposed,
}: {
  diff: ReviewDiff;
  decision: Decision;
  isEditing: boolean;
  onEdit: (on: boolean) => void;
  onChangeText: (text: string) => void;
  onChangeTitle: (title: string) => void;
  onToggleAccept: () => void;
  onUseOriginal: () => void;
  onResetProposed: () => void;
}) {
  const aiChanged = sectionChanged(diff.original, diff.proposed);
  const userEdited = decision.text !== diff.proposed || decision.title !== (diff.title || "");
  const rejected = !decision.accepted;

  const liveDiff: ReviewDiff = { ...diff, title: decision.title, proposed: decision.text };

  let pill: { label: string; cls: string };
  if (rejected) pill = { label: "Removed", cls: "bg-rose-50 text-rose-700" };
  else if (userEdited) pill = { label: "Your edit", cls: "bg-amber-50 text-amber-700" };
  else if (aiChanged) pill = { label: "AI revised", cls: "bg-emerald-50 text-emerald-700" };
  else pill = { label: "Unchanged", cls: "bg-ink-100 text-ink-600" };

  return (
    <li className={`nf-card p-5 ${rejected ? "opacity-75" : ""}`}>
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-bold text-ink-800">{decision.title || diff.slot_id}</h3>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${pill.cls}`}>
            {pill.label}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {!rejected && (
            <button
              type="button"
              onClick={() => onEdit(!isEditing)}
              className={`nf-btn-ghost gap-1 px-2 py-1 text-[12px] ${isEditing ? "text-brand-600" : ""}`}
            >
              {isEditing ? <EyeIcon className="h-3.5 w-3.5" /> : <PencilIcon className="h-3.5 w-3.5" />}
              {isEditing ? "Preview" : "Edit"}
            </button>
          )}
          <button
            type="button"
            onClick={onToggleAccept}
            className={`nf-btn-ghost gap-1 px-2 py-1 text-[12px] ${rejected ? "text-emerald-600" : "text-rose-600"}`}
          >
            {rejected ? (
              <>
                <RefreshIcon className="h-3.5 w-3.5" /> Restore
              </>
            ) : (
              <>
                <XIcon className="h-3.5 w-3.5" /> Remove section
              </>
            )}
          </button>
        </div>
      </header>

      {rejected ? (
        <p className="rounded-lg bg-rose-50/60 px-3 py-2 text-[13px] text-rose-700 ring-1 ring-inset ring-rose-100">
          This section (heading and content) will be removed from the new version. Restore it to
          keep it.
        </p>
      ) : isEditing ? (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-500">
              Heading
            </label>
            <input
              value={decision.title}
              onChange={(e) => onChangeTitle(e.target.value)}
              className="w-full rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-[13px] font-semibold text-ink-800 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
                Content
                <span className="ml-1 font-normal normal-case text-ink-400">
                  — one bullet per line, start with “- ”
                </span>
              </p>
              <textarea
                value={decision.text}
                onChange={(e) => onChangeText(e.target.value)}
                rows={Math.min(16, Math.max(5, decision.text.split("\n").length + 1))}
                className="w-full resize-y rounded-lg border border-ink-200 bg-white px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink-800 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
              />
            </div>
            <div>
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
                Live preview
              </p>
              <div className="max-h-72 overflow-auto rounded-lg border border-ink-100 bg-white px-3 py-2">
                <SectionDiffView diff={liveDiff} mode="proposed" showHighlights={false} showTitle={false} />
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={onUseOriginal} className="nf-btn-ghost px-2 py-1 text-[12px]">
              Use original text
            </button>
            <button
              type="button"
              onClick={onResetProposed}
              className="nf-btn-ghost px-2 py-1 text-[12px]"
              disabled={!userEdited}
            >
              Reset to AI version
            </button>
            <button
              type="button"
              onClick={() => onEdit(false)}
              className="nf-btn-ghost ml-auto px-2 py-1 text-[12px] text-brand-600"
            >
              Done editing
            </button>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-ink-100 bg-white px-1 py-1">
          <SectionDiffView
            diff={liveDiff}
            mode="proposed"
            showHighlights={aiChanged && !userEdited}
            showTitle={false}
          />
        </div>
      )}

      {diff.sources && diff.sources.length > 0 && !rejected && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">
            Sources
          </span>
          {diff.sources.map((s, i) => (
            <span
              key={i}
              className="rounded-full bg-ink-50 px-2 py-0.5 text-[11px] font-medium text-ink-600 ring-1 ring-inset ring-ink-100"
            >
              {s}
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

/* ── New-section card ─────────────────────────────────────────────────── */

function NewSectionCard({
  section,
  onChange,
  onRemove,
}: {
  section: NewSection;
  onChange: (patch: Partial<NewSection>) => void;
  onRemove: () => void;
}) {
  const liveDiff: ReviewDiff = {
    slot_id: section.id,
    title: section.title || "New section",
    original: "",
    proposed: section.text,
    sources: [],
  };
  return (
    <li className="nf-card border-l-2 border-l-emerald-300 p-5">
      <header className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
            New section
          </span>
        </div>
        <button
          type="button"
          onClick={onRemove}
          className="nf-btn-ghost gap-1 px-2 py-1 text-[12px] text-rose-600"
        >
          <TrashIcon className="h-3.5 w-3.5" /> Discard
        </button>
      </header>

      <div className="space-y-3">
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[220px] flex-1">
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-500">
              Heading
            </label>
            <input
              value={section.title}
              onChange={(e) => onChange({ title: e.target.value })}
              placeholder="e.g. 8. Appendix"
              className="w-full rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-[13px] font-semibold text-ink-800 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-500">
              Level
            </label>
            <select
              value={section.level}
              onChange={(e) => onChange({ level: Number(e.target.value) })}
              className="rounded-lg border border-ink-200 bg-white px-2 py-1.5 text-[13px] text-ink-800 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            >
              <option value={1}>Heading 1</option>
              <option value={2}>Heading 2</option>
              <option value={3}>Heading 3</option>
            </select>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
              Content
              <span className="ml-1 font-normal normal-case text-ink-400">
                — one bullet per line, start with “- ”
              </span>
            </p>
            <textarea
              value={section.text}
              onChange={(e) => onChange({ text: e.target.value })}
              rows={Math.min(16, Math.max(5, section.text.split("\n").length + 1))}
              placeholder={"Intro sentence.\n- First point\n- Second point"}
              className="w-full resize-y rounded-lg border border-ink-200 bg-white px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink-800 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
              Live preview
            </p>
            <div className="max-h-72 overflow-auto rounded-lg border border-ink-100 bg-white px-3 py-2">
              {section.text.trim() ? (
                <SectionDiffView diff={liveDiff} mode="proposed" showHighlights={false} showTitle={false} />
              ) : (
                <p className="text-[12px] italic text-ink-400">Start typing to preview…</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </li>
  );
}

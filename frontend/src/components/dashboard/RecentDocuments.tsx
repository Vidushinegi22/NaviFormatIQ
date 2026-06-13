import { useMemo, useState } from "react";
import {
  FileTextIcon,
  ArrowRightIcon,
  SearchIcon,
  CalendarIcon,
  FolderIcon,
  ClockIcon,
  ChevronDownIcon,
  TrashIcon,
} from "@/components/icons/Icons";
import { WORKFLOW_DEFINITIONS } from "@/config/wizardSteps";
import type { RecentDocument } from "@/types/workflow";
import type { ProjectProgress } from "@/types/api";

type StatusFilter = "all" | ProjectProgress;

/** Badge label + colour for each derived progress state. */
const PROGRESS_META: Record<ProjectProgress, { label: string; color: string }> = {
  not_started: {
    label: "Not Started",
    color: "bg-ink-100 text-ink-600 ring-ink-200",
  },
  in_progress: {
    label: "In Progress",
    color: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  completed: {
    label: "Completed",
    color: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
};

interface RecentAnalysesProps {
  documents: RecentDocument[];
  /** Called when the user clicks a campaign card to resume it. */
  onOpen: (doc: RecentDocument) => void;
  /** Called when the user deletes a campaign. */
  onDelete?: (docId: string) => void;
  /** Show a loading placeholder while projects are being fetched. */
  loading?: boolean;
}

/**
 * Recent Analyses section — campaign card grid.
 *
 * Renders each previous project as a visual card with status badge,
 * workflow type tag, project ID, creation date, document / run counts,
 * and a quick-open action. Includes search + status filter.
 */
export function RecentDocuments({
  documents,
  onOpen,
  onDelete,
  loading,
}: RecentAnalysesProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const dateFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      }),
    []
  );

  const relativeFormatter = useMemo(
    () =>
      new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }),
    []
  );

  const formatRelative = (iso: string) => {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.round(diff / 60_000);
    if (mins < 1) return "Just now";
    if (mins < 60) return relativeFormatter.format(-mins, "minute");
    const hours = Math.round(mins / 60);
    if (hours < 24) return relativeFormatter.format(-hours, "hour");
    const days = Math.round(hours / 24);
    return relativeFormatter.format(-days, "day");
  };

  const filtered = useMemo(() => {
    let items = documents;
    if (search.trim()) {
      const q = search.toLowerCase();
      items = items.filter(
        (d) =>
          d.name.toLowerCase().includes(q) ||
          d.id.toLowerCase().includes(q)
      );
    }
    if (statusFilter !== "all") {
      items = items.filter((d) => d.progress === statusFilter);
    }
    return items;
  }, [documents, search, statusFilter]);

  return (
    <section aria-labelledby="recent-analyses-heading">
      {/* Section header with search + filter */}
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2
            id="recent-analyses-heading"
            className="text-lg font-bold tracking-tight text-ink-800"
          >
            Recent Projects
          </h2>
          <p className="text-sm text-ink-500">
            {loading
              ? "Loading…"
              : `${filtered.length} project${filtered.length !== 1 ? "s" : ""}`}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Search */}
          <div className="relative">
            <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search projects..."
              className="w-48 rounded-lg border border-ink-200 bg-white py-2 pl-9 pr-3 text-sm text-ink-700 placeholder-ink-400 outline-none transition-colors focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            />
          </div>

          {/* Status filter */}
          <div className="relative">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              className="appearance-none rounded-lg border border-ink-200 bg-white py-2 pl-3 pr-8 text-sm font-medium text-ink-700 outline-none transition-colors focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            >
              <option value="all">All Status</option>
              <option value="not_started">Not Started</option>
              <option value="in_progress">In Progress</option>
              <option value="completed">Completed</option>
            </select>
            <ChevronDownIcon className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
          </div>
        </div>
      </div>

      {/* Cards grid */}
      {loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="nf-campaign-card animate-pulse"
            >
              <div className="h-4 w-24 rounded bg-ink-100" />
              <div className="mt-3 h-5 w-40 rounded bg-ink-100" />
              <div className="mt-2 h-3 w-32 rounded bg-ink-100" />
              <div className="mt-4 flex gap-4">
                <div className="h-3 w-20 rounded bg-ink-100" />
                <div className="h-3 w-16 rounded bg-ink-100" />
              </div>
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState hasSearch={search.length > 0} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((doc, idx) => (
            <CampaignCard
              key={doc.id}
              doc={doc}
              dateFormatter={dateFormatter}
              formatRelative={formatRelative}
              onOpen={onOpen}
              onDelete={onDelete}
              index={idx}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Campaign Card ──────────────────────────────────────────────────────── */

function CampaignCard({
  doc,
  dateFormatter,
  formatRelative,
  onOpen,
  onDelete,
  index,
}: {
  doc: RecentDocument;
  dateFormatter: Intl.DateTimeFormat;
  formatRelative: (iso: string) => string;
  onOpen: (doc: RecentDocument) => void;
  onDelete?: (docId: string) => void;
  index: number;
}) {
  const wf = doc.workflow ? WORKFLOW_DEFINITIONS[doc.workflow] : null;
  const wfTitle = wf?.title ?? "Workspace";
  const { label: statusLabel, color: statusColor } =
    PROGRESS_META[doc.progress] ?? PROGRESS_META.not_started;

  // Generate a stable, human-friendly project ID from the project UUID.
  const projectId = `PROJ-${new Date(doc.lastModified).getFullYear()}-${doc.id.slice(0, 3).toUpperCase()}`;

  const delayMs = Math.min(index, 6) * 50;

  return (
    <button
      type="button"
      onClick={() => onOpen(doc)}
      style={{ animationDelay: `${delayMs}ms` }}
      className="nf-campaign-card group text-left animate-fade-up"
      aria-label={`Open ${doc.name}`}
    >
      {/* Top row: icon + badges */}
      <div className="flex items-start justify-between gap-2">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
          <FolderIcon className="h-5 w-5" />
        </span>
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${statusColor}`}
          >
            {statusLabel}
          </span>
          <span className="inline-flex items-center rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-semibold text-ink-600 ring-1 ring-inset ring-ink-200">
            {wfTitle}
          </span>
          {onDelete && (
            <div
              role="button"
              tabIndex={0}
              className="ml-1 rounded p-1 text-ink-400 transition-colors hover:bg-red-50 hover:text-red-500 z-10"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onDelete(doc.id);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  e.stopPropagation();
                  onDelete(doc.id);
                }
              }}
              aria-label={`Delete ${doc.name}`}
            >
              <TrashIcon className="h-4 w-4" />
            </div>
          )}
        </div>
      </div>

      {/* Name */}
      <h3 className="mt-3 truncate text-base font-bold tracking-tight text-ink-800 group-hover:text-brand-600 transition-colors">
        {doc.name}
      </h3>

      {/* Project ID */}
      <p className="mt-0.5 text-xs text-ink-500">
        ID: {projectId}
      </p>

      {/* Meta: created date + last updated */}
      <div className="mt-3 flex items-center justify-between text-xs text-ink-500">
        <span className="inline-flex items-center gap-1.5">
          <CalendarIcon className="h-3.5 w-3.5" />
          {dateFormatter.format(new Date(doc.lastModified))}
        </span>
        <span className="inline-flex items-center gap-1.5 text-ink-400">
          <ClockIcon className="h-3.5 w-3.5" />
          {formatRelative(doc.lastModified)}
        </span>
      </div>

      {/* Hover arrow */}
      <div className="mt-3 flex items-center justify-end">
        <ArrowRightIcon className="h-4 w-4 text-ink-300 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-brand-500" />
      </div>
    </button>
  );
}

/* ── Empty state ────────────────────────────────────────────────────────── */

function EmptyState({ hasSearch }: { hasSearch: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-ink-200 px-5 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-ink-100 text-ink-400">
        <FileTextIcon className="h-6 w-6" />
      </span>
      <p className="text-sm font-medium text-ink-700">
        {hasSearch ? "No matching projects" : "No projects yet"}
      </p>
      <p className="text-xs text-ink-500">
        {hasSearch
          ? "Try a different search term."
          : "Start a new project to see it here."}
      </p>
    </div>
  );
}

import { useEffect, useState } from "react";
import {
  ClipboardListIcon,
  CodeBracketsIcon,
  DownloadIcon,
  FileTextIcon,
  InfoIcon,
  PaintBrushIcon,
  RefreshIcon,
  TableIcon,
} from "@/components/icons/Icons";
import { downloadExport, getExports, ApiError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { ExportCategory, ExportItem, ExportManifest } from "@/types/api";

/** Section metadata — drives ordering, titles and icons in the catalog. */
const SECTIONS: {
  key: ExportCategory;
  title: string;
  blurb: string;
  icon: React.ReactNode;
}[] = [
  {
    key: "document",
    title: "Document",
    blurb: "The finished file, ready to share.",
    icon: <FileTextIcon className="h-4 w-4" />,
  },
  {
    key: "formatting",
    title: "Styling & formatting",
    blurb: "The look of the document — reuse it elsewhere.",
    icon: <PaintBrushIcon className="h-4 w-4" />,
  },
  {
    key: "data",
    title: "Data",
    blurb: "Structured content for downstream tools.",
    icon: <CodeBracketsIcon className="h-4 w-4" />,
  },
  {
    key: "report",
    title: "Reports",
    blurb: "Summaries of what happened in this run.",
    icon: <ClipboardListIcon className="h-4 w-4" />,
  },
];

function formatBadge(format: string): { label: string; cls: string } {
  switch (format) {
    case "docx":
      return { label: "Word", cls: "bg-blue-50 text-blue-700 ring-blue-100" };
    case "pdf":
      return { label: "PDF", cls: "bg-rose-50 text-rose-700 ring-rose-100" };
    case "json":
      return { label: "JSON", cls: "bg-violet-50 text-violet-700 ring-violet-100" };
    case "csv":
      return { label: "CSV", cls: "bg-emerald-50 text-emerald-700 ring-emerald-100" };
    default:
      return { label: format.toUpperCase(), cls: "bg-ink-100 text-ink-600 ring-ink-200" };
  }
}

function itemIcon(item: ExportItem): React.ReactNode {
  if (item.format === "csv") return <TableIcon className="h-5 w-5" />;
  if (item.format === "json") return <CodeBracketsIcon className="h-5 w-5" />;
  if (item.category === "formatting") return <PaintBrushIcon className="h-5 w-5" />;
  if (item.category === "report") return <ClipboardListIcon className="h-5 w-5" />;
  return <FileTextIcon className="h-5 w-5" />;
}

/**
 * Export hub — renders the backend-driven catalog of deliverables for a
 * finished run as grouped, downloadable cards. New deliverables added on the
 * backend appear here automatically.
 */
export function ExportCatalog({ runId }: { runId: string }) {
  const [manifest, setManifest] = useState<ExportManifest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [itemError, setItemError] = useState<{ id: string; message: string } | null>(null);

  useEffect(() => {
    let alive = true;
    setManifest(null);
    setLoadError(null);
    getExports(runId)
      .then((m) => alive && setManifest(m))
      .catch((e) =>
        alive &&
        setLoadError(e instanceof ApiError ? e.message : (e as Error).message)
      );
    return () => {
      alive = false;
    };
  }, [runId]);

  const handle = async (item: ExportItem) => {
    setBusy(item.id);
    setItemError(null);
    try {
      await downloadExport(runId, item);
    } catch (e) {
      setItemError({
        id: item.id,
        message: e instanceof ApiError ? e.message : (e as Error).message,
      });
    } finally {
      setBusy(null);
    }
  };

  if (loadError) {
    return (
      <p className="rounded-lg bg-rose-50 px-3 py-3 text-sm text-rose-700">
        Couldn't load downloads: {loadError}
      </p>
    );
  }

  if (!manifest) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-ink-50/60 px-3 py-4 text-sm text-ink-500">
        <RefreshIcon className="h-4 w-4 animate-spin" />
        Preparing your downloads…
      </div>
    );
  }

  if (manifest.exports.length === 0) {
    return (
      <p className="rounded-lg bg-ink-50/60 px-3 py-3 text-center text-sm text-ink-500">
        No downloadable file was produced by this run.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {SECTIONS.map((section) => {
        const items = manifest.exports.filter((e) => e.category === section.key);
        if (items.length === 0) return null;
        return (
          <section key={section.key}>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ink-100 text-ink-500">
                {section.icon}
              </span>
              <h3 className="text-sm font-bold text-ink-800">{section.title}</h3>
              <span className="text-[12px] text-ink-400">· {section.blurb}</span>
            </div>
            <div className="space-y-2.5">
              {items.map((item) => {
                const badge = formatBadge(item.format);
                const showErr = itemError?.id === item.id;
                return (
                  <div key={item.id}>
                    <div
                      className={`flex items-center gap-3 rounded-lg border px-4 py-3 ${
                        item.available
                          ? "border-ink-100 bg-white"
                          : "border-ink-100 bg-ink-50/50"
                      }`}
                    >
                      <span
                        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ring-1 ring-inset ${
                          item.available
                            ? "bg-brand-50 text-brand-500 ring-brand-100"
                            : "bg-ink-100 text-ink-400 ring-ink-200"
                        }`}
                      >
                        {itemIcon(item)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-semibold text-ink-800">
                            {item.label}
                          </p>
                          <span
                            className={`inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ring-inset ${badge.cls}`}
                          >
                            {badge.label}
                          </span>
                        </div>
                        <p className="truncate text-[12px] text-ink-500">
                          {item.description}
                          {item.size_bytes ? <> · {formatBytes(item.size_bytes)}</> : null}
                        </p>
                      </div>
                      {item.available ? (
                        <button
                          type="button"
                          onClick={() => handle(item)}
                          disabled={busy === item.id}
                          className="nf-btn-primary shrink-0"
                        >
                          {busy === item.id ? (
                            <RefreshIcon className="h-4 w-4 animate-spin" />
                          ) : (
                            <DownloadIcon className="h-4 w-4" />
                          )}
                          Download
                        </button>
                      ) : (
                        <span
                          className="flex shrink-0 items-center gap-1 text-[12px] text-ink-400"
                          title={item.reason ?? "Unavailable"}
                        >
                          <InfoIcon className="h-4 w-4" />
                          Unavailable
                        </span>
                      )}
                    </div>
                    {!item.available && item.reason && (
                      <p className="mt-1 pl-1 text-[12px] text-ink-400">{item.reason}</p>
                    )}
                    {showErr && (
                      <p className="mt-1 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
                        {itemError!.message}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

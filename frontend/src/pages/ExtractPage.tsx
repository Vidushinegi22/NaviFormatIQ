import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import {
  NeedsSourcePanel,
  LoadingPanel,
  ErrorPanel,
} from "@/components/wizard/States";
import {
  CodeBracketsIcon,
  FileTextIcon,
  HeadingIcon,
  ImageIcon,
  ParagraphIcon,
  TableIcon,
  DocumentDuplicateIcon,
  PaintBrushIcon,
} from "@/components/icons/Icons";
import { useDocument } from "@/context/DocumentContext";
import { useWorkflow } from "@/context/WorkflowContext";
import { getExtract, ApiError } from "@/lib/api";
import { nextStepPath } from "@/lib/stepRouting";
import type {
  ContentElement,
  DocumentContent,
  DocumentStyling,
  TextRun,
} from "@/types/api";

/** A section groups a heading with its child elements. */
interface Section {
  headingIdx: number;
  heading: ContentElement;
  children: { el: ContentElement; idx: number }[];
}

function runText(content?: TextRun[] | null): string {
  return (content ?? []).map((r) => r.text).join("");
}

/**
 * Page 2 — Extract.
 *
 * Fetches the real content + styling JSON the backend extracted from the
 * uploaded document and lets the user confirm the parse: a heading outline,
 * element counts, the detected page setup, fonts and named styles.
 */
export function ExtractPage() {
  const navigate = useNavigate();
  const { source, markComplete, setCurrentStep } = useDocument();
  const { selectedWorkflow } = useWorkflow();

  const [content, setContent] = useState<DocumentContent | null>(null);
  const [styling, setStyling] = useState<DocumentStyling | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // When set, the next load forces a fresh extraction (Rerun Extraction)
  // instead of returning the cached result.
  const forceNext = useRef(false);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [showJson, setShowJson] = useState(false);

  // Track current step for resume-on-reopen
  useEffect(() => {
    setCurrentStep("extract");
  }, [setCurrentStep]);

  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    const force = forceNext.current;
    forceNext.current = false;
    getExtract(source.documentId, source.version, force)
      .then(({ content: c, styling: s }) => {
        if (cancelled) return;
        setContent(c);
        setStyling(s);
        markComplete("extract");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof ApiError ? e.message : (e as Error).message
        );
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [source, reloadKey, markComplete]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const el of content?.elements ?? []) c[el.type] = (c[el.type] ?? 0) + 1;
    return c;
  }, [content]);

  const headings = useMemo(
    () =>
      (content?.elements ?? [])
        .map((el, idx) => ({ el, idx }))
        .filter((x) => x.el.type === "heading"),
    [content]
  );

  /** Sections: each heading + its child paragraphs / list items / tables. */
  const sections = useMemo<Section[]>(() => {
    const elements = content?.elements ?? [];
    const secs: Section[] = [];
    let current: Section | null = null;
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      if (el.type === "heading") {
        current = { headingIdx: i, heading: el, children: [] };
        secs.push(current);
      } else if (current) {
        current.children.push({ el, idx: i });
      }
    }
    return secs;
  }, [content]);

  /** Find the section for the selected heading index. */
  const selectedSection = useMemo<Section | null>(() => {
    if (selectedIdx == null) return null;
    return sections.find((s) => s.headingIdx === selectedIdx) ?? null;
  }, [selectedIdx, sections]);

  const fonts = useMemo(() => {
    const set = new Set<string>();
    for (const rs of Object.values(styling?.run_styles ?? {})) {
      if (rs.font_name) set.add(rs.font_name);
    }
    for (const el of content?.elements ?? []) {
      for (const r of el.content ?? []) {
        if (r.inline_style?.font_name) set.add(r.inline_style.font_name);
      }
    }
    return [...set].sort();
  }, [styling, content]);

  const selected: ContentElement | null =
    selectedIdx != null ? content?.elements[selectedIdx] ?? null : null;

  const jsonText = useMemo(() => {
    if (!content || !styling) return "";
    return JSON.stringify(
      {
        metadata: content.metadata,
        element_count: content.elements.length,
        counts,
        page_style: styling.page_style,
        paragraph_styles: Object.keys(styling.paragraph_styles),
        run_styles: Object.keys(styling.run_styles),
      },
      null,
      2
    );
  }, [content, styling, counts]);

  // While we don't yet have parsed content, the user can't proceed — keep the
  // footer's primary button present but disabled so "Next" never looks ready
  // (and clickable) while "Reading document…" is still on screen.
  const blockedAction = {
    label: "Continue",
    onClick: () => {},
    disabled: true,
  };

  if (!source) {
    return (
      <WizardLayout activeKey="extract" title="Extraction results" primaryAction={blockedAction}>
        <NeedsSourcePanel />
      </WizardLayout>
    );
  }

  if (loading && !content) {
    return (
      <WizardLayout activeKey="extract" title="Extraction results" primaryAction={blockedAction}>
        <LoadingPanel
          title="Reading document…"
          message={`Reading structure and styling from ${source.filename}.`}
        />
      </WizardLayout>
    );
  }

  if (error && !content) {
    return (
      <WizardLayout activeKey="extract" title="Extraction results" primaryAction={blockedAction}>
        <ErrorPanel
          title="Extraction failed"
          message={error}
          onRetry={() => setReloadKey((k) => k + 1)}
        />
      </WizardLayout>
    );
  }

  if (!content || !styling) return null;

  const meta = content.metadata;
  const page = styling.page_style;

  return (
    <WizardLayout
      activeKey="extract"
      title="Extraction results"
      subtitle="Confirm we understood the document — heading outline, element counts, page setup, and detected styles."
      headerActions={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              forceNext.current = true;
              setReloadKey((k) => k + 1);
            }}
            className="nf-btn-ghost"
          >
            <DocumentDuplicateIcon className="h-4 w-4" />
            Rerun Extraction
          </button>
          <button
            type="button"
            onClick={() => setShowJson((v) => !v)}
            className="nf-btn-ghost"
            aria-pressed={showJson}
          >
            <CodeBracketsIcon className="h-4 w-4" />
            {showJson ? "Hide" : "Show"} JSON
          </button>
        </div>
      }
      primaryAction={{
        label: "Continue",
        onClick: () => {
          markComplete("extract");
          const p = nextStepPath(selectedWorkflow, "extract");
          if (p) navigate(p);
        },
      }}
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Heading outline */}
        <section
          aria-labelledby="structure-heading"
          className="nf-card p-5 lg:col-span-4"
        >
          <header className="mb-3 flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">
                Document
              </p>
              <h2 id="structure-heading" className="text-sm font-bold text-ink-800">
                Heading outline
              </h2>
            </div>
            <span className="text-[11px] font-medium text-ink-500">
              {headings.length} headings
            </span>
          </header>
          {headings.length === 0 ? (
            <p className="rounded-lg bg-ink-50/60 px-3 py-3 text-center text-xs text-ink-500">
              No headings were detected in this document.
            </p>
          ) : (
            <ul className="max-h-[28rem] space-y-0.5 overflow-auto">
              {headings.map(({ el, idx }) => {
                const level = Math.max(1, Math.min(el.level ?? 1, 6));
                const isSel = idx === selectedIdx;
                return (
                  <li key={idx}>
                    <button
                      type="button"
                      onClick={() => setSelectedIdx(idx)}
                      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors ${
                        isSel ? "bg-brand-50 text-brand-800" : "text-ink-700 hover:bg-ink-50"
                      }`}
                      style={{ paddingLeft: `${(level - 1) * 14 + 8}px` }}
                      aria-pressed={isSel}
                    >
                      <span
                        className={`inline-flex h-5 min-w-[1.75rem] items-center justify-center rounded px-1 text-[10px] font-bold uppercase ${
                          level === 1 ? "bg-brand-500 text-white" : "bg-ink-100 text-ink-500"
                        }`}
                        aria-hidden="true"
                      >
                        H{level}
                      </span>
                      <span className="truncate font-medium">
                        {runText(el.content) || "(untitled)"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Stats + selected element + JSON */}
        <section className="space-y-6 lg:col-span-5">
          <div className="nf-card p-5">
            <header className="mb-4 flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">
                  Statistics
                </p>
                <h2 className="text-sm font-bold text-ink-800">Document statistics</h2>
              </div>
              <span className="truncate text-[11px] text-ink-500">{source.filename}</span>
            </header>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <StatTile icon={<FileTextIcon className="h-4 w-4" />} label="Pages" value={meta.page_count ?? "—"} />
              <StatTile icon={<DocumentDuplicateIcon className="h-4 w-4" />} label="Elements" value={content.elements.length} />
              <StatTile icon={<HeadingIcon className="h-4 w-4" />} label="Headings" value={counts.heading ?? 0} />
              <StatTile icon={<ParagraphIcon className="h-4 w-4" />} label="Paragraphs" value={counts.paragraph ?? 0} />
              <StatTile icon={<TableIcon className="h-4 w-4" />} label="Tables" value={counts.table ?? 0} />
              <StatTile icon={<ImageIcon className="h-4 w-4" />} label="Images" value={counts.image ?? 0} />
            </div>
            {meta.title || meta.author ? (
              <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-ink-100 pt-4 text-[13px]">
                {meta.title && <Attr label="Title" value={meta.title} />}
                {meta.author && <Attr label="Author" value={meta.author} />}
                {meta.source_type && <Attr label="Source" value={meta.source_type.toUpperCase()} />}
              </dl>
            ) : null}
          </div>

          <div className="nf-card p-5">
            <header className="mb-3">
              <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">
                {selected ? "Selected element" : "Inspector"}
              </p>
              <h2 className="text-sm font-bold text-ink-800">
                {selected ? `${selected.type} element` : "Pick a heading to inspect"}
              </h2>
            </header>
            {selected ? (
              <>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 text-[13px] sm:grid-cols-3">
                  <Attr label="Type" value={selected.type} />
                  <Attr label="Level" value={selected.level != null ? `H${selected.level}` : "—"} />
                  <Attr label="Style ref" value={selected.style_ref ?? "—"} />
                  <Attr label="Alignment" value={selected.inline_style?.alignment ?? "—"} />
                  <Attr
                    label="Space before"
                    value={selected.inline_style?.space_before_pt != null ? `${selected.inline_style.space_before_pt} pt` : "—"}
                  />
                  <Attr
                    label="Line spacing"
                    value={selected.inline_style?.line_spacing != null ? String(selected.inline_style.line_spacing) : "—"}
                  />
                </dl>
                <div className="mt-4 rounded-lg bg-ink-50/60 p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">
                    Heading text
                  </p>
                  <p className="mt-1 text-[13px] leading-relaxed font-semibold text-ink-700">
                    {runText(selected.content) || <span className="italic text-ink-400">(no text)</span>}
                  </p>
                </div>

                {/* Section content preview */}
                {selectedSection && selectedSection.children.length > 0 && (
                  <div className="mt-4">
                    <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
                      Section content ({selectedSection.children.length} elements)
                    </p>
                    <ul className="max-h-48 space-y-1.5 overflow-auto">
                      {selectedSection.children.slice(0, 20).map(({ el, idx }) => {
                        const text = runText(el.content);
                        if (!text) return null;
                        const badge =
                          el.type === "list_item"
                            ? "LI"
                            : el.type === "table"
                              ? "TBL"
                              : "P";
                        const badgeColor =
                          el.type === "list_item"
                            ? "bg-amber-100 text-amber-700"
                            : el.type === "table"
                              ? "bg-purple-100 text-purple-700"
                              : "bg-ink-100 text-ink-500";
                        return (
                          <li
                            key={idx}
                            className="flex items-start gap-2 rounded-md bg-ink-50/40 px-2.5 py-1.5 text-[12px] text-ink-600"
                          >
                            <span
                              className={`mt-0.5 inline-flex h-4 min-w-[1.5rem] shrink-0 items-center justify-center rounded text-[9px] font-bold ${badgeColor}`}
                            >
                              {badge}
                            </span>
                            <span className="line-clamp-2">{text}</span>
                          </li>
                        );
                      })}
                      {selectedSection.children.length > 20 && (
                        <li className="px-2.5 py-1 text-[11px] italic text-ink-400">
                          … and {selectedSection.children.length - 20} more elements
                        </li>
                      )}
                    </ul>
                  </div>
                )}
              </>
            ) : (
              <p className="rounded-lg bg-ink-50/60 px-3 py-3 text-center text-xs text-ink-500">
                Select a heading from the outline to see its attributes.
              </p>
            )}

            {showJson && (
              <div className="mt-5">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
                  Extraction summary JSON
                </p>
                <pre className="max-h-72 overflow-auto rounded-lg bg-ink-900/95 p-4 text-[11.5px] leading-relaxed text-brand-100">
                  <code>{jsonText}</code>
                </pre>
              </div>
            )}
          </div>
        </section>

        {/* Page setup + styles */}
        <section className="space-y-6 lg:col-span-3">
          <div className="nf-card p-5">
            <header className="mb-3 flex items-center gap-2">
              <PaintBrushIcon className="h-4 w-4 text-brand-500" />
              <h2 className="text-sm font-bold text-ink-800">Page setup</h2>
            </header>
            <dl className="space-y-2 text-[13px]">
              <Attr label="Size" value={`${page.width_inches}″ × ${page.height_inches}″`} />
              <Attr label="Orientation" value={page.orientation} />
              <Attr
                label="Margins"
                value={`${page.margins.top_inches}/${page.margins.right_inches}/${page.margins.bottom_inches}/${page.margins.left_inches}″`}
              />
            </dl>
          </div>

          <div className="nf-card p-5">
            <header className="mb-3">
              <h2 className="text-sm font-bold text-ink-800">Detected styles</h2>
            </header>
            <dl className="space-y-2 text-[13px]">
              <Attr label="Paragraph styles" value={String(Object.keys(styling.paragraph_styles).length)} />
              <Attr label="Run styles" value={String(Object.keys(styling.run_styles).length)} />
              <Attr label="Table styles" value={String(Object.keys(styling.table_styles).length)} />
            </dl>
            {fonts.length > 0 && (
              <div className="mt-3 border-t border-ink-100 pt-3">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
                  Fonts
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {fonts.slice(0, 12).map((f) => (
                    <span
                      key={f}
                      className="rounded-full bg-ink-50 px-2 py-0.5 text-[11px] font-medium text-ink-700 ring-1 ring-inset ring-ink-100"
                    >
                      {f}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </WizardLayout>
  );
}

function StatTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg bg-ink-50/60 p-3">
      <span
        aria-hidden="true"
        className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-brand-500 ring-1 ring-ink-100"
      >
        {icon}
      </span>
      <div>
        <p className="text-[11px] uppercase tracking-wider text-ink-500">{label}</p>
        <p className="text-sm font-bold text-ink-800 tabular-nums">
          {typeof value === "number" ? value.toLocaleString() : value}
        </p>
      </div>
    </div>
  );
}

function Attr({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wider text-ink-500">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-[12.5px] font-semibold text-ink-800">
        {value}
      </dd>
    </div>
  );
}

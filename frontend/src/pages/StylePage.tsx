import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import { RunProgress } from "@/components/wizard/RunProgress";
import { NeedsSourcePanel, ErrorPanel } from "@/components/wizard/States";
import {
  PaintBrushIcon,
  FileTextIcon,
  RefreshIcon,
  SparklesIcon,
  DocumentDuplicateIcon,
  HeadingIcon,
  TableIcon,
  ParagraphIcon,
  InfoIcon,
} from "@/components/icons/Icons";
import { Card } from "@/components/ui/card";
import { useDocument } from "@/context/DocumentContext";
import { startStyle, getStyling, interpretStyleSource, ApiError } from "@/lib/api";
import { useRun } from "@/lib/useRun";
import { pathForStep } from "@/lib/stepRouting";
import type {
  DocumentStyling,
  RecognisedStructure,
  StyleInterpretResponse,
  StyleSourceMode,
  StyleSpec,
} from "@/types/api";

function fontsOf(styling: DocumentStyling | null): string[] {
  if (!styling) return [];
  const set = new Set<string>();
  for (const rs of Object.values(styling.run_styles)) {
    if (rs.font_name) set.add(rs.font_name);
  }
  return [...set].sort();
}

/**
 * Style page (style-update flow).
 *
 * The uploaded "Style Template" can be one of two things, and this page adapts
 * to whichever it is:
 *   • an EXAMPLE document whose own look is copied onto the content, or
 *   • a FORMATTING GUIDELINE that *describes* the rules (fonts, colours,
 *     headings, tables, margins) — which we read and extract into a structured
 *     spec, then apply.
 * The backend auto-detects which; the user can confirm or override here.
 */
export function StylePage() {
  const navigate = useNavigate();
  const { project, source, secondary, runId, setRunId, markComplete, setCurrentStep } =
    useDocument();
  const { run, events, status } = useRun(runId);

  const [normalizeFonts, setNormalizeFonts] = useState(true);
  const [promoteHeadings, setPromoteHeadings] = useState(true);
  const [mode, setMode] = useState<StyleSourceMode>("auto");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contentStyle, setContentStyle] = useState<DocumentStyling | null>(null);
  const [templateStyle, setTemplateStyle] = useState<DocumentStyling | null>(null);
  const [interp, setInterp] = useState<StyleInterpretResponse | null>(null);
  const [interpLoading, setInterpLoading] = useState(false);
  const startedRef = useRef(false);

  // Track current step for resume-on-reopen
  useEffect(() => {
    setCurrentStep("style");
  }, [setCurrentStep]);

  // Background-fetch both raw stylings (used for the example-mode before/after).
  useEffect(() => {
    if (source) {
      getStyling(source.documentId, source.version)
        .then(setContentStyle)
        .catch(() => {});
    }
  }, [source]);
  useEffect(() => {
    if (secondary) {
      getStyling(secondary.documentId, secondary.version)
        .then(setTemplateStyle)
        .catch(() => {});
    }
  }, [secondary]);

  // Detect/interpret the style source whenever the template or chosen mode
  // changes. This is what powers the detection card + rules preview.
  useEffect(() => {
    if (!project || !secondary) return;
    let cancelled = false;
    setInterpLoading(true);
    interpretStyleSource(project.id, {
      style_document_id: secondary.documentId,
      mode,
    })
      .then((r) => {
        if (!cancelled) setInterp(r);
      })
      .catch(() => {
        if (!cancelled) setInterp(null);
      })
      .finally(() => {
        if (!cancelled) setInterpLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project, secondary, mode]);

  useEffect(() => {
    if (status === "done") markComplete("style");
  }, [status, markComplete]);

  const apply = useCallback(async () => {
    if (!project || !source || !secondary || startedRef.current) return;
    startedRef.current = true;
    setStarting(true);
    setError(null);
    try {
      const { run_id } = await startStyle(project.id, {
        content_document_id: source.documentId,
        style_document_id: secondary.documentId,
        normalize_fonts: normalizeFonts,
        promote_headings: promoteHeadings,
        style_source_mode: mode,
      });
      setRunId(run_id);
    } catch (e) {
      startedRef.current = false;
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setStarting(false);
    }
  }, [project, source, secondary, normalizeFonts, promoteHeadings, mode, setRunId]);

  const restart = () => {
    startedRef.current = false;
    setRunId(null);
    setError(null);
  };

  if (!source || !secondary) {
    return (
      <WizardLayout activeKey="style" title="Apply style template">
        <NeedsSourcePanel
          message={
            !source
              ? "Upload the content document to continue."
              : "Upload a style template — either a document to copy the look from, or a formatting guideline that describes the rules — on the Upload step."
          }
        />
      </WizardLayout>
    );
  }

  const primaryAction =
    status === "done"
      ? { label: "Export", onClick: () => navigate(pathForStep("export")) }
      : { label: "Continue", onClick: () => {}, disabled: true };

  const isGuideline = interp?.effective_kind === "guideline";

  return (
    <WizardLayout
      activeKey="style"
      title="Apply style template"
      subtitle="We adapt to your template — copying an example's look, or reading a formatting guideline's rules — then apply it to your content."
      primaryAction={primaryAction}
    >
      {status === "error" ? (
        <ErrorPanel
          title="Style transfer failed"
          message={run?.error ?? error ?? "The run did not complete."}
          onRetry={restart}
        />
      ) : status === "done" && run ? (
        <div className="mx-auto max-w-xl">
          <Card className="flex flex-col items-center gap-3 p-8 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100">
              <PaintBrushIcon className="h-6 w-6" />
            </span>
            <h2 className="text-lg font-bold text-ink-800">Style applied</h2>
            <p className="max-w-md text-sm text-ink-500">
              {source.filename} now follows {secondary.filename}
              {isGuideline ? "'s formatting rules" : "'s formatting"}. Continue to Export to download the result.
            </p>
            {run.style_interpretation?.structure && (
              <StructureStats structure={run.style_interpretation.structure} />
            )}
            {run.warnings && run.warnings.length > 0 && (
              <details className="mt-2 w-full text-left">
                <summary className="cursor-pointer text-xs font-semibold text-ink-500 hover:text-ink-700">
                  What was applied ({run.warnings.length})
                </summary>
                <ul className="mt-2 space-y-1 rounded-lg bg-ink-50/70 p-3 text-[12px] text-ink-600">
                  {run.warnings.map((w, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="text-ink-400">•</span>
                      <span>{w}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Card>
        </div>
      ) : runId ? (
        <div className="mx-auto max-w-2xl">
          <RunProgress run={run} events={events} title="Applying styles…" />
        </div>
      ) : (
        <div className="space-y-6">
          {/* Detection + override */}
          <DetectionCard
            interp={interp}
            loading={interpLoading}
            mode={mode}
            onModeChange={setMode}
            templateName={secondary.filename}
          />

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* Left: interpreted rules (guideline) or before/after (example) */}
            <div className="lg:col-span-2">
              {isGuideline && interp?.spec ? (
                <InterpretedRules spec={interp.spec} notes={interp.notes ?? interp.spec.notes ?? []} />
              ) : (
                <div className="nf-card p-5">
                  <header className="mb-4 flex items-center gap-2">
                    <PaintBrushIcon className="h-4 w-4 text-brand-500" />
                    <h2 className="text-sm font-bold text-ink-800">Before → after</h2>
                  </header>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <StyleSnapshot
                      label="Content (current)"
                      filename={source.filename}
                      styling={contentStyle}
                    />
                    <StyleSnapshot
                      label="Template (target)"
                      filename={secondary.filename}
                      styling={templateStyle}
                      accent
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Options + apply */}
            <aside className="space-y-6">
              <div className="nf-card p-5">
                <h3 className="mb-3 text-sm font-bold text-ink-800">Options</h3>
                <div className="space-y-3">
                  <Toggle
                    label="Normalize fonts"
                    hint="Unify body fonts onto the template's."
                    checked={normalizeFonts}
                    onChange={setNormalizeFonts}
                  />
                  <Toggle
                    label="Deep understanding"
                    hint="Use AI to recognize headings, lists and tables in flat documents and rebuild them."
                    checked={promoteHeadings}
                    onChange={setPromoteHeadings}
                  />
                </div>
                {error && (
                  <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
                    {error}
                  </p>
                )}
                <button
                  type="button"
                  onClick={apply}
                  disabled={starting}
                  className="nf-btn-primary mt-4 w-full justify-center"
                >
                  {starting ? (
                    <RefreshIcon className="h-4 w-4 animate-spin" />
                  ) : (
                    <PaintBrushIcon className="h-4 w-4" />
                  )}
                  {starting ? "Starting…" : "Apply style"}
                </button>
              </div>
            </aside>
          </div>
        </div>
      )}
    </WizardLayout>
  );
}

/* ── Detection card + mode override ───────────────────────────────────────── */

const MODES: { value: StyleSourceMode; label: string }[] = [
  { value: "auto", label: "Auto-detect" },
  { value: "guideline", label: "Style guide" },
  { value: "example", label: "Example doc" },
];

function DetectionCard({
  interp,
  loading,
  mode,
  onModeChange,
  templateName,
}: {
  interp: StyleInterpretResponse | null;
  loading: boolean;
  mode: StyleSourceMode;
  onModeChange: (m: StyleSourceMode) => void;
  templateName: string;
}) {
  const guideline = interp?.effective_kind === "guideline";
  const forced = interp?.method === "forced";
  const pct = interp ? Math.round(interp.confidence * 100) : 0;

  return (
    <div className="nf-card p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <span
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl ring-1 ring-inset ${
              guideline
                ? "bg-violet-50 text-violet-600 ring-violet-100"
                : "bg-brand-50 text-brand-600 ring-brand-100"
            }`}
          >
            {guideline ? (
              <SparklesIcon className="h-5 w-5" />
            ) : (
              <DocumentDuplicateIcon className="h-5 w-5" />
            )}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-bold text-ink-800">
                {loading
                  ? "Analyzing style template…"
                  : !interp
                    ? "Style template"
                    : guideline
                      ? "Formatting guideline detected"
                      : "Example document detected"}
              </h2>
              {interp && !loading && (
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                    forced
                      ? "bg-ink-100 text-ink-600"
                      : "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-100"
                  }`}
                >
                  {forced ? "Manual override" : `${pct}% confident`}
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate text-xs text-ink-500">
              <FileTextIcon className="mr-1 inline h-3 w-3" />
              {templateName}
            </p>
            <p className="mt-1.5 max-w-xl text-[13px] text-ink-600">
              {loading
                ? "Reading the document to decide how best to apply it…"
                : interp
                  ? guideline
                    ? `We'll read the rules and apply: ${interp.summary}.`
                    : interp.summary
                  : "Choose how to interpret this template below."}
            </p>
            {interp && !loading && !forced && interp.reason && (
              <p className="mt-1 text-[11.5px] italic text-ink-400">{interp.reason}</p>
            )}
          </div>
        </div>

        {/* Mode override segmented control */}
        <div className="shrink-0">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Interpret as
          </p>
          <div className="inline-flex rounded-lg bg-ink-100 p-0.5">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => onModeChange(m.value)}
                className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                  mode === m.value
                    ? "bg-white text-ink-800 shadow-sm"
                    : "text-ink-500 hover:text-ink-700"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Interpreted rules preview (guideline mode) ───────────────────────────── */

function InterpretedRules({ spec, notes }: { spec: StyleSpec; notes: string[] }) {
  const headings = spec.headings ?? [];
  const palette = spec.colors ?? {};
  const margins = spec.page?.margin_top_in ?? spec.page?.margin_left_in;
  const table = spec.table ?? {};
  const tableHasRules =
    table.header_fill_hex || table.alt_row_fill_hex || table.border_color_hex;

  return (
    <div className="nf-card p-5">
      <header className="mb-4 flex items-center gap-2">
        <SparklesIcon className="h-4 w-4 text-violet-500" />
        <h2 className="text-sm font-bold text-ink-800">Extracted formatting rules</h2>
      </header>

      <div className="space-y-4">
        {/* Body */}
        {(spec.body?.font || spec.body?.size_pt) && (
          <RuleBlock icon={<ParagraphIcon className="h-3.5 w-3.5" />} title="Body text">
            <span className="font-semibold text-ink-800">
              {[spec.body?.font, spec.body?.size_pt ? `${spec.body.size_pt}pt` : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {spec.body?.color_hex && <Swatch hex={spec.body.color_hex} />}
            {spec.body?.alignment && (
              <span className="text-ink-500">{spec.body.alignment}</span>
            )}
          </RuleBlock>
        )}

        {/* Title block (masthead) */}
        {(spec.title?.size_pt || spec.subtitle?.size_pt || spec.metadata?.size_pt) && (
          <RuleBlock icon={<HeadingIcon className="h-3.5 w-3.5" />} title="Title block">
            <ul className="w-full space-y-1.5">
              {(
                [
                  ["Title", spec.title],
                  ["Subtitle", spec.subtitle],
                  ["Metadata", spec.metadata],
                ] as const
              )
                .filter(([, t]) => t && (t.size_pt || t.font))
                .map(([label, t]) => (
                  <li key={label} className="flex flex-wrap items-center gap-1.5">
                    <span className="min-w-[68px] text-xs font-semibold text-ink-700">{label}</span>
                    <span className="text-xs text-ink-600">
                      {[t!.font, t!.size_pt ? `${t!.size_pt}pt` : null]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                    {t!.color_hex && <Swatch hex={t!.color_hex} />}
                    {t!.bold && <Tag>bold</Tag>}
                    {t!.alignment && <Tag>{t!.alignment}</Tag>}
                  </li>
                ))}
            </ul>
          </RuleBlock>
        )}

        {/* Headings */}
        {headings.length > 0 && (
          <RuleBlock icon={<HeadingIcon className="h-3.5 w-3.5" />} title="Headings">
            <ul className="w-full space-y-1.5">
              {headings.map((h, i) => (
                <li key={i} className="flex flex-wrap items-center gap-1.5">
                  <span className="min-w-[68px] text-xs font-semibold text-ink-700">
                    {h.style_name ?? `Heading ${h.level ?? "?"}`}
                  </span>
                  <span className="text-xs text-ink-600">
                    {[h.font, h.size_pt ? `${h.size_pt}pt` : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                  {h.color_hex && <Swatch hex={h.color_hex} />}
                  {h.bold && <Tag>bold</Tag>}
                  {h.bottom_border && <Tag>underline rule</Tag>}
                </li>
              ))}
            </ul>
          </RuleBlock>
        )}

        {/* Tables */}
        {tableHasRules && (
          <RuleBlock icon={<TableIcon className="h-3.5 w-3.5" />} title="Tables">
            {table.header_fill_hex && (
              <span className="inline-flex items-center gap-1 text-xs text-ink-600">
                header <Swatch hex={table.header_fill_hex} />
              </span>
            )}
            {table.alt_row_fill_hex && (
              <span className="inline-flex items-center gap-1 text-xs text-ink-600">
                rows <Swatch hex={table.alt_row_fill_hex} />
              </span>
            )}
            {table.border_color_hex && (
              <span className="inline-flex items-center gap-1 text-xs text-ink-600">
                borders <Swatch hex={table.border_color_hex} />
              </span>
            )}
          </RuleBlock>
        )}

        {/* Page */}
        {(margins != null || spec.page?.orientation) && (
          <RuleBlock icon={<FileTextIcon className="h-3.5 w-3.5" />} title="Page">
            {margins != null && (
              <span className="text-xs text-ink-600">{margins}″ margins</span>
            )}
            {spec.page?.orientation && (
              <span className="text-xs text-ink-500">{spec.page.orientation}</span>
            )}
            {spec.page?.width_in && spec.page?.height_in && (
              <span className="text-xs text-ink-500">
                {spec.page.width_in}″ × {spec.page.height_in}″
              </span>
            )}
          </RuleBlock>
        )}

        {/* Palette */}
        {Object.keys(palette).length > 0 && (
          <RuleBlock icon={<PaintBrushIcon className="h-3.5 w-3.5" />} title="Palette">
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(palette)
                .slice(0, 8)
                .map(([name, hex]) => (
                  <span
                    key={name}
                    className="inline-flex items-center gap-1 rounded-full bg-ink-50 px-1.5 py-0.5 text-[11px] text-ink-600"
                    title={name}
                  >
                    <Swatch hex={hex} />
                    {name.length > 16 ? `#${hex}` : name}
                  </span>
                ))}
            </div>
          </RuleBlock>
        )}

        {/* Notes — captured but not auto-applied */}
        {notes.length > 0 && (
          <div className="rounded-lg bg-amber-50/60 p-3 ring-1 ring-inset ring-amber-100">
            <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-amber-700">
              <InfoIcon className="h-3 w-3" /> Also noted
            </p>
            <ul className="space-y-0.5 text-[12px] text-amber-800/90">
              {notes.slice(0, 5).map((n, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-amber-400">•</span>
                  <span>{n}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function RuleBlock({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink-50 text-ink-500">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-400">
          {title}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">{children}</div>
      </div>
    </div>
  );
}

function Swatch({ hex }: { hex: string }) {
  const clean = hex.replace(/^#/, "");
  return (
    <span
      className="inline-block h-3.5 w-3.5 shrink-0 rounded-sm ring-1 ring-inset ring-ink-200"
      style={{ backgroundColor: `#${clean}` }}
      title={`#${clean}`}
    />
  );
}

function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full bg-ink-100 px-1.5 py-0.5 text-[10px] font-semibold text-ink-600">
      {children}
    </span>
  );
}

/* ── Recognised-structure stats (result view) ─────────────────────────────── */

function StructureStats({ structure }: { structure: RecognisedStructure }) {
  const stats: { label: string; value: number }[] = [
    { label: "Headings", value: structure.headings ?? 0 },
    { label: "List items", value: structure.list_items ?? 0 },
    { label: "Tables", value: structure.tables ?? 0 },
  ];
  if (!stats.some((s) => s.value > 0)) return null;
  return (
    <div className="mt-1 w-full">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
        Understood your document
      </p>
      <div className="grid grid-cols-3 gap-2">
        {stats.map((s) => (
          <div
            key={s.label}
            className="rounded-lg bg-ink-50/70 px-2 py-2 text-center ring-1 ring-inset ring-ink-100"
          >
            <div className="text-lg font-bold text-ink-800">{s.value}</div>
            <div className="text-[11px] text-ink-500">{s.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Example-mode before/after snapshot ───────────────────────────────────── */

function StyleSnapshot({
  label,
  filename,
  styling,
  accent,
}: {
  label: string;
  filename: string;
  styling: DocumentStyling | null;
  accent?: boolean;
}) {
  const fonts = fontsOf(styling);
  const page = styling?.page_style;
  return (
    <div
      className={`rounded-xl border p-4 ${
        accent ? "border-brand-200 bg-brand-50/50" : "border-ink-100 bg-ink-50/40"
      }`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">
        {label}
      </p>
      <p className="mb-3 flex items-center gap-1.5 truncate text-sm font-bold text-ink-800">
        <FileTextIcon className="h-4 w-4 shrink-0 text-ink-400" />
        {filename}
      </p>
      {styling ? (
        <dl className="space-y-1.5 text-[12.5px]">
          <Row label="Page" value={`${page!.width_inches}″ × ${page!.height_inches}″`} />
          <Row
            label="Margins"
            value={`${page!.margins.top_inches}/${page!.margins.left_inches}″`}
          />
          <Row label="Para styles" value={String(Object.keys(styling.paragraph_styles).length)} />
          <div>
            <dt className="text-ink-500">Fonts</dt>
            <dd className="mt-1 flex flex-wrap gap-1">
              {fonts.length ? (
                fonts.slice(0, 6).map((f) => (
                  <span
                    key={f}
                    className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-ink-700 ring-1 ring-inset ring-ink-100"
                  >
                    {f}
                  </span>
                ))
              ) : (
                <span className="text-ink-400">—</span>
              )}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="text-[12px] text-ink-400">Loading styling…</p>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-ink-500">{label}</dt>
      <dd className="font-semibold text-ink-800">{value}</dd>
    </div>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex w-full items-start gap-3 rounded-lg bg-ink-50/60 px-3 py-2.5 text-left transition-colors hover:bg-ink-50"
    >
      <span
        className={`mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full px-0.5 transition-colors ${
          checked ? "bg-brand-500" : "bg-ink-300"
        }`}
      >
        <span
          className={`h-4 w-4 rounded-full bg-white transition-transform ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] font-semibold text-ink-800">{label}</span>
        <span className="block text-[12px] text-ink-500">{hint}</span>
      </span>
    </button>
  );
}

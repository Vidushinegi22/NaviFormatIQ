import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { useNavigate } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import { RunProgress } from "@/components/wizard/RunProgress";
import { NeedsSourcePanel, ErrorPanel } from "@/components/wizard/States";
import { Toggle } from "@/components/wizard/Toggle";
import {
  ScoreGauge,
  DimensionBars,
  SectionBars,
  SeverityBar,
  scorePct,
  SEVERITY_HEX,
} from "@/components/wizard/Charts";
import {
  ClipboardListIcon,
  RefreshIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  SearchIcon,
  XIcon,
  MessageSquareIcon,
} from "@/components/icons/Icons";
import { Button } from "@/components/ui/button";
import { useDocument } from "@/context/DocumentContext";
import { startCompliance, listGuidelines, getGuideline, ApiError } from "@/lib/api";
import { useRun } from "@/lib/useRun";
import { pathForStep } from "@/lib/stepRouting";
import type {
  ComplianceDimension,
  ComplianceFinding,
  ComplianceResult,
  FindingSeverity,
  FindingStatus,
  Guideline,
  GuidelineDetail,
  RunDetail,
} from "@/types/api";

type Triage = "open" | "resolved" | "ignored";

const DIMENSIONS: ComplianceDimension[] = ["content", "structure", "formatting", "style", "tone"];

// Full literal class strings — Tailwind JIT cannot see interpolated names.
const STATUS_STYLE: Record<FindingStatus, { label: string; badge: string; dot: string; dotRing: string; accent: string }> = {
  compliant:     { label: "Compliant",  badge: "bg-emerald-50 text-emerald-700 ring-emerald-200/60", dot: "bg-emerald-500", dotRing: "ring-emerald-200", accent: "border-l-emerald-400" },
  partial:       { label: "Partial",    badge: "bg-amber-50 text-amber-700 ring-amber-200/60",       dot: "bg-amber-500",   dotRing: "ring-amber-200",   accent: "border-l-amber-400"   },
  non_compliant: { label: "Gap",        badge: "bg-rose-50 text-rose-700 ring-rose-200/60",          dot: "bg-rose-500",    dotRing: "ring-rose-200",    accent: "border-l-rose-400"    },
  not_applicable:{ label: "N/A",        badge: "bg-slate-50 text-slate-500 ring-slate-200/60",       dot: "bg-slate-300",   dotRing: "ring-slate-200",   accent: "border-l-slate-300"   },
};
const SEVERITY_STYLE: Record<FindingSeverity, { label: string; badge: string }> = {
  critical: { label: "Critical", badge: "bg-rose-50 text-rose-700 ring-rose-200/60" },
  major:    { label: "Major",    badge: "bg-orange-50 text-orange-700 ring-orange-200/60" },
  minor:    { label: "Minor",    badge: "bg-amber-50 text-amber-700 ring-amber-200/60" },
  info:     { label: "Info",     badge: "bg-blue-50 text-blue-700 ring-blue-200/60" },
};
const SEV_RANK: Record<FindingSeverity, number> = { critical: 0, major: 1, minor: 2, info: 3 };

/** Map a legacy run (flags/coverage/score) onto the rich result shape. */
function legacyResult(run: RunDetail): ComplianceResult | null {
  if (!run.flags?.length && run.compliance_score == null && !run.coverage) return null;
  const kindToSeverity: Record<string, FindingSeverity> = {
    missing: "critical", length: "minor", format: "minor", guidance: "info",
  };
  const findings: ComplianceFinding[] = (run.flags ?? []).map((f, i) => ({
    id: `legacy-${i}`,
    section: f.slot_id,
    requirement_title: f.note || f.slot_id,
    dimension: "content",
    status: "non_compliant",
    severity: kindToSeverity[f.kind] ?? "minor",
  }));
  let overall = run.compliance_score ?? 0;
  if (run.compliance_score == null && run.coverage?.required_total) {
    const missing = run.coverage.missing?.length ?? 0;
    overall = (run.coverage.required_total - missing) / run.coverage.required_total;
  }
  const severity_counts: Partial<Record<FindingSeverity, number>> = {};
  for (const f of findings) severity_counts[f.severity] = (severity_counts[f.severity] ?? 0) + 1;
  return {
    overall_score: overall,
    per_dimension: {},
    per_section: [],
    severity_counts,
    findings,
    summary: null,
  };
}

const topOf = (section?: string | null) => (section ? section.split(".")[0] : "—");

/**
 * Compliance page (compliance-check flow).
 *
 * Audits the uploaded document against the selected pre-loaded guideline
 * (e.g. ICH E3) across content/structure/formatting/style/tone, then renders a
 * rich dashboard: overall score, per-dimension + per-section + severity charts,
 * and grouped findings with evidence, guideline citations and suggested fixes.
 */
export function CompliancePage() {
  const navigate = useNavigate();
  const {
    project,
    source,
    runId,
    domainId,
    guidelineId,
    setRunId,
    setGuidelineId,
    markComplete,
    setCurrentStep,
  } = useDocument();
  const { run, events, status } = useRun(runId);

  const [detail, setDetail] = useState<GuidelineDetail | null>(null);
  const [fallbackGuidelines, setFallbackGuidelines] = useState<Guideline[]>([]);
  const [dims, setDims] = useState<Set<ComplianceDimension>>(new Set(DIMENSIONS));
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [triage, setTriage] = useState<Record<string, Triage>>({});
  const startedRef = useRef(false);

  useEffect(() => {
    setCurrentStep("compliance");
  }, [setCurrentStep]);

  // Load the selected guideline's detail for the pre-run "audit target" card.
  useEffect(() => {
    if (!guidelineId) return;
    getGuideline(guidelineId).then(setDetail).catch(() => setDetail(null));
  }, [guidelineId]);

  // If we arrived without a selection (deep-link), offer a guideline picker.
  useEffect(() => {
    if (guidelineId || status === "done") return;
    listGuidelines(domainId ?? "pharma")
      .then((gs) => {
        setFallbackGuidelines(gs);
        if (gs.length) {
          const e3 = gs.find((g) => g.code === "ICH-E3");
          setGuidelineId((e3 ?? gs[0]).id);
        }
      })
      .catch(() => {});
  }, [guidelineId, domainId, status, setGuidelineId]);

  useEffect(() => {
    if (status === "done") markComplete("compliance");
  }, [status, markComplete]);

  const runCheck = useCallback(async () => {
    if (!project || !source || !guidelineId || startedRef.current) return;
    startedRef.current = true;
    setStarting(true);
    setError(null);
    try {
      const subset = dims.size < DIMENSIONS.length ? Array.from(dims) : undefined;
      const { run_id } = await startCompliance(project.id, {
        draft_document_id: source.documentId,
        domain_id: domainId ?? "pharma",
        guideline_id: guidelineId,
        dimensions: subset,
        mode: "check",
      });
      setRunId(run_id);
    } catch (e) {
      startedRef.current = false;
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setStarting(false);
    }
  }, [project, source, guidelineId, domainId, dims, setRunId]);

  const restart = () => {
    startedRef.current = false;
    setRunId(null);
    setTriage({});
    setError(null);
  };

  const result: ComplianceResult | null = useMemo(
    () => (run?.compliance ?? (run ? legacyResult(run) : null)),
    [run]
  );
  const guidelineCode = result?.guideline?.code ?? detail?.code ?? "ICH-E3";

  if (!source) {
    return (
      <WizardLayout activeKey="compliance" title="Compliance audit">
        <NeedsSourcePanel />
      </WizardLayout>
    );
  }

  const primaryAction =
    status === "done"
      ? { label: "Export", onClick: () => navigate(pathForStep("export")) }
      : { label: "Continue", onClick: () => {}, disabled: true };

  return (
    <WizardLayout
      activeKey="compliance"
      title="Compliance audit"
      subtitle="Audit the document against the selected guideline across content, structure, formatting, style and tone."
      primaryAction={primaryAction}
    >
      {status === "error" ? (
        <ErrorPanel
          title="Compliance audit failed"
          message={run?.error ?? error ?? "The run did not complete."}
          onRetry={restart}
        />
      ) : runId && status !== "done" ? (
        <div className="mx-auto max-w-2xl">
          <RunProgress run={run} events={events} title={`Auditing against ${guidelineCode}…`} />
        </div>
      ) : status === "done" && run && result ? (
        <Dashboard
          result={result}
          guidelineCode={guidelineCode}
          triage={triage}
          setTriage={setTriage}
          onAsk={(f) =>
            navigate(pathForStep("chat"), {
              state: {
                prefill: `How do I fix "${f.requirement_title}" in section ${f.section ?? ""} to comply with ${guidelineCode}?`,
              },
            })
          }
        />
      ) : (
        <PreRun
          filename={source.filename}
          detail={detail}
          fallbackGuidelines={fallbackGuidelines}
          guidelineId={guidelineId}
          onPickGuideline={setGuidelineId}
          dims={dims}
          setDims={setDims}
          starting={starting}
          error={error}
          onRun={runCheck}
        />
      )}
    </WizardLayout>
  );
}

/* ── pre-run configuration ──────────────────────────────────────────────── */
function PreRun({
  filename,
  detail,
  fallbackGuidelines,
  guidelineId,
  onPickGuideline,
  dims,
  setDims,
  starting,
  error,
  onRun,
}: {
  filename: string;
  detail: GuidelineDetail | null;
  fallbackGuidelines: Guideline[];
  guidelineId: string | null;
  onPickGuideline: (id: string) => void;
  dims: Set<ComplianceDimension>;
  setDims: (s: Set<ComplianceDimension>) => void;
  starting: boolean;
  error: string | null;
  onRun: () => void;
}) {
  const toggle = (d: ComplianceDimension) => {
    const next = new Set(dims);
    if (next.has(d)) next.delete(d);
    else next.add(d);
    if (next.size === 0) return; // keep at least one
    setDims(next);
  };
  return (
    <div className="w-full">
      <div className="nf-card p-6 shadow-sm ring-1 ring-black/5">
        <div className="flex flex-col lg:flex-row lg:items-start gap-8">
          
          <div className="flex-1 space-y-6">
            <header>
              <h2 className="text-xl font-bold text-ink-900">Run Compliance Audit</h2>
              <p className="mt-1.5 text-sm text-ink-500">
                Audit <span className="font-medium text-ink-700">{filename}</span> against the guideline.
              </p>
            </header>

            {guidelineId && detail ? (
              <div className="rounded-xl border border-ink-100 bg-gradient-to-br from-ink-50/80 to-white p-5 shadow-sm">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-brand-600">Audit Target</p>
                <p className="mt-1 text-sm font-bold text-ink-900">
                  {detail.code} <span className="text-ink-400 font-normal mx-1">—</span> {detail.title}
                </p>
                {detail.version && <p className="mt-0.5 text-[12px] text-ink-500">{detail.version}</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                  {(detail.dimension_coverage ?? []).map((d) => (
                    <span key={d} className="rounded-full bg-white px-2 py-0.5 text-[11px] font-semibold capitalize text-ink-700 shadow-sm ring-1 ring-inset ring-ink-200/50">
                      {d}
                    </span>
                  ))}
                  {detail.requirement_count != null && (
                    <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-200/50">
                      {detail.requirement_count} requirements
                    </span>
                  )}
                </div>
              </div>
            ) : (
              <div>
                <label htmlFor="cmp-g" className="mb-1.5 block text-[12px] font-semibold uppercase tracking-wider text-ink-500">
                  Guideline
                </label>
                <select
                  id="cmp-g"
                  value={guidelineId ?? ""}
                  onChange={(e) => onPickGuideline(e.target.value)}
                  className="w-full rounded-lg border border-ink-200 bg-white px-4 py-3 text-sm font-medium text-ink-800 shadow-sm transition-shadow focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
                >
                  {fallbackGuidelines.length === 0 && <option value="">Loading guidelines…</option>}
                  {fallbackGuidelines.map((g) => (
                    <option key={g.id} value={g.id}>{g.code} — {g.title}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <div className="flex-1 space-y-6 lg:border-l lg:border-ink-100 lg:pl-8">
            <div className="rounded-xl border border-ink-100 bg-ink-50/30 p-5">
              <p className="mb-3 text-[12px] font-semibold uppercase tracking-wider text-ink-500">Dimensions to check</p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {DIMENSIONS.map((d) => (
                  <Toggle key={d} label={d[0].toUpperCase() + d.slice(1)} checked={dims.has(d)} onChange={() => toggle(d)} />
                ))}
              </div>
            </div>

            {error && <p className="rounded-xl bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700 ring-1 ring-inset ring-rose-200/50">{error}</p>}
            
            <div className="pt-2">
              <Button
                type="button"
                size="lg"
                onClick={onRun}
                disabled={starting || !guidelineId}
                className="w-full text-[15px] tracking-wide shadow-md transition-all hover:-translate-y-0.5 hover:shadow-lg disabled:hover:translate-y-0 disabled:hover:shadow-md"
              >
                {starting && <RefreshIcon className="h-5 w-5 animate-spin" />}
                {starting ? "Starting…" : "Run Compliance Audit"}
              </Button>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}

/* ── results dashboard ──────────────────────────────────────────────────── */
type StatusView = FindingStatus | "all" | "issues";

const SEV_CHIPS: (FindingSeverity | "all")[] = ["all", "critical", "major", "minor", "info"];
const STATUS_OPTIONS: { value: StatusView; label: string }[] = [
  { value: "issues", label: "Open issues" },
  { value: "non_compliant", label: "Gaps only" },
  { value: "partial", label: "Partial only" },
  { value: "compliant", label: "Compliant only" },
  { value: "not_applicable", label: "Not applicable" },
  { value: "all", label: "All checks" },
];
const INITIAL_PER_SECTION = 6;

function Dashboard({
  result,
  guidelineCode,
  triage,
  setTriage,
  onAsk,
}: {
  result: ComplianceResult;
  guidelineCode: string;
  triage: Record<string, Triage>;
  setTriage: Dispatch<SetStateAction<Record<string, Triage>>>;
  onAsk: (f: ComplianceFinding) => void;
}) {
  const [dimFilter, setDimFilter] = useState<ComplianceDimension | "all">("all");
  const [sevFilter, setSevFilter] = useState<FindingSeverity | "all">("all");
  const [statusView, setStatusView] = useState<StatusView>("issues");
  const [query, setQuery] = useState("");
  const [sectionFocus, setSectionFocus] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const q = query.trim().toLowerCase();

  const matchesBase = useCallback(
    (f: ComplianceFinding) =>
      (dimFilter === "all" || f.dimension === dimFilter) &&
      (!sectionFocus || topOf(f.section) === sectionFocus) &&
      (!q ||
        f.requirement_title.toLowerCase().includes(q) ||
        (f.section ?? "").toLowerCase().includes(q)),
    [dimFilter, sectionFocus, q]
  );
  const matchesStatus = useCallback(
    (f: ComplianceFinding) => {
      if (statusView === "all") return true;
      if (statusView === "issues") return f.status === "non_compliant" || f.status === "partial";
      return f.status === statusView;
    },
    [statusView]
  );

  const filtered = useMemo(
    () =>
      result.findings.filter(
        (f) =>
          matchesBase(f) &&
          matchesStatus(f) &&
          (sevFilter === "all" || f.severity === sevFilter)
      ),
    [result.findings, matchesBase, matchesStatus, sevFilter]
  );

  // Counts for the severity chips — respect base + status filters, ignore severity.
  const sevCounts = useMemo(() => {
    const c: Record<string, number> = { all: 0, critical: 0, major: 0, minor: 0, info: 0 };
    for (const f of result.findings) {
      if (matchesBase(f) && matchesStatus(f)) {
        c[f.severity] = (c[f.severity] ?? 0) + 1;
        c.all += 1;
      }
    }
    return c;
  }, [result.findings, matchesBase, matchesStatus]);

  // Group filtered findings by top-level section, ordered numerically.
  const groups = useMemo(() => {
    const by: Record<string, ComplianceFinding[]> = {};
    for (const f of filtered) (by[topOf(f.section)] ??= []).push(f);
    const order = Object.keys(by).sort((a, b) =>
      a === "—" ? 1 : b === "—" ? -1 : Number(a) - Number(b)
    );
    return order.map((sec) => ({
      sec,
      title: result.per_section.find((s) => s.section === sec)?.title ?? "",
      score: result.per_section.find((s) => s.section === sec)?.score ?? null,
      findings: by[sec].sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]),
    }));
  }, [filtered, result.per_section]);

  const openIssues = useMemo(
    () =>
      result.findings.filter(
        (f) =>
          (f.status === "non_compliant" || f.status === "partial") &&
          (triage[f.id] ?? "open") === "open"
      ).length,
    [result.findings, triage]
  );

  const anyFilter =
    dimFilter !== "all" || sevFilter !== "all" || statusView !== "issues" || q !== "" || sectionFocus !== null;
  const reset = () => {
    setDimFilter("all");
    setSevFilter("all");
    setStatusView("issues");
    setQuery("");
    setSectionFocus(null);
    setExpanded(new Set());
  };
  const toggleSection = (sec: string) =>
    setExpanded((p) => {
      const n = new Set(p);
      if (n.has(sec)) n.delete(sec);
      else n.add(sec);
      return n;
    });

  const miniSelectCls =
    "rounded-lg border border-ink-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-ink-700 shadow-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 transition-shadow";

  return (
    <div className="space-y-5">
      {/* ── top summary band ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 lg:items-stretch">

        {/* Score card */}
        <section className="nf-card overflow-hidden lg:col-span-4 flex flex-col">
          <div className="border-b border-ink-100 px-5 py-3.5 flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              <ClipboardListIcon className="h-4 w-4 text-brand-500" />
              <h2 className="text-sm font-bold text-ink-800">Compliance score</h2>
            </div>
            <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-[11px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-200/60">
              {guidelineCode}
            </span>
          </div>
          {/* gauge — never scrolls */}
          <div className="px-5 pt-5 shrink-0">
            <ScoreGauge
              value={result.overall_score}
              caption={(result.status_label ?? "").toUpperCase() || undefined}
            />
          </div>
          {/* summary — scrolls if long */}
          {result.summary && (
            <div className="flex-1 overflow-y-auto px-5 pb-5 pt-4">
              <div className="border-t border-ink-100 pt-4">
                <p className="text-[12.5px] leading-relaxed text-ink-500">
                  {result.summary}
                </p>
              </div>
            </div>
          )}
          {!result.summary && <div className="flex-1" />}
        </section>

        {/* Dimension + severity card */}
        <section className="nf-card overflow-hidden lg:col-span-4 flex flex-col">
          <div className="border-b border-ink-100 px-5 py-3.5 shrink-0">
            <h2 className="text-sm font-bold text-ink-800">Scores by dimension</h2>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
            <DimensionBars data={result.per_dimension} />
            <div>
              <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-ink-400">Open issues by severity</h3>
              <SeverityBar counts={result.severity_counts} />
            </div>
          </div>
        </section>

        {/* Section compliance card */}
        <section className="nf-card overflow-hidden lg:col-span-4 flex flex-col">
          <div className="border-b border-ink-100 px-5 py-3.5 shrink-0 flex items-center justify-between">
            <h2 className="text-sm font-bold text-ink-800">Compliance by section</h2>
            <span className="text-[11px] text-ink-400">Click to filter</span>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <SectionBars
              data={result.per_section}
              onSelect={(s) => setSectionFocus((prev) => (prev === s ? null : s))}
            />
          </div>
        </section>
      </div>

      {/* ── findings list ── */}
      <section className="nf-card overflow-hidden">
        {/* findings header + toolbar */}
        <div className="border-b border-ink-100 px-5 py-4 space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <h2 className="text-sm font-bold text-ink-800">Findings</h2>
              <span className="text-[12px] text-ink-400">
                {openIssues} open · {result.findings.length} checked
                {filtered.length !== result.findings.length && ` · ${filtered.length} shown`}
              </span>
            </div>
            {anyFilter && (
              <button
                type="button"
                onClick={reset}
                className="text-[12px] font-semibold text-brand-600 hover:text-brand-700 transition-colors"
              >
                Reset filters
              </button>
            )}
          </div>

          {/* row: search + status + dimension */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1">
              <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-300" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search findings…"
                className="w-full rounded-lg border border-ink-200 bg-white py-1.5 pl-8 pr-8 text-[12.5px] text-ink-700 shadow-sm placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 transition-shadow"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-ink-300 hover:text-ink-600"
                  aria-label="Clear search"
                >
                  <XIcon className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <select
              value={statusView}
              onChange={(e) => setStatusView(e.target.value as StatusView)}
              className={miniSelectCls}
              aria-label="Filter by status"
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <select
              value={dimFilter}
              onChange={(e) => setDimFilter(e.target.value as ComplianceDimension | "all")}
              className={`${miniSelectCls} capitalize`}
              aria-label="Filter by dimension"
            >
              <option value="all">All dimensions</option>
              {DIMENSIONS.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>

          {/* row: severity chips + section focus */}
          <div className="flex flex-wrap items-center gap-1.5">
            {SEV_CHIPS.map((s) => {
              const active = sevFilter === s;
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSevFilter(s)}
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-semibold transition-colors ${
                    active
                      ? "bg-ink-800 text-white shadow-sm"
                      : "bg-ink-50 text-ink-600 ring-1 ring-inset ring-ink-200/70 hover:bg-ink-100"
                  }`}
                >
                  {s !== "all" && (
                    <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: SEVERITY_HEX[s] }} />
                  )}
                  {s === "all" ? "All severities" : SEVERITY_STYLE[s].label}
                  <span className={`tabular-nums ${active ? "text-white/70" : "text-ink-400"}`}>
                    {sevCounts[s] ?? 0}
                  </span>
                </button>
              );
            })}
            {sectionFocus && (
              <button
                type="button"
                onClick={() => setSectionFocus(null)}
                className="ml-auto inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-[11.5px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-200/60 hover:bg-brand-100 transition-colors"
              >
                Section {sectionFocus}
                <XIcon className="h-3 w-3 opacity-70" />
              </button>
            )}
          </div>
        </div>

        {/* findings body */}
        <div className="px-5 py-4">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-xl bg-ink-50/60 px-4 py-10 text-center ring-1 ring-inset ring-ink-100">
              <p className="text-sm font-semibold text-ink-700">No findings match these filters.</p>
              <p className="text-[12px] text-ink-500">
                {anyFilter ? "Try resetting the filters to see all checks." : "Nothing to show."}
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {groups.map((g) => {
                const isExp = expanded.has(g.sec);
                const shown = isExp ? g.findings : g.findings.slice(0, INITIAL_PER_SECTION);
                const more = g.findings.length - shown.length;
                return (
                  <div key={g.sec}>
                    {/* Section header */}
                    <div className="mb-3 flex items-center gap-3">
                      <div className="flex items-baseline gap-2 min-w-0">
                        <span className="text-[11px] font-bold uppercase tracking-widest text-ink-400">
                          Section {g.sec}
                        </span>
                        {g.title && (
                          <span className="text-[12px] font-semibold text-ink-600 truncate">{g.title}</span>
                        )}
                        <span className="text-[11px] text-ink-300 tabular-nums">{g.findings.length}</span>
                      </div>
                      <div className="flex-1 h-px bg-ink-100" />
                      {g.score != null && (
                        <span
                          className={`shrink-0 text-[11px] font-bold tabular-nums px-2 py-0.5 rounded-full ${
                            g.score >= 0.75
                              ? "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200/60"
                              : g.score >= 0.5
                              ? "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200/60"
                              : "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200/60"
                          }`}
                        >
                          {scorePct(g.score)}%
                        </span>
                      )}
                    </div>

                    <ul className="space-y-2.5">
                      {shown.map((f) => (
                        <FindingCard
                          key={f.id}
                          f={f}
                          triage={triage[f.id] ?? "open"}
                          onTriage={(s) => setTriage((p) => ({ ...p, [f.id]: s }))}
                          onAsk={() => onAsk(f)}
                        />
                      ))}
                    </ul>

                    {(more > 0 || (isExp && g.findings.length > INITIAL_PER_SECTION)) && (
                      <button
                        type="button"
                        onClick={() => toggleSection(g.sec)}
                        className="mt-2 inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[12px] font-semibold text-brand-600 hover:bg-brand-50 hover:text-brand-700 transition-colors"
                      >
                        {more > 0 ? (
                          <>
                            <ChevronDownIcon className="h-3.5 w-3.5" />
                            Show {more} more in this section
                          </>
                        ) : (
                          <>
                            <ChevronRightIcon className="h-3.5 w-3.5 rotate-90" />
                            Show fewer
                          </>
                        )}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function FindingCard({
  f,
  triage,
  onTriage,
  onAsk,
}: {
  f: ComplianceFinding;
  triage: Triage;
  onTriage: (s: Triage) => void;
  onAsk: () => void;
}) {
  const [open, setOpen] = useState(false);
  const st = STATUS_STYLE[f.status];
  const sev = SEVERITY_STYLE[f.severity];
  const resolved = triage !== "open";
  const hasDetail = f.evidence || f.citation?.quote || f.rationale || f.suggested_fix;

  return (
    <li
      className={`overflow-hidden rounded-xl border border-l-[3px] border-ink-100 transition-all duration-150 ${st.accent} ${
        resolved ? "bg-ink-50/50 opacity-60" : "bg-white shadow-sm hover:border-ink-200 hover:shadow"
      }`}
    >
      {/* main row */}
      <div className="flex items-start gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ring-inset ${sev.badge}`}
            >
              {sev.label}
            </span>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ring-inset ${st.badge}`}
            >
              {st.label}
            </span>
            <span className="text-[10px] font-semibold capitalize text-ink-400">
              {f.dimension}
            </span>
          </div>
          <p className="mt-1.5 text-[13px] font-semibold text-ink-800 leading-snug">{f.requirement_title}</p>
          {f.doc_location && (
            <p className="mt-0.5 text-[11.5px] text-ink-400">{f.doc_location}</p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1 mt-0.5">
          {hasDetail && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="rounded-md p-1 text-ink-300 hover:bg-ink-100 hover:text-ink-600 transition-colors"
              aria-label={open ? "Collapse" : "Expand"}
            >
              {open ? <ChevronDownIcon className="h-4 w-4" /> : <ChevronRightIcon className="h-4 w-4" />}
            </button>
          )}
        </div>
      </div>

      {/* expanded detail */}
      {open && hasDetail && (
        <div className="space-y-3 border-t border-ink-100 px-4 pb-4 pt-3">
          {f.evidence && (
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-wider text-ink-400 mb-1">Evidence in document</p>
              <p className="text-[12.5px] italic text-ink-600 leading-relaxed">"{f.evidence}"</p>
            </div>
          )}
          {f.rationale && (
            <p className="text-[12.5px] text-ink-600 leading-relaxed">{f.rationale}</p>
          )}
          {f.citation?.quote && (
            <div className="rounded-lg border-l-2 border-brand-400 bg-brand-50/50 px-4 py-2.5">
              <p className="text-[10.5px] font-bold uppercase tracking-wider text-brand-600 mb-1">
                {f.citation.guideline_section ? `Guideline ${f.citation.guideline_section}` : "Guideline"}
              </p>
              <p className="text-[12.5px] text-ink-700 leading-relaxed">{f.citation.quote}</p>
            </div>
          )}
          {f.suggested_fix && (
            <div className="rounded-lg bg-emerald-50 px-4 py-2.5 ring-1 ring-inset ring-emerald-100">
              <p className="text-[10.5px] font-bold uppercase tracking-wider text-emerald-600 mb-1">Suggested fix</p>
              <p className="text-[12.5px] text-emerald-900 leading-relaxed">{f.suggested_fix}</p>
            </div>
          )}
        </div>
      )}

      {/* action bar */}
      <div className="flex items-center justify-between gap-2 border-t border-ink-100 bg-ink-50/40 px-4 py-2">
        <button
          type="button"
          onClick={onAsk}
          className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-brand-600 hover:text-brand-700 transition-colors"
        >
          <MessageSquareIcon className="h-3.5 w-3.5" />
          Ask about this
        </button>
        <div className="flex items-center gap-1.5">
          {(["resolved", "ignored"] as Triage[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onTriage(triage === s ? "open" : s)}
              className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-all duration-150 ${
                triage === s
                  ? "bg-brand-500 text-white shadow-sm"
                  : "bg-white text-ink-500 ring-1 ring-inset ring-ink-200 hover:ring-ink-300 hover:text-ink-700"
              }`}
            >
              {s === "resolved" ? "Resolve" : "Ignore"}
            </button>
          ))}
        </div>
      </div>
    </li>
  );
}

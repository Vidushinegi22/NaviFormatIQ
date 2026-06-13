import { useEffect } from "react";
import { Link } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import { ExportCatalog } from "@/components/wizard/ExportCatalog";
import { RunProgress } from "@/components/wizard/RunProgress";
import { NeedsSourcePanel, ErrorPanel, WarningsList } from "@/components/wizard/States";
import { DownloadIcon, FileTextIcon } from "@/components/icons/Icons";
import { useDocument } from "@/context/DocumentContext";
import { useRun } from "@/lib/useRun";
import { pathForStep } from "@/lib/stepRouting";
import { sectionChanged } from "@/lib/docBlocks";
import type { RunDetail } from "@/types/api";

/**
 * Export page — the terminal step for every workflow.
 *
 * Surfaces the finished run as a backend-driven export hub: the document in
 * Word or PDF, its styling & formatting details, the extracted content, and
 * any change / compliance report the run produced.
 */
export function ExportPage() {
  const { source, runId, markComplete, setCurrentStep } = useDocument();
  const { run, events, status } = useRun(runId);

  // Track current step for resume-on-reopen
  useEffect(() => {
    setCurrentStep("export");
  }, [setCurrentStep]);

  useEffect(() => {
    if (status === "done") markComplete("export");
  }, [status, markComplete]);

  if (!source) {
    return (
      <WizardLayout activeKey="export" title="Export" hideFooter>
        <NeedsSourcePanel />
      </WizardLayout>
    );
  }

  if (!runId) {
    return (
      <WizardLayout activeKey="export" title="Export">
        <div className="mx-auto max-w-xl">
          <div className="nf-card flex flex-col items-center gap-3 p-8 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100">
              <DownloadIcon className="h-6 w-6" />
            </span>
            <h2 className="text-lg font-bold text-ink-800">Nothing to export yet</h2>
            <p className="max-w-md text-sm text-ink-500">
              Complete the workflow's previous steps to produce an output to export.
            </p>
            <Link to={pathForStep("extract")} className="nf-btn-primary">
              Back to the workflow
            </Link>
          </div>
        </div>
      </WizardLayout>
    );
  }

  if (status === "error") {
    return (
      <WizardLayout activeKey="export" title="Export" hideFooter>
        <ErrorPanel
          title="Run failed"
          message={run?.error ?? "The run did not complete, so there is nothing to export."}
        />
      </WizardLayout>
    );
  }

  if (status !== "done" || !run) {
    return (
      <WizardLayout activeKey="export" title="Export" hideFooter>
        <div className="mx-auto max-w-2xl">
          <RunProgress run={run} events={events} title="Finishing up…" />
        </div>
      </WizardLayout>
    );
  }

  const summaryRows = buildSummaryRows(run, source.filename);

  return (
    <WizardLayout
      activeKey="export"
      title="Export"
      subtitle="Your results are ready. Download them in the format you need — or continue to Chat to refine further."
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Download hub */}
        <div className="lg:col-span-2">
          <div className="nf-card p-6">
            <header className="mb-5 flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100">
                <DownloadIcon className="h-6 w-6" />
              </span>
              <div>
                <h2 className="text-base font-bold text-ink-800">Ready to download</h2>
                <p className="text-[13px] text-ink-500">
                  Word or PDF, the styling &amp; formatting, the extracted content, and more.
                </p>
              </div>
            </header>

            <ExportCatalog runId={run.id} />

            <WarningsList warnings={run.warnings} />
          </div>
        </div>

        {/* Summary */}
        <aside>
          <div className="nf-card p-5">
            <h3 className="mb-3 text-sm font-bold text-ink-800">Summary</h3>
            <dl className="space-y-2.5 text-[13px]">
              {summaryRows.map((row) => (
                <SummaryRow
                  key={row.label}
                  icon={row.icon ? <FileTextIcon className="h-4 w-4" /> : undefined}
                  label={row.label}
                  value={row.value}
                />
              ))}
            </dl>
          </div>
        </aside>
      </div>
    </WizardLayout>
  );
}

type SummaryRowData = { label: string; value: string; icon?: boolean };

/** Human-readable name for the task that produced this output. */
function humanizeTask(flow: string, mode?: string | null): string {
  if (flow === "regenerate") return "New version";
  if (flow === "style") return "Style update";
  if (flow === "compliance") return mode === "check" ? "Compliance check" : "Compliance fix";
  return flow;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * Build a compact, flow-aware summary: always the document + task, then only
 * the few facts that are meaningful for *this* kind of run. No raw warning
 * counts or out-of-context compliance scores.
 */
function buildSummaryRows(run: RunDetail, filename: string): SummaryRowData[] {
  const rows: SummaryRowData[] = [
    { label: "Document", value: filename, icon: true },
    { label: "Task", value: humanizeTask(run.flow, run.mode) },
  ];

  if (run.flow === "compliance") {
    const findings = run.compliance?.findings.length ?? run.flags.length;
    rows.push({ label: "Findings", value: findings ? String(findings) : "None" });
    const critical = run.compliance?.severity_counts?.critical ?? 0;
    if (critical > 0) rows.push({ label: "Critical issues", value: String(critical) });
    const guideline = run.compliance?.guideline?.code || run.compliance?.guideline?.title;
    if (guideline) rows.push({ label: "Guideline", value: guideline });
    return rows;
  }

  if (run.flow === "style") {
    const s = run.style_interpretation?.structure;
    const bits: string[] = [];
    if (s?.headings) bits.push(plural(s.headings, "heading"));
    if (s?.list_items) bits.push(plural(s.list_items, "list item"));
    if (s?.tables) bits.push(plural(s.tables, "table"));
    if (bits.length) rows.push({ label: "Restyled", value: bits.join(" · ") });
    const kind = run.style_interpretation?.mode_used;
    if (kind) rows.push({ label: "Style source", value: kind === "guideline" ? "Guideline" : "Example" });
    return rows;
  }

  // regenerate (new version)
  const profile = run.doc_profile;
  if (profile?.doc_type) rows.push({ label: "Type", value: profile.doc_type });
  if (profile?.version && profile?.new_version && profile.version !== profile.new_version) {
    rows.push({ label: "Version", value: `${profile.version} → ${profile.new_version}` });
  }
  const changed = run.diff.filter((d) => sectionChanged(d.original, d.proposed)).length;
  rows.push({ label: "Sections changed", value: changed > 0 ? String(changed) : "None" });
  return rows;
}

function SummaryRow({
  icon,
  label,
  value,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="flex items-center gap-2 text-ink-500">
        {icon}
        {label}
      </dt>
      <dd className="min-w-0 truncate font-semibold text-ink-800">{value}</dd>
    </div>
  );
}

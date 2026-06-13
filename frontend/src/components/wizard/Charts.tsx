/**
 * Lightweight, zero-dependency SVG charts for the compliance dashboard.
 *
 * The project deliberately ships no chart library (see Icons.tsx); these mirror
 * the hand-rolled house style and the design tokens. All scores are fractions
 * (0..1); `scorePct` renders them as whole percentages.
 */
import type {
  ComplianceDimension,
  ComplianceSectionScore,
  FindingSeverity,
} from "@/types/api";

export const scorePct = (v: number | null | undefined): number =>
  Math.round(Math.max(0, Math.min(1, v ?? 0)) * 100);

const EMERALD = "#059669";
const AMBER   = "#D97706";
const ROSE    = "#E11D48";
const INK     = "#94A3B8";
const BRAND   = "#051D60";

export const SEVERITY_HEX: Record<FindingSeverity, string> = {
  critical: "#E11D48",
  major:    "#EA580C",
  minor:    "#D97706",
  info:     "#2563EB",
};

function bandColor(frac: number | null | undefined): string {
  const v = frac ?? 0;
  if (v >= 0.75) return EMERALD;
  if (v >= 0.5)  return AMBER;
  return ROSE;
}

/* ── overall gauge (generalised donut) ─────────────────────────────────── */
export function ScoreGauge({
  value,
  label = "Overall",
  caption,
  size = 112,
}: {
  value: number | null; // 0..1
  label?: string;
  caption?: string;
  size?: number;
}) {
  const pct   = value == null ? null : scorePct(value);
  const radius = 40;
  const c      = 2 * Math.PI * radius;
  const dash   = ((pct ?? 0) / 100) * c;
  const color  = bandColor(value);

  return (
    <div className="flex items-center gap-5" role="img" aria-label={`${label}: ${pct ?? "not available"}%`}>
      {/* Donut */}
      <div className="relative shrink-0">
        <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
          {/* Track */}
          <circle cx="50" cy="50" r={radius} stroke="#EEF2F4" strokeWidth="10" fill="none" />
          {/* Progress */}
          <circle
            cx="50"
            cy="50"
            r={radius}
            stroke={color}
            strokeWidth="10"
            fill="none"
            strokeDasharray={`${dash} ${c}`}
            strokeLinecap="round"
            transform="rotate(-90 50 50)"
            style={{ transition: "stroke-dasharray 0.6s ease" }}
          />
          {/* Center text */}
          <text
            x="50"
            y="48"
            textAnchor="middle"
            fontSize="22"
            fontWeight="800"
            fill={color}
            fontFamily="inherit"
          >
            {pct == null ? "—" : `${pct}%`}
          </text>
        </svg>
      </div>

      {/* Label */}
      <div className="min-w-0">
        <p className="text-base font-bold text-ink-800 leading-tight">{label}</p>
        {caption && (
          <span
            className={`mt-1.5 inline-block rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${
              (value ?? 0) >= 0.75
                ? "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200/60"
                : (value ?? 0) >= 0.5
                ? "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200/60"
                : "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200/60"
            }`}
          >
            {caption}
          </span>
        )}
      </div>
    </div>
  );
}

/* ── per-dimension bars ────────────────────────────────────────────────── */
const DIM_ORDER: ComplianceDimension[] = ["content", "structure", "formatting", "style", "tone"];

export function DimensionBars({
  data,
}: {
  data: Partial<Record<ComplianceDimension, number>>;
}) {
  const rows = DIM_ORDER.filter((d) => data[d] != null).map((d) => ({ d, v: data[d] as number }));
  if (rows.length === 0) return <p className="text-[12px] text-ink-400">No dimension scores.</p>;

  return (
    <div
      className="space-y-3"
      role="img"
      aria-label={"Scores by dimension: " + rows.map((r) => `${r.d} ${scorePct(r.v)}%`).join(", ")}
    >
      {rows.map(({ d, v }) => {
        const pct   = scorePct(v);
        const color = bandColor(v);
        return (
          <div key={d} className="flex items-center gap-3">
            <span className="w-[5.5rem] shrink-0 text-[12px] font-medium capitalize text-ink-600">{d}</span>
            <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-ink-100">
              <span
                className="absolute inset-y-0 left-0 rounded-full"
                style={{
                  width: `${pct}%`,
                  backgroundColor: color,
                  transition: "width 0.5s cubic-bezier(.4,0,.2,1)",
                }}
              />
            </div>
            <span className="w-9 shrink-0 text-right text-[12px] font-bold tabular-nums" style={{ color }}>
              {pct}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ── per-section bars ──────────────────────────────────────────────────── */
export function SectionBars({
  data,
  onSelect,
}: {
  data: ComplianceSectionScore[];
  onSelect?: (section: string) => void;
}) {
  const rows = data.filter((s) => s.score != null);
  if (rows.length === 0) return <p className="text-[12px] text-ink-400">No section scores.</p>;

  return (
    <div className="space-y-1">
      {rows.map((s) => {
        const pct   = scorePct(s.score);
        const color = bandColor(s.score);
        return (
          <button
            key={s.section}
            type="button"
            onClick={() => onSelect?.(s.section)}
            className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-ink-50 group"
          >
            <span
              className="w-36 shrink-0 truncate text-[11.5px] font-medium text-ink-500 group-hover:text-ink-700 transition-colors"
              title={`${s.section}. ${s.title ?? ""}`}
            >
              <span className="font-bold text-ink-400">{s.section}.</span>{" "}
              {s.title || ""}
            </span>
            <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-ink-100">
              <span
                className="absolute inset-y-0 left-0 rounded-full"
                style={{
                  width: `${pct}%`,
                  backgroundColor: color,
                  transition: "width 0.5s cubic-bezier(.4,0,.2,1)",
                }}
              />
            </div>
            <span
              className="w-9 shrink-0 text-right text-[11.5px] font-bold tabular-nums"
              style={{ color }}
            >
              {pct}%
            </span>
            {s.findings_count > 0 && (
              <span className="w-6 shrink-0 rounded-full bg-rose-50 text-center text-[10px] font-bold text-rose-600 ring-1 ring-inset ring-rose-100">
                {s.findings_count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ── severity distribution ─────────────────────────────────────────────── */
const SEV_ORDER: FindingSeverity[] = ["critical", "major", "minor", "info"];

const SEV_LABEL: Record<FindingSeverity, string> = {
  critical: "Critical",
  major:    "Major",
  minor:    "Minor",
  info:     "Info",
};

export function SeverityBar({
  counts,
}: {
  counts: Partial<Record<FindingSeverity, number>>;
}) {
  const total = SEV_ORDER.reduce((n, s) => n + (counts[s] ?? 0), 0);

  return (
    <div
      role="img"
      aria-label={"Open issues by severity: " + SEV_ORDER.map((s) => `${counts[s] ?? 0} ${s}`).join(", ")}
    >
      {/* Stacked bar */}
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-ink-100">
        {total === 0
          ? null
          : SEV_ORDER.map((s) => {
              const n = counts[s] ?? 0;
              if (!n) return null;
              return (
                <span
                  key={s}
                  style={{ width: `${(n / total) * 100}%`, backgroundColor: SEVERITY_HEX[s] }}
                  title={`${n} ${s}`}
                />
              );
            })}
      </div>

      {/* Legend */}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {SEV_ORDER.map((s) => {
          const n = counts[s] ?? 0;
          return (
            <div key={s} className="flex items-center gap-2">
              <span
                className="h-2 w-2 shrink-0 rounded-sm"
                style={{ backgroundColor: SEVERITY_HEX[s] }}
              />
              <span className="text-[11.5px] text-ink-500">{SEV_LABEL[s]}</span>
              <span className="text-[12px] font-bold tabular-nums text-ink-800 ml-auto">{n}</span>
            </div>
          );
        })}
      </div>

      {total === 0 && (
        <p className="mt-2 text-[12px] font-medium text-emerald-700">No open issues 🎉</p>
      )}
    </div>
  );
}

export { BRAND, INK };

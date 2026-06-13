import { useMemo } from "react";
import type { ReviewDiff } from "@/types/api";
import {
  computeWordDiff,
  getOriginalHighlights,
  getProposedHighlights,
} from "@/lib/diffUtils";
import { alignBlocks, blockText, parseBlocks, type Block } from "@/lib/docBlocks";

/* ── Types ──────────────────────────────────────────────────────────── */

interface DocumentRendererProps {
  /** Array of section-level diffs from the run. */
  diffs: ReviewDiff[];
  /** Which version to render: "original" or "proposed". */
  mode: "original" | "proposed";
  /** Whether to show diff highlighting. */
  showHighlights: boolean;
  /** Optional filename shown at top of document. */
  filename?: string;
  /** When set, each section gets `id="<prefix>-<slot_id>"` as a scroll target. */
  sectionIdPrefix?: string;
  /** Slot to softly spotlight (used by the change prev/next navigation). */
  activeSlotId?: string | null;
}

/* ── Highlight rendering ────────────────────────────────────────────── */

/**
 * Render a block's text with word-level highlights computed against its
 * ALIGNED counterpart (not the whole section), so only real changes light up.
 */
function HighlightedText({
  self,
  counterpart,
  mode,
  showHighlights,
}: {
  self: string;
  counterpart: string;
  mode: "original" | "proposed";
  showHighlights: boolean;
}) {
  const spans = useMemo(() => {
    if (!showHighlights) return null;
    const original = mode === "original" ? self : counterpart;
    const proposed = mode === "original" ? counterpart : self;
    const ops = computeWordDiff(original, proposed);
    return mode === "original"
      ? getOriginalHighlights(ops)
      : getProposedHighlights(ops);
  }, [self, counterpart, mode, showHighlights]);

  if (!showHighlights || !spans) {
    return <>{self}</>;
  }

  return (
    <>
      {spans.map((s, i) => {
        if (!s.highlight) return <span key={i}>{s.text}</span>;
        return (
          <mark
            key={i}
            className={
              mode === "original"
                ? "bg-amber-100/80 text-amber-900 rounded-sm px-[1px]"
                : "bg-emerald-100/80 text-emerald-900 rounded-sm px-[1px]"
            }
          >
            {s.text}
          </mark>
        );
      })}
    </>
  );
}

/* ── Section renderer ───────────────────────────────────────────────── */

export function SectionDiffView({
  diff,
  mode,
  showHighlights,
  showTitle = true,
}: {
  diff: ReviewDiff;
  mode: "original" | "proposed";
  showHighlights: boolean;
  showTitle?: boolean;
}) {
  const origBlocks = useMemo(() => parseBlocks(diff.original), [diff.original]);
  const propBlocks = useMemo(() => parseBlocks(diff.proposed), [diff.proposed]);
  const { aToB, bToA } = useMemo(
    () => alignBlocks(origBlocks, propBlocks),
    [origBlocks, propBlocks]
  );

  const blocks = mode === "original" ? origBlocks : propBlocks;
  const text = mode === "original" ? diff.original : diff.proposed;

  // Text of the aligned counterpart block (for per-block highlighting).
  const counterpartText = (i: number): string => {
    const cp = mode === "original" ? aToB[i] : bToA[i];
    if (cp == null) return "";
    const arr = mode === "original" ? propBlocks : origBlocks;
    return blockText(arr[cp]);
  };

  // A block is a [TODO] placeholder if it's a paragraph starting with "[TODO]".
  const isTodoBlock = (b: Block) =>
    b.kind === "paragraph" && b.text.startsWith("[TODO]");

  // Check if the section has any visible content after excluding empty and
  // TODO-placeholder blocks — show "(empty section)" only when nothing remains.
  const hasVisibleContent =
    !!text &&
    text.trim() !== "" &&
    blocks.some(
      (b) => b.kind !== "empty" && !isTodoBlock(b) && blockText(b).trim() !== ""
    );

  if (!hasVisibleContent) {
    return (
      <div className={showTitle ? "px-6 py-4" : "py-1"}>
        {showTitle && (
          <h3 className="text-base font-bold text-ink-800" style={SERIF}>
            {diff.title || diff.slot_id}
          </h3>
        )}
        <p className="mt-1 italic text-ink-400 text-sm">(empty section)</p>
      </div>
    );
  }

  return (
    <div className={showTitle ? "px-6 py-3" : ""}>
      {/* Section title */}
      {showTitle && (
        <div className="mb-3 pb-2 border-b border-ink-100">
          <h3 className="text-base font-bold text-ink-800" style={SERIF}>
            {diff.title || diff.slot_id}
          </h3>
        </div>
      )}

      {/* Formatted content — skip [TODO] placeholder blocks. Consecutive
          table rows render as ONE table (header + striped body) instead of a
          stack of disconnected row boxes. */}
      <div className="space-y-1.5 leading-relaxed" style={SERIF}>
        {groupBlocks(blocks).map((group, gi) => {
          if (group.kind === "table") {
            return (
              <TableView
                key={gi}
                rows={group.idxs.map((i) => blocks[i] as Block & { cells: string[] })}
              />
            );
          }
          const i = group.idx;
          const block = blocks[i];
          return isTodoBlock(block) ? null : (
            <BlockView
              key={gi}
              block={block}
              self={blockText(block)}
              counterpart={counterpartText(i)}
              mode={mode}
              showHighlights={showHighlights}
            />
          );
        })}
      </div>
    </div>
  );
}

const SERIF = { fontFamily: "'Times New Roman', 'Georgia', serif" } as const;

/* ── Table grouping ─────────────────────────────────────────────────── */

type RenderGroup = { kind: "single"; idx: number } | { kind: "table"; idxs: number[] };

/** Group consecutive table-row blocks so they render as one table. */
function groupBlocks(blocks: Block[]): RenderGroup[] {
  const out: RenderGroup[] = [];
  let run: number[] = [];
  blocks.forEach((b, i) => {
    if (b.kind === "table-row") {
      run.push(i);
      return;
    }
    if (run.length) {
      out.push({ kind: "table", idxs: run });
      run = [];
    }
    out.push({ kind: "single", idx: i });
  });
  if (run.length) out.push({ kind: "table", idxs: run });
  return out;
}

function TableView({ rows }: { rows: { cells: string[] }[] }) {
  if (rows.length === 0) return null;
  const cols = Math.max(...rows.map((r) => r.cells.length));
  const hasHeader = rows.length > 1;
  const body = hasHeader ? rows.slice(1) : rows;
  return (
    <div className="my-2 overflow-x-auto rounded-lg border border-ink-200">
      <table className="w-full border-collapse text-[12px]">
        {hasHeader && (
          <thead>
            <tr>
              {Array.from({ length: cols }).map((_, ci) => (
                <th
                  key={ci}
                  className="border-b border-ink-200 bg-ink-100/70 px-2.5 py-1.5 text-left font-semibold text-ink-800"
                >
                  {rows[0].cells[ci] ?? ""}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri} className={ri % 2 ? "bg-ink-50/40" : "bg-white"}>
              {Array.from({ length: cols }).map((_, ci) => (
                <td
                  key={ci}
                  className="border-b border-ink-100 px-2.5 py-1.5 align-top text-ink-700"
                >
                  {r.cells[ci] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const HEADING_SIZES: Record<number, string> = {
  1: "text-[18px] font-bold mt-4 mb-1",
  2: "text-[16px] font-bold mt-3 mb-1",
  3: "text-[14px] font-semibold mt-2 mb-1",
  4: "text-[13px] font-semibold mt-2 mb-0.5",
  5: "text-[12px] font-semibold mt-1",
  6: "text-[11px] font-semibold mt-1 uppercase tracking-wider",
};

function BlockView({
  block,
  self,
  counterpart,
  mode,
  showHighlights,
}: {
  block: Block;
  self: string;
  counterpart: string;
  mode: "original" | "proposed";
  showHighlights: boolean;
}) {
  const hl = (
    <HighlightedText
      self={self}
      counterpart={counterpart}
      mode={mode}
      showHighlights={showHighlights}
    />
  );

  if (block.kind === "empty") return <div className="h-2" />;

  if (block.kind === "heading") {
    const Tag = `h${Math.min(block.level + 1, 6)}` as keyof JSX.IntrinsicElements;
    return <Tag className={`text-ink-900 ${HEADING_SIZES[block.level] ?? HEADING_SIZES[3]}`}>{hl}</Tag>;
  }

  if (block.kind === "list-item") {
    return (
      <div
        className="flex gap-2 text-[13px] text-ink-700"
        style={{ paddingLeft: `${1 + block.level * 1.25}rem` }}
      >
        <span className="shrink-0 text-ink-400 select-none w-5 text-right">
          {block.ordered ? block.marker : "•"}
        </span>
        <span className="flex-1">{hl}</span>
      </div>
    );
  }

  if (block.kind === "table-row") {
    return (
      <div
        className="grid gap-0 overflow-hidden rounded border border-ink-200"
        style={{ gridTemplateColumns: `repeat(${block.cells.length}, 1fr)` }}
      >
        {block.cells.map((cell, ci) => (
          <div
            key={ci}
            className="border-r border-ink-100 bg-ink-50/30 px-2 py-1.5 text-[12px] text-ink-700 last:border-r-0"
          >
            {cell}
          </div>
        ))}
      </div>
    );
  }

  return <p className="text-[13px] text-ink-700 leading-[1.75]">{hl}</p>;
}

/* ── Main document renderer ─────────────────────────────────────────── */

export function DocumentRenderer({
  diffs,
  mode,
  showHighlights,
  filename,
  sectionIdPrefix,
  activeSlotId,
}: DocumentRendererProps) {
  return (
    <div className="bg-white min-h-[600px]">
      {filename && (
        <div className="px-6 pt-5 pb-3 border-b border-ink-100">
          <div className="flex items-center gap-2">
            <div
              className="w-1 h-6 rounded-full"
              style={{ backgroundColor: mode === "original" ? "#051D60" : "#059669" }}
            />
            <h2
              className="text-sm font-bold text-ink-800 uppercase tracking-wide"
              style={SERIF}
            >
              {filename}
            </h2>
          </div>
        </div>
      )}

      <div className="divide-y divide-ink-100/60">
        {diffs.length === 0 ? (
          <div className="flex items-center justify-center py-20">
            <p className="text-sm text-ink-400 italic">No content changes to display</p>
          </div>
        ) : (
          diffs.map((diff) => (
            <div
              key={diff.slot_id}
              id={sectionIdPrefix ? `${sectionIdPrefix}-${diff.slot_id}` : undefined}
              className={`transition-colors duration-500 ${
                activeSlotId === diff.slot_id ? "bg-brand-50/60" : ""
              }`}
            >
              <SectionDiffView
                diff={diff}
                mode={mode}
                showHighlights={showHighlights}
              />
            </div>
          ))
        )}
      </div>

      <div className="px-6 py-3 border-t border-ink-100 mt-4">
        <p className="text-[11px] text-ink-400 text-center">
          {diffs.length} section{diffs.length !== 1 ? "s" : ""}
          {mode === "original" ? " — Original Document" : " — Updated Document"}
        </p>
      </div>
    </div>
  );
}

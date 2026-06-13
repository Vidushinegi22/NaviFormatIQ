/**
 * Document block model + structural alignment.
 * =============================================
 *
 * Section body text (from the regenerate flow) is a plain string whose lines
 * encode structure: bullets (`- ` / `• `), numbered steps (`1.`), pipe tables,
 * and paragraphs. To render and diff it faithfully we:
 *
 *   1. `parseBlocks(text)` — split the text into typed blocks (one per line).
 *   2. `alignBlocks(a, b)` — pair each block in A with its best match in B so a
 *      side-by-side diff highlights *only* what actually changed. Matching is
 *      similarity-based (Jaccard over words), so a lightly-edited bullet aligns
 *      and shows a word-level change, while a genuinely new bullet is unmatched
 *      and highlighted whole.
 *
 * This replaces the old per-line-index approach, which (a) rendered the entire
 * section for every paragraph line — the duplicated-content bug — and (b)
 * mis-aligned highlights whenever a line was added or removed.
 */

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list-item"; level: number; ordered: boolean; marker: string; text: string }
  | { kind: "table-row"; cells: string[] }
  | { kind: "empty" };

const LIST_RE = /^(\s*)(\d+[.)]|[-*•◦▪‣·])\s+(.*\S)\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.*\S)\s*$/;

/** Parse a section's text into typed structural blocks (one per line). */
export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of (text ?? "").split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed) {
      blocks.push({ kind: "empty" });
      continue;
    }

    const h = trimmed.match(HEADING_RE);
    if (h) {
      blocks.push({ kind: "heading", level: h[1].length, text: h[2].trim() });
      continue;
    }

    const l = raw.match(LIST_RE);
    if (l) {
      const indent = l[1].replace(/\t/g, "  ").length;
      blocks.push({
        kind: "list-item",
        level: Math.min(Math.floor(indent / 2), 3),
        ordered: /\d/.test(l[2]),
        marker: l[2],
        text: l[3].trim(),
      });
      continue;
    }

    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const cells = trimmed.slice(1, -1).split("|").map((c) => c.trim());
      if (!cells.every((c) => /^[-:]+$/.test(c))) {
        blocks.push({ kind: "table-row", cells });
        continue;
      }
    }

    blocks.push({ kind: "paragraph", text: trimmed });
  }
  return blocks;
}

/** The plain text carried by a block (for diffing / editing). */
export function blockText(b: Block): string {
  switch (b.kind) {
    case "heading":
    case "paragraph":
    case "list-item":
      return b.text;
    case "table-row":
      return b.cells.join(" | ");
    default:
      return "";
  }
}

/* ── Alignment ─────────────────────────────────────────────────────────── */

function norm(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

function similarity(x: string, y: string): number {
  if (x === y) return 1;
  if (!x || !y) return 0;
  const xs = new Set(x.split(" "));
  const ys = new Set(y.split(" "));
  let inter = 0;
  for (const w of xs) if (ys.has(w)) inter++;
  const union = xs.size + ys.size - inter;
  return union ? inter / union : 0;
}

function blocksMatch(a: Block, b: Block): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "empty") return true;
  const x = norm(blockText(a));
  const y = norm(blockText(b));
  if (x === y) return true;
  if (!x || !y) return false;
  return similarity(x, y) >= 0.5;
}

/**
 * Align two block lists. Returns index maps: `aToB[i]` is the index in B that
 * block `a[i]` pairs with (or null if it has no counterpart), and vice-versa.
 * Uses an LCS over a similarity-based match so order is preserved.
 */
export function alignBlocks(
  a: Block[],
  b: Block[]
): { aToB: (number | null)[]; bToA: (number | null)[] } {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0)
  );
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = blocksMatch(a[i], b[j])
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const aToB: (number | null)[] = new Array(m).fill(null);
  const bToA: (number | null)[] = new Array(n).fill(null);
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (blocksMatch(a[i], b[j])) {
      aToB[i] = j;
      bToA[j] = i;
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      j++;
    }
  }
  return { aToB, bToA };
}

/** True when a block has visible content (used to count real changes). */
export function isContentBlock(b: Block): boolean {
  return b.kind !== "empty" && blockText(b).trim() !== "";
}

/**
 * True when the proposed text *visibly* differs from the original — i.e. the
 * block-aligned renderer would actually surface a change.
 *
 * This uses the SAME block alignment as the diff highlighter (`parseBlocks` +
 * `alignBlocks`), so the "AI revised" badge and the green highlights always
 * agree. A section the model merely re-emitted with cosmetic differences
 * (whitespace, blank lines, bullet-marker style) reports `false`; an added,
 * removed, or reworded content block reports `true`.
 *
 * The previous implementation compared the whole normalized strings, which
 * flagged every section the regenerate flow re-emitted — even untouched ones —
 * as "AI revised" while nothing was highlighted.
 */
export function sectionChanged(original: string, proposed: string): boolean {
  // Strip [TODO] placeholder lines (not the entire text) so they don't inflate
  // or deflate the change count — only real content differences should count.
  const stripTodo = (t: string) =>
    t
      .split("\n")
      .filter((ln) => !ln.trim().startsWith("[TODO]"))
      .join("\n")
      .trim();
  const a = parseBlocks(stripTodo(original ?? ""));
  const b = parseBlocks(stripTodo(proposed ?? ""));
  const { aToB, bToA } = alignBlocks(a, b);

  // A content block was added (present in proposed, no counterpart in original).
  for (let j = 0; j < b.length; j++) {
    if (bToA[j] == null && isContentBlock(b[j])) return true;
  }
  // A content block was removed (present in original, no counterpart in proposed).
  for (let i = 0; i < a.length; i++) {
    if (aToB[i] == null && isContentBlock(a[i])) return true;
  }
  // A matched block was reworded (same slot, different text).
  for (let i = 0; i < a.length; i++) {
    const j = aToB[i];
    if (j != null && norm(blockText(a[i])) !== norm(blockText(b[j]))) return true;
  }
  return false;
}

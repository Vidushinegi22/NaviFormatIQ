/**
 * Word-level diff utilities.
 *
 * Uses a simple LCS (longest common subsequence) approach to produce
 * a list of diff operations. No external dependencies.
 */

export type DiffOp =
  | { kind: "keep"; text: string }
  | { kind: "delete"; text: string }
  | { kind: "insert"; text: string };

/** A renderable run of text with a highlight flag. */
export interface HighlightSpan {
  text: string;
  highlight: boolean;
}

/**
 * Tokenise a block of text into words, preserving whitespace by
 * attaching trailing space to each token. This keeps rendering
 * faithful to the original spacing.
 */
function tokenise(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [];
}

/**
 * Build an LCS table for two token arrays.
 */
function lcsTable(a: string[], b: string[]): number[][] {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0)
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1].trim() === b[j - 1].trim()) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  return dp;
}

/**
 * Back-track through the LCS table to produce a diff.
 */
function backtrack(
  dp: number[][],
  a: string[],
  b: string[],
  i: number,
  j: number
): DiffOp[] {
  const ops: DiffOp[] = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1].trim() === b[j - 1].trim()) {
      ops.push({ kind: "keep", text: b[j - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ kind: "insert", text: b[j - 1] });
      j--;
    } else {
      ops.push({ kind: "delete", text: a[i - 1] });
      i--;
    }
  }

  return ops.reverse();
}

/**
 * Compute a word-level diff between `original` and `proposed`.
 *
 * Returns an array of operations:
 * - `keep`   → word exists in both (unchanged)
 * - `delete` → word exists only in original (removed / modified)
 * - `insert` → word exists only in proposed (added / modified)
 */
export function computeWordDiff(
  original: string,
  proposed: string
): DiffOp[] {
  const a = tokenise(original);
  const b = tokenise(proposed);
  const dp = lcsTable(a, b);
  return backtrack(dp, a, b, a.length, b.length);
}

/**
 * Maximum number of unchanged words absorbed between two highlighted runs
 * when merging word-level highlights into phrase-level spans.
 */
const PHRASE_GAP = 2;

/**
 * Merge nearby word-level highlights into phrase-level spans.
 *
 * Word-level LCS diffs of rewritten prose tend to produce a "zebra" pattern —
 * many one-word highlights separated by one or two unchanged words — which is
 * hard to scan. When two highlighted runs (≥2 changed words in total) are
 * separated by ≤ PHRASE_GAP unchanged words, the gap is absorbed so the whole
 * phrase reads as one change. Adjacent spans with the same flag are then
 * coalesced so each phrase renders as a single span.
 */
export function mergePhraseHighlights(spans: HighlightSpan[]): HighlightSpan[] {
  // Absorb short unchanged gaps BETWEEN highlighted runs (never leading or
  // trailing ones, so highlights still start/end on a changed word).
  const flags = spans.map((s) => s.highlight);
  let prevHl = -1; // index of the last highlighted span seen
  for (let i = 0; i < spans.length; i++) {
    if (!spans[i].highlight) continue;
    const gap = i - prevHl - 1;
    if (prevHl >= 0 && gap > 0 && gap <= PHRASE_GAP) {
      for (let k = prevHl + 1; k < i; k++) flags[k] = true;
    }
    prevHl = i;
  }
  // Coalesce adjacent spans with the same flag.
  const out: HighlightSpan[] = [];
  for (let i = 0; i < spans.length; i++) {
    const last = out[out.length - 1];
    if (last && last.highlight === flags[i]) last.text += spans[i].text;
    else out.push({ text: spans[i].text, highlight: flags[i] });
  }
  return out;
}

/**
 * Build highlighted spans for the **original** document.
 * Words that were deleted/changed get `highlight: true`.
 * Nearby changes are merged into phrase-level spans for readability.
 */
export function getOriginalHighlights(ops: DiffOp[]): HighlightSpan[] {
  return mergePhraseHighlights(
    ops
      .filter((op) => op.kind !== "insert")
      .map((op) => ({
        text: op.text,
        highlight: op.kind === "delete",
      }))
  );
}

/**
 * Build highlighted spans for the **proposed** document.
 * Words that were inserted/changed get `highlight: true`.
 * Nearby changes are merged into phrase-level spans for readability.
 */
export function getProposedHighlights(ops: DiffOp[]): HighlightSpan[] {
  return mergePhraseHighlights(
    ops
      .filter((op) => op.kind !== "delete")
      .map((op) => ({
        text: op.text,
        highlight: op.kind === "insert",
      }))
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { getRun, runStreamUrl } from "@/lib/api";
import type { RunDetail, RunEvent, RunStatus } from "@/types/api";

const TERMINAL: RunStatus[] = ["done", "error", "cancelled"];
const POLL_MS = 2000;

/**
 * Terminal runs never change, so cache their detail for the lifetime of the
 * tab. Navigating back/forward through the wizard (or reopening a finished
 * step) renders the stored result instantly — no refetch, no polling, no SSE,
 * and crucially no "in progress" flash that reads as the process re-running.
 */
const terminalRunCache = new Map<string, RunDetail>();

export interface UseRunResult {
  run: RunDetail | null;
  /** Live node-trace events for progress display (newest last). */
  events: RunEvent[];
  status: RunStatus | null;
  loading: boolean;
  error: string | null;
  /** Re-open the stream + polling (call after resuming a paused run). */
  reconnect: () => void;
  /** Force a one-off refetch of the authoritative run detail. */
  refresh: () => Promise<RunDetail | null>;
}

function isTerminal(s: RunStatus | string | undefined): boolean {
  return !!s && TERMINAL.includes(s as RunStatus);
}

/**
 * Track a flow run end-to-end.
 *
 * Polling drives the authoritative `run` state (robust even if SSE is
 * buffered by a proxy); an EventSource layered on top surfaces live trace
 * events for a lively progress view. Both stop at a terminal or HITL status.
 * Pass `null` to track nothing.
 */
export function useRun(runId: string | null): UseRunResult {
  // Seed from the terminal cache so a finished run renders on the very first
  // paint — before any effect runs.
  const [run, setRun] = useState<RunDetail | null>(() =>
    runId ? terminalRunCache.get(runId) ?? null : null
  );
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);

  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const reconnect = useCallback(() => {
    setEvents([]);
    setError(null);
    setEpoch((e) => e + 1);
  }, []);

  const refresh = useCallback(async (): Promise<RunDetail | null> => {
    if (!runId) return null;
    try {
      const detail = await getRun(runId);
      if (isTerminal(detail.status)) terminalRunCache.set(runId, detail);
      setRun(detail);
      return detail;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [runId]);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }

    // Finished run already in cache: render it and do nothing else. No
    // network, no polling, no SSE — instant back/forward navigation.
    const cached = terminalRunCache.get(runId);
    if (cached) {
      setRun(cached);
      return;
    }

    let cancelled = false;

    const stopStream = () => {
      esRef.current?.close();
      esRef.current = null;
    };
    const stopPoll = () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };

    const settleIfDone = (detail: RunDetail | null) => {
      if (!detail) return;
      if (isTerminal(detail.status)) {
        // Run finished — nothing more will change. Cache for instant revisits.
        terminalRunCache.set(runId, detail);
        stopPoll();
        stopStream();
      } else if (detail.status === "hitl") {
        // Paused for review: the server closes the SSE stream, so close it
        // too (avoids reconnect spam). Keep POLLING so we catch the
        // resume → running → done transition without a race.
        stopStream();
      }
    };

    // 2) Polling safety net — started only for live runs.
    const startPoll = () => {
      pollRef.current = setInterval(async () => {
        try {
          const detail = await getRun(runId);
          if (cancelled) return;
          setRun(detail);
          settleIfDone(detail);
        } catch {
          /* transient — next tick retries */
        }
      }, POLL_MS);
    };

    // 3) Live SSE for trace events — started only for running runs.
    const startStream = () => {
      try {
        const es = new EventSource(runStreamUrl(runId));
        esRef.current = es;
        es.onmessage = (msg) => {
          if (cancelled) return;
          let ev: RunEvent;
          try {
            ev = JSON.parse(msg.data) as RunEvent;
          } catch {
            return;
          }
          setEvents((prev) => [...prev, ev]);
          if (isTerminal(ev.status) || ev.status === "hitl") {
            // Pull the authoritative detail (artifacts, diff, flags) and stop.
            refresh().then(settleIfDone);
            stopStream();
          }
        };
        es.onerror = () => {
          // Browser auto-reconnects while the run is live; polling covers gaps.
        };
      } catch {
        /* EventSource unsupported — polling still drives the UI */
      }
    };

    // 1) Initial authoritative fetch decides whether live tracking is needed.
    setLoading(true);
    getRun(runId)
      .then((detail) => {
        if (cancelled) return;
        setRun(detail);
        if (isTerminal(detail.status)) {
          // Already finished (e.g. reopened project): one fetch, cache, stop.
          terminalRunCache.set(runId, detail);
          return;
        }
        startPoll();
        // Paused (hitl): the server closes SSE streams, so don't open one;
        // polling alone catches the resume → running → done transition.
        if (detail.status !== "hitl") startStream();
      })
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
      stopPoll();
      stopStream();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, epoch]);

  return {
    run,
    events,
    status: run?.status ?? null,
    loading,
    error,
    reconnect,
    refresh,
  };
}

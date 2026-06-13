import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { FileKind } from "@/types/workflow";
import { updateProject } from "@/lib/api";

/**
 * Document / pipeline context.
 *
 * Holds the live state of one run through the wizard: the backend Project,
 * the uploaded source document, an optional secondary document (the style
 * source for the Update-Style flow, or a structure template), the current
 * flow run id, and rolling per-step completion flags.
 *
 * The whole thing is mirrored to sessionStorage so a hard reload mid-wizard
 * keeps the user on track. Document *bytes* never live here — only the ids
 * the backend handed back, exactly as a production client would keep them.
 *
 * Step completion and current_step are also persisted to the backend so
 * reopening a project from the dashboard restores the full wizard state.
 */

/** A reference to a stored document version on the backend. */
export interface DocRef {
  documentId: string;
  artifactId: string;
  version: number; // backend storage version (always 1 per upload)
  filename: string;
  kind: FileKind; // "docx" | "pdf"
  size?: number;
  uploadedAt?: string; // ISO
  /** User-entered document version label (e.g. "9") — the second-version flow. */
  versionLabel?: string;
}

export interface StepCompletion {
  upload: boolean;
  extract: boolean;
  review: boolean;
  compliance: boolean;
  style: boolean;
  generate: boolean;
  chat: boolean;
  export: boolean;
}

const initialCompletion: StepCompletion = {
  upload: false,
  extract: false,
  review: false,
  compliance: false,
  style: false,
  generate: false,
  chat: false,
  export: false,
};

interface PipelineState {
  project: { id: string; name: string } | null;
  /** Primary uploaded document (the draft / content target). */
  source: DocRef | null;
  /** Style source (Update-Style flow) or optional structure template. */
  secondary: DocRef | null;
  /**
   * Generate-New-Version flow: other uploaded versions of the same document,
   * used purely as context (what changed across revisions). `source` is the
   * highest-version upload; these are the rest.
   */
  contextDocs: DocRef[];
  /** Generate-New-Version flow: the new version number (max uploaded + 1). */
  targetVersion: number | null;
  /** Id of the in-flight (or last) flow run. */
  runId: string | null;
  /** Compliance: selected domain (e.g. "pharma"). */
  domainId: string | null;
  /** Compliance: selected pre-loaded guideline id (e.g. ICH E3). */
  guidelineId: string | null;
  completion: StepCompletion;
}

const emptyState: PipelineState = {
  project: null,
  source: null,
  secondary: null,
  contextDocs: [],
  targetVersion: null,
  runId: null,
  domainId: null,
  guidelineId: null,
  completion: initialCompletion,
};

interface DocumentContextValue extends PipelineState {
  setProject: (p: PipelineState["project"]) => void;
  setSource: (d: DocRef | null) => void;
  setSecondary: (d: DocRef | null) => void;
  setContextDocs: (docs: DocRef[]) => void;
  setTargetVersion: (v: number | null) => void;
  setRunId: (id: string | null) => void;
  /** Compliance: domain + guideline selection (persisted to project meta). */
  setDomainId: (id: string | null) => void;
  setGuidelineId: (id: string | null) => void;
  markComplete: (step: keyof StepCompletion) => void;
  /** Track which wizard page the user is on (persisted to backend). */
  setCurrentStep: (step: string) => void;
  /** Restore completion flags from backend data (used when reopening a project). */
  hydrateCompletion: (completion: Record<string, boolean>) => void;
  /** Restore domain/guideline selection from project meta (on reopen). */
  hydrateSelection: (meta: Record<string, unknown> | null | undefined) => void;
  reset: () => void;
}

const STORAGE_KEY = "naviFormatiq.pipeline";

const DocumentContext = createContext<DocumentContextValue | undefined>(
  undefined
);

function readStored(): PipelineState {
  if (typeof window === "undefined") return emptyState;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyState;
    const parsed = JSON.parse(raw) as Partial<PipelineState>;
    return {
      ...emptyState,
      ...parsed,
      completion: { ...initialCompletion, ...(parsed.completion ?? {}) },
    };
  } catch {
    return emptyState;
  }
}

export function DocumentProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PipelineState>(() => readStored());

  // Persist across reloads / step navigation.
  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* storage may be unavailable in embedded contexts */
    }
  }, [state]);

  const setProject = useCallback(
    (project: PipelineState["project"]) =>
      setState((s) => ({ ...s, project })),
    []
  );
  const setSource = useCallback(
    (source: DocRef | null) => setState((s) => ({ ...s, source })),
    []
  );
  const setSecondary = useCallback(
    (secondary: DocRef | null) => setState((s) => ({ ...s, secondary })),
    []
  );
  const setContextDocs = useCallback(
    (contextDocs: DocRef[]) => setState((s) => ({ ...s, contextDocs })),
    []
  );
  const setTargetVersion = useCallback(
    (targetVersion: number | null) => setState((s) => ({ ...s, targetVersion })),
    []
  );
  const setRunId = useCallback(
    (runId: string | null) => setState((s) => ({ ...s, runId })),
    []
  );

  const setDomainId = useCallback(
    (domainId: string | null) =>
      setState((s) => {
        if (s.project?.id) {
          updateProject(s.project.id, {
            meta: { domain_id: domainId, guideline_id: s.guidelineId },
          }).catch(() => {});
        }
        return { ...s, domainId };
      }),
    []
  );
  const setGuidelineId = useCallback(
    (guidelineId: string | null) =>
      setState((s) => {
        if (s.project?.id) {
          updateProject(s.project.id, {
            meta: { domain_id: s.domainId, guideline_id: guidelineId },
          }).catch(() => {});
        }
        return { ...s, guidelineId };
      }),
    []
  );

  const markComplete = useCallback(
    (step: keyof StepCompletion) =>
      setState((s) => {
        if (s.completion[step]) return s;
        const newCompletion = { ...s.completion, [step]: true };
        // Persist to backend (fire-and-forget)
        if (s.project?.id) {
          updateProject(s.project.id, {
            completion: newCompletion,
            current_step: step,
          }).catch(() => {
            /* backend offline — sessionStorage still has the data */
          });
        }
        return { ...s, completion: newCompletion };
      }),
    []
  );

  const setCurrentStep = useCallback(
    (step: string) => {
      setState((s) => {
        // Persist current page to backend (fire-and-forget)
        if (s.project?.id) {
          updateProject(s.project.id, { current_step: step }).catch(() => {});
        }
        return s; // no local state change needed
      });
    },
    []
  );

  const hydrateCompletion = useCallback(
    (completion: Record<string, boolean>) => {
      setState((s) => ({
        ...s,
        completion: {
          ...initialCompletion,
          ...completion,
        },
      }));
    },
    []
  );

  const hydrateSelection = useCallback(
    (meta: Record<string, unknown> | null | undefined) => {
      if (!meta) return;
      const domainId = (meta.domain_id as string) ?? null;
      const guidelineId = (meta.guideline_id as string) ?? null;
      setState((s) => ({
        ...s,
        domainId: domainId ?? s.domainId,
        guidelineId: guidelineId ?? s.guidelineId,
      }));
    },
    []
  );

  const reset = useCallback(() => setState(emptyState), []);

  const value = useMemo<DocumentContextValue>(
    () => ({
      ...state,
      setProject,
      setSource,
      setSecondary,
      setContextDocs,
      setTargetVersion,
      setRunId,
      setDomainId,
      setGuidelineId,
      markComplete,
      setCurrentStep,
      hydrateCompletion,
      hydrateSelection,
      reset,
    }),
    [state, setProject, setSource, setSecondary, setContextDocs, setTargetVersion, setRunId, setDomainId, setGuidelineId, markComplete, setCurrentStep, hydrateCompletion, hydrateSelection, reset]
  );

  return (
    <DocumentContext.Provider value={value}>
      {children}
    </DocumentContext.Provider>
  );
}

export function useDocument(): DocumentContextValue {
  const ctx = useContext(DocumentContext);
  if (!ctx) {
    throw new Error("useDocument must be used within a DocumentProvider");
  }
  return ctx;
}

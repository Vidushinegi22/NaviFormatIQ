import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { WorkflowType } from "@/types/workflow";

/**
 * Workflow selection context.
 *
 * Persists the user's selected workflow across pages so the wizard on
 * subsequent screens can render the correct step rail. The value is also
 * mirrored to sessionStorage so a hard reload mid-wizard keeps state.
 */

const STORAGE_KEY = "naviFormatiq.selectedWorkflow";
const NAME_STORAGE_KEY = "naviFormatiq.workflowName";

interface WorkflowContextValue {
  selectedWorkflow: WorkflowType | null;
  setSelectedWorkflow: (workflow: WorkflowType | null) => void;
  /** User-entered workflow/campaign name (e.g. "Email Demo"). */
  workflowName: string | null;
  setWorkflowName: (name: string | null) => void;
}

const WorkflowContext = createContext<WorkflowContextValue | undefined>(
  undefined
);

function readStored(): WorkflowType | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw === "second-version" || raw === "style-update" || raw === "compliance-check") {
      return raw;
    }
    return null;
  } catch {
    return null;
  }
}

function readStoredName(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(NAME_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function WorkflowProvider({ children }: { children: ReactNode }) {
  const [selectedWorkflow, setSelectedWorkflowState] =
    useState<WorkflowType | null>(() => readStored());
  const [workflowName, setWorkflowNameState] =
    useState<string | null>(() => readStoredName());

  // Mirror to sessionStorage so navigation + reloads keep selection.
  useEffect(() => {
    try {
      if (selectedWorkflow) {
        window.sessionStorage.setItem(STORAGE_KEY, selectedWorkflow);
      } else {
        window.sessionStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // Storage may be unavailable in some embedded contexts — fall through.
    }
  }, [selectedWorkflow]);

  useEffect(() => {
    try {
      if (workflowName) {
        window.sessionStorage.setItem(NAME_STORAGE_KEY, workflowName);
      } else {
        window.sessionStorage.removeItem(NAME_STORAGE_KEY);
      }
    } catch {
      // fall through
    }
  }, [workflowName]);

  const setSelectedWorkflow = useCallback(
    (workflow: WorkflowType | null) => {
      setSelectedWorkflowState(workflow);
    },
    []
  );

  const setWorkflowName = useCallback(
    (name: string | null) => {
      setWorkflowNameState(name);
    },
    []
  );

  const value = useMemo(
    () => ({ selectedWorkflow, setSelectedWorkflow, workflowName, setWorkflowName }),
    [selectedWorkflow, setSelectedWorkflow, workflowName, setWorkflowName]
  );

  return (
    <WorkflowContext.Provider value={value}>
      {children}
    </WorkflowContext.Provider>
  );
}

export function useWorkflow(): WorkflowContextValue {
  const ctx = useContext(WorkflowContext);
  if (!ctx) {
    throw new Error("useWorkflow must be used within a WorkflowProvider");
  }
  return ctx;
}

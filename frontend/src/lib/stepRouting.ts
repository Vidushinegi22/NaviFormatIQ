import { WORKFLOW_DEFINITIONS } from "@/config/wizardSteps";
import type { WizardStepKey, WorkflowType } from "@/types/workflow";

/**
 * Maps wizard step keys to their URL paths.
 * Routes are mounted by `router.tsx` and the wizard rail uses these to
 * build navigation links.
 */
export const STEP_TO_PATH: Record<WizardStepKey, string> = {
  upload: "/upload",
  extract: "/extract",
  review: "/review",
  compliance: "/compliance",
  style: "/style",
  generate: "/generate",
  chat: "/chat",
  export: "/export",
};

export function pathForStep(key: WizardStepKey): string {
  return STEP_TO_PATH[key];
}

/**
 * Resolve the path of the step that follows `activeKey` in the given
 * workflow (or null if it is the last step). Lets pages run a side-effect
 * before advancing without hard-coding the next route.
 */
export function nextStepPath(
  workflow: WorkflowType | null,
  activeKey: WizardStepKey
): string | null {
  const def = WORKFLOW_DEFINITIONS[workflow ?? "second-version"];
  const i = def.steps.findIndex((s) => s.key === activeKey);
  if (i < 0 || i >= def.steps.length - 1) return null;
  return pathForStep(def.steps[i + 1].key);
}

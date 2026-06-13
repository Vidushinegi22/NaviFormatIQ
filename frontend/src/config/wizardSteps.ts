import type { WorkflowDefinition, WorkflowType } from "@/types/workflow";

/**
 * Canonical definitions for every workflow Navi FormatiQ exposes.
 *
 * The wizard on subsequent pages is generated from `steps` here, so the
 * dashboard and the wizard never disagree about which screens to show.
 */
export const WORKFLOW_DEFINITIONS: Record<WorkflowType, WorkflowDefinition> = {
  "second-version": {
    type: "second-version",
    category: "Document Drafting",
    title: "Generate New Version",
    description:
      "Produce an updated revision of clinical protocols, INDs, CSRs, or regulatory submissions while preserving original intent, section hierarchy, and GxP-compliant structure.",
    steps: [
      { key: "upload", label: "Upload", hint: "Protocol or filing" },
      { key: "extract", label: "Extract", hint: "Sections and styling" },
      { key: "review", label: "Revise", hint: "Edit & update content" },
      { key: "generate", label: "Result", hint: "Compare versions" },
      { key: "export", label: "Export", hint: "Submission-ready" },
      { key: "chat", label: "Chat", hint: "Refine with AI" },
    ],
  },
  "style-update": {
    type: "style-update",
    category: "Formatting",
    title: "Update Style",
    description:
      "Apply your sponsor or CRO template (ICH-compliant fonts, headings, tables, and layout) to clinical reports and regulatory dossiers, or align to a reference filing.",
    steps: [
      { key: "upload", label: "Upload", hint: "Content + template" },
      { key: "extract", label: "Extract", hint: "Detect styling" },
      { key: "style", label: "Style", hint: "Apply template" },
      { key: "export", label: "Export", hint: "Final dossier" },
      { key: "chat", label: "Chat", hint: "Tune output" },
    ],
  },
  "compliance-check": {
    type: "compliance-check",
    category: "Compliance and GxP",
    title: "Compliance Check",
    description:
      "Review documents against FDA, EMA, ICH, and internal SOPs - flag GxP gaps, missing CTD sections, label-claim issues, and formatting deviations before submission.",
    steps: [
      { key: "upload", label: "Upload", hint: "Document + guideline" },
      { key: "extract", label: "Extract", hint: "Parse content" },
      { key: "compliance", label: "Compliance", hint: "Audit vs guideline" },
      { key: "export", label: "Export", hint: "Compliance report" },
      { key: "chat", label: "Chat", hint: "Ask about findings" },
    ],
  },
};

/** Ordered list - used wherever we render every workflow (e.g. dashboard). */
export const WORKFLOW_ORDER: WorkflowType[] = [
  "second-version",
  "style-update",
  "compliance-check",
];

/** Safe lookup helper used by pages outside this module. */
export function getWorkflowDefinition(
  type: WorkflowType
): WorkflowDefinition {
  const def = WORKFLOW_DEFINITIONS[type];
  if (!def) {
    throw new Error(`Unknown workflow type: ${type}`);
  }
  return def;
}

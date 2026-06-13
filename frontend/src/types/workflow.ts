/**
 * Workflow domain types.
 *
 * These types are the single source of truth for which document-processing
 * workflows Navi FormatiQ supports and what wizard steps each one renders.
 */

import type { ProjectProgress } from "./api";

export type WorkflowType =
  | "second-version"
  | "style-update"
  | "compliance-check";

/** Known wizard step keys. Routes are derived from these. */
export type WizardStepKey =
  | "upload"
  | "extract"
  | "review"
  | "compliance"
  | "style"
  | "generate"
  | "chat"
  | "export";

/** A single step in the wizard rail (Upload, Extract, ...). */
export interface WizardStep {
  /** Stable key used in URLs and analytics. */
  key: WizardStepKey;
  /** Human-readable label shown in the rail. */
  label: string;
  /** Short helper copy shown under the label on wide screens. */
  hint?: string;
}

/** Full definition of a workflow, including its wizard configuration. */
export interface WorkflowDefinition {
  type: WorkflowType;
  title: string;
  description: string;
  /** Tag shown above the title (e.g. "Drafting"). */
  category: string;
  /** Ordered wizard steps that drive subsequent pages. */
  steps: WizardStep[];
}

/** Metadata for a document/workspace shown in the Recent widget. */
export interface RecentDocument {
  id: string;
  name: string;
  /** ISO-8601 string. Rendered via Intl.DateTimeFormat. */
  lastModified: string;
  /** Workflow the project was created with, if known. */
  workflow: WorkflowType | null;
  /** Derived progress for the status badge. */
  progress: ProjectProgress;
}

/** Uploaded-file kinds the app accepts. */
export type FileKind = "docx" | "pdf";

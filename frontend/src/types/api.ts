/**
 * Backend API types.
 *
 * These mirror the Pydantic models the FastAPI backend returns
 * (app/schemas/api.py and app/schemas/document_model.py). They are the
 * single source of truth for request/response shapes used by `lib/api.ts`.
 */

/* ── projects ──────────────────────────────────────────────────────────── */

export type ProjectStatus = string; // "active" | "archived" | ...

/** Derived workflow progress shown as the dashboard status badge. */
export type ProjectProgress = "not_started" | "in_progress" | "completed";

export interface ProjectRead {
  id: string;
  name: string;
  status: ProjectStatus;
  /** Backend-derived progress (uploads + run outcomes). */
  progress?: ProjectProgress;
  flow_hint?: string | null;
  meta: Record<string, unknown>;
  current_step?: string | null;
  completion?: Record<string, boolean>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DocumentRead {
  id: string;
  kind: string; // "source" | "template" | "style" | "output"
  display_name: string;
  current_version: number;
}

export interface RunRead {
  id: string;
  flow: FlowName;
  mode?: string | null;
  status: RunStatus;
  domain_id?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface ProjectDetail extends ProjectRead {
  documents: DocumentRead[];
  runs: RunRead[];
}

export interface VersionRead {
  id: string;
  version: number;
  artifact_id?: string | null;
  created_by_run_id?: string | null;
  created_at?: string | null;
}

/* ── uploads / artifacts ───────────────────────────────────────────────── */

export interface UploadRead {
  document_id: string;
  version: number;
  artifact_id: string;
  uri: string;
  filename: string;
}

export interface ArtifactRead {
  id: string;
  uri: string;
  kind: string;
  filename: string;
  mime?: string | null;
  size_bytes: number;
}

/* ── exports (download hub) ────────────────────────────────────────────── */

/** Grouping for the export catalog; the UI renders sections in this order. */
export type ExportCategory = "document" | "formatting" | "data" | "report";

/** One downloadable deliverable the backend can produce for a finished run. */
export interface ExportItem {
  id: string;
  category: ExportCategory;
  label: string;
  description: string;
  format: string; // "docx" | "pdf" | "json"
  filename: string;
  available: boolean;
  /** Why it's unavailable (e.g. LibreOffice missing for PDF). */
  reason?: string | null;
  /** Set when the deliverable maps to a stored artifact (presign-capable). */
  artifact_id?: string | null;
  size_bytes?: number | null;
}

export interface ExportManifest {
  exports: ExportItem[];
}

/* ── flows / runs ──────────────────────────────────────────────────────── */

export type FlowName = "regenerate" | "style" | "compliance";

export type RunStatus =
  | "pending"
  | "running"
  | "hitl"
  | "done"
  | "error"
  | "cancelled";

export interface RunStarted {
  run_id: string;
  status: RunStatus;
}

/** A single section-level change proposed by the generator (run.diff). */
export interface ReviewDiff {
  slot_id: string;
  title: string;
  original: string;
  proposed: string;
  sources: string[];
  accepted?: boolean | null;
  reviewer_edit?: string | null;
}

/** A compliance issue surfaced against the domain rules (run.flags). */
export interface ComplianceFlag {
  slot_id: string;
  kind: string; // "missing" | "format" | "length" | "guidance"
  note: string;
}

export interface CoverageReport {
  missing?: string[];
  filled?: string[];
  required_total?: number;
  [key: string]: unknown;
}

export interface RunTrace {
  agent: string;
  ts?: string;
  summary?: Record<string, unknown>;
}

export interface RunDetail {
  id: string;
  flow: FlowName;
  mode?: string | null;
  status: RunStatus;
  domain_id?: string | null;
  diff: ReviewDiff[];
  flags: ComplianceFlag[];
  coverage?: CoverageReport | null;
  compliance_score?: number | null;
  traces: RunTrace[];
  rendered_docx_uri?: string | null;
  rendered_pdf_uri?: string | null;
  warnings: string[];
  error?: string | null;
  artifacts: ArtifactRead[];
  /** Flow 2: detected style-source kind, applied spec, and recognised structure. */
  style_interpretation?: StyleInterpretation | null;
  /** Flow 1: document profile (type/tone/summary). */
  doc_profile?: DocProfile | null;
  /** Flow 1: auto field-updates applied (version/date replacements + revision row). */
  field_updates?: FieldUpdates | null;
  /** Compliance audit (v2): rich multi-dimensional result. Null on other flows. */
  compliance?: ComplianceResult | null;
}

/** Flow 1 document profile + detected/updated fields. */
export interface DocProfile {
  doc_type?: string;
  summary?: string;
  tone?: string;
  document_number?: string | null;
  version?: string | null;
  new_version?: string | null;
  effective_date?: string | null;
  new_date?: string | null;
  has_revision_table?: boolean;
  author?: string | null;
}

export interface FieldUpdates {
  replacements?: { label: string; find: string; replace: string }[];
  revision?: { header: string[]; row: string[] } | null;
}

/** Counts from the structure-recognition pass over the content document. */
export interface RecognisedStructure {
  headings?: number;
  heading_levels?: Record<string, number>;
  list_items?: number;
  list_bullet?: number;
  list_numbered?: number;
  tables?: number;
  title_block?: number;
  method?: string;
}

/** What Flow 2 detected + did, stored on the run for the result view. */
export interface StyleInterpretation {
  mode_used?: "guideline" | "example";
  detected_kind?: "guideline" | "example";
  confidence?: number;
  reason?: string;
  summary?: string;
  spec?: StyleSpec | null;
  structure?: RecognisedStructure | null;
}

/** Reviewer decision sent back to resume a paused (HITL) run. */
export interface ReviewDecisionItem {
  slot_id: string;
  accepted?: boolean | null;
  reviewer_edit?: string | null;
  proposed?: string | null;
  /** Rename the section heading. */
  title?: string | null;
  /** Heading level (1-9) for a new section. */
  level?: number | null;
  /** True when this is a brand-new section to insert. */
  is_new?: boolean | null;
}

/** A single SSE event emitted while a run streams. */
export interface RunEvent {
  agent: string;
  status?: RunStatus | string;
  message?: string;
  payload?: {
    diff?: ReviewDiff[] | null;
    flags?: ComplianceFlag[] | null;
    coverage?: CoverageReport | null;
    rendered_docx_uri?: string | null;
    rendered_pdf_uri?: string | null;
    [key: string]: unknown;
  };
}

/* ── start-flow request bodies ─────────────────────────────────────────── */

export type VersionBump = "minor" | "major" | "none";

export interface RegenerateRequest {
  draft_document_id?: string;
  draft_artifact_id?: string;
  template_document_id?: string;
  template_artifact_id?: string;
  user_suggestions?: string | null;
  domain_id?: string | null;
  output_format?: string;
  /** Skip the AI body rewrite: carry sections through unchanged for manual
   * editing. Version/date/revision-row auto-updates still run. */
  skip_ai_rewrite?: boolean;
  /** How to auto-bump the version — fallback when target_version is omitted. */
  version_bump?: VersionBump;
  /** Other uploaded versions of the doc, used as context (not rewritten). */
  context_document_ids?: string[];
  /** Explicit new version number (max uploaded + 1); overrides version_bump. */
  target_version?: string | null;
}

/* ── revision suggestions (pre-run, Revise step) ───────────────────────── */

export type SuggestionKind =
  | "add_section"
  | "expand_section"
  | "revise_section";

/** One mature, content-level change suggested for the new version. */
export interface RevisionSuggestion {
  title: string;
  detail: string;
  /** Existing heading to change, or the proposed new section's heading. */
  section?: string | null;
  kind: SuggestionKind | string;
}

export interface SuggestionsResponse {
  suggestions: RevisionSuggestion[];
}

export interface SuggestRequest {
  draft_document_id?: string;
  context_document_ids?: string[];
  domain_id?: string | null;
}

/** How the uploaded style template should be interpreted. */
export type StyleSourceMode = "auto" | "guideline" | "example";

export interface StyleRequest {
  content_document_id?: string;
  content_artifact_id?: string;
  style_document_id?: string;
  style_artifact_id?: string;
  normalize_fonts?: boolean;
  promote_headings?: boolean;
  /** "auto" detects; "guideline" reads described rules; "example" copies the look. */
  style_source_mode?: StyleSourceMode;
  output_format?: string;
}

export interface StyleInterpretRequest {
  style_document_id?: string;
  style_artifact_id?: string;
  mode?: StyleSourceMode;
}

/** One heading rule lifted from a formatting guideline. */
export interface StyleSpecHeading {
  level?: number | null;
  style_name?: string | null;
  font?: string | null;
  size_pt?: number | null;
  bold?: boolean | null;
  italic?: boolean | null;
  underline?: boolean | null;
  color_hex?: string | null;
  alignment?: string | null;
  bottom_border?: boolean | null;
}

/** Look of one masthead element (title / subtitle / metadata line). */
export interface StyleTitleBlock {
  font?: string | null;
  size_pt?: number | null;
  bold?: boolean | null;
  italic?: boolean | null;
  color_hex?: string | null;
  alignment?: string | null;
}

/** The structured style rules extracted from a guideline (subset used for display). */
export interface StyleSpec {
  page?: {
    width_in?: number | null;
    height_in?: number | null;
    orientation?: string | null;
    margin_top_in?: number | null;
    margin_bottom_in?: number | null;
    margin_left_in?: number | null;
    margin_right_in?: number | null;
  };
  body?: {
    font?: string | null;
    size_pt?: number | null;
    color_hex?: string | null;
    alignment?: string | null;
    line_spacing?: number | null;
    space_after_pt?: number | null;
  };
  headings?: StyleSpecHeading[];
  table?: {
    header_fill_hex?: string | null;
    header_text_hex?: string | null;
    header_bold?: boolean | null;
    alt_row_fill_hex?: string | null;
    border_color_hex?: string | null;
  };
  title?: StyleTitleBlock;
  subtitle?: StyleTitleBlock;
  metadata?: StyleTitleBlock;
  lists?: {
    bullet_char?: string | null;
    bullet_indent_in?: number | null;
    space_after_pt?: number | null;
  };
  header_footer?: Record<string, unknown>;
  colors?: Record<string, string>;
  accent_color_hex?: string | null;
  notes?: string[];
}

export interface StyleInterpretResponse {
  effective_kind: StyleSourceMode | "guideline" | "example";
  detected_kind: "guideline" | "example";
  confidence: number;
  reason: string;
  method: "llm" | "heuristic" | "forced";
  summary: string;
  spec?: StyleSpec | null;
  notes?: string[];
  palette?: Record<string, string>;
}

export interface ComplianceRequest {
  draft_document_id?: string;
  draft_artifact_id?: string;
  template_document_id?: string;
  template_artifact_id?: string;
  domain_id?: string;
  /** The pre-loaded guideline (e.g. ICH-E3) to audit against. */
  guideline_id?: string;
  /** Optional subset of dimensions to check; omit for all five. */
  dimensions?: ComplianceDimension[];
  mode?: "apply" | "check";
  output_format?: string;
}

/* ── compliance audit (guidelines) ─────────────────────────────────────── */

export type ComplianceDimension =
  | "content"
  | "structure"
  | "formatting"
  | "style"
  | "tone";

export type FindingStatus =
  | "compliant"
  | "partial"
  | "non_compliant"
  | "not_applicable";

export type FindingSeverity = "critical" | "major" | "minor" | "info";

/** Row from GET /guidelines (the Upload-page selector + catalog). */
export interface Guideline {
  id: string;
  code: string; // "ICH-E3"
  title: string;
  domain: string;
  version?: string | null;
  description?: string | null;
  status: string; // "ready" for published
  requirement_count?: number;
  dimension_coverage?: ComplianceDimension[];
}

export interface GuidelineSection {
  section_no?: string | null;
  title: string;
  level: number;
}

export interface GuidelineDetail extends Guideline {
  sections: GuidelineSection[];
  page_count?: number | null;
}

/** Result of manually ingesting a guideline PDF (POST /guidelines). */
export interface GuidelineIngestResult {
  id: string;
  code: string;
  status: string;
  sections: number;
  requirements: number;
  indexed_chunks: number;
}

/** One audited requirement (a rich compliance finding). */
export interface ComplianceFinding {
  id: string;
  section?: string | null; // section_no
  section_title?: string | null;
  requirement_title: string;
  dimension: ComplianceDimension;
  status: FindingStatus;
  severity: FindingSeverity;
  confidence?: number | null;
  evidence?: string | null; // quote from the user's document
  doc_location?: string | null;
  rationale?: string | null;
  citation?: { guideline_section?: string; quote?: string } | null;
  suggested_fix?: string | null;
}

export interface ComplianceSectionScore {
  section: string;
  title?: string;
  score: number | null; // 0..1
  status: FindingStatus | string;
  findings_count: number;
  total?: number;
}

/** The rich, multi-dimensional audit result on RunDetail.compliance. */
export interface ComplianceResult {
  overall_score: number; // 0..1
  status_label?: string | null; // strong | moderate | weak | failing
  per_dimension: Partial<Record<ComplianceDimension, number>>;
  per_section: ComplianceSectionScore[];
  severity_counts: Partial<Record<FindingSeverity, number>>;
  findings: ComplianceFinding[];
  summary?: string | null;
  guideline?: { id?: string; code?: string; title?: string; version?: string | null } | null;
}

/* ── chat ──────────────────────────────────────────────────────────────── */

export interface ChatSessionRead {
  id: string;
  project_id?: string | null;
  title: string;
  subject_document_id?: string | null;
  subject_run_id?: string | null;
  guideline_id?: string | null;
}

export interface ChatMessageRead {
  id: string;
  role: "user" | "assistant" | "system" | string;
  content: string;
  tool_name?: string | null;
  tool_result_ref?: Record<string, unknown> | null;
  created_at?: string | null;
}

export interface ChatTurnResponse {
  answer: string;
  steps: Array<Record<string, unknown>>;
}

/* ── domains ───────────────────────────────────────────────────────────── */

export interface DomainRead {
  slug: string;
  name: string;
  has_corpus: boolean;
  qdrant_collection?: string | null;
}

/* ── document content / styling (extraction output) ────────────────────── */

export type ElementType =
  | "heading"
  | "paragraph"
  | "image"
  | "table"
  | "list_item"
  | "page_break"
  | "section_break"
  | "hyperlink"
  | "header"
  | "footer";

export interface RunStyle {
  font_name?: string | null;
  font_size_pt?: number | null;
  bold?: boolean | null;
  italic?: boolean | null;
  underline?: string | null;
  color_hex?: string | null;
  all_caps?: boolean | null;
  small_caps?: boolean | null;
  [key: string]: unknown;
}

export interface TextRun {
  text: string;
  style_ref?: string | null;
  inline_style?: RunStyle | null;
  hyperlink_url?: string | null;
}

export interface ParagraphStyle {
  alignment?: "left" | "center" | "right" | "justify" | null;
  space_before_pt?: number | null;
  space_after_pt?: number | null;
  line_spacing?: number | null;
  outline_level?: number | null;
  [key: string]: unknown;
}

export interface TableCell {
  content: TextRun[];
  style_ref?: string | null;
}

export interface TableRow {
  cells: TableCell[];
  is_header: boolean;
}

export interface ContentElement {
  type: ElementType;
  level?: number | null;
  content?: TextRun[] | null;
  style_ref?: string | null;
  inline_style?: ParagraphStyle | null;
  rows?: TableRow[] | null;
  list_type?: "bullet" | "numbered" | null;
  list_level?: number | null;
  alt_text?: string | null;
  url?: string | null;
}

export interface DocumentMetadata {
  source_file?: string | null;
  source_type?: string | null; // "docx" | "pdf"
  extracted_at?: string | null;
  page_count?: number | null;
  author?: string | null;
  title?: string | null;
}

export interface DocumentContent {
  metadata: DocumentMetadata;
  elements: ContentElement[];
}

export interface PageMargins {
  top_inches: number;
  bottom_inches: number;
  left_inches: number;
  right_inches: number;
}

export interface PageStyle {
  width_inches: number;
  height_inches: number;
  orientation: "portrait" | "landscape";
  margins: PageMargins;
}

export interface DocumentStyling {
  metadata: { source_file?: string | null; created_at?: string | null };
  page_style: PageStyle;
  paragraph_styles: Record<string, ParagraphStyle>;
  run_styles: Record<string, RunStyle>;
  table_styles: Record<string, unknown>;
  cell_styles: Record<string, unknown>;
}

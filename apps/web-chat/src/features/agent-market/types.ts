// Agent Market (AM1) — wire shapes of the control plane at
// /api/v1/agent-market (services/chat-edge-api/internal/agentmarket).
// Money values are decimal STRINGS: they are formatted for display and never
// used in arithmetic here.

export interface ManifestInput {
  role: string;
  label: string;
  note?: string;
  required?: boolean;
  accept?: string[];
}

export interface ManifestOutput {
  format: string;
  label: string;
  default?: boolean;
}

export interface Manifest {
  schema_version?: string;
  id?: string;
  version?: string;
  name?: string;
  name_en?: string;
  short_name?: string;
  category?: string;
  icon?: string;
  summary?: string;
  typical_run_seconds?: number;
  greeting?: { text?: string; role?: string; tagline?: string };
  inputs?: ManifestInput[];
  outputs?: ManifestOutput[];
  capabilities?: { required?: string[]; optional?: string[] };
  install?: { defaults?: Record<string, unknown>; questions?: string[] };
  permissions?: { default?: string; denied?: string[] };
  cannot?: string;
  limits?: { max_file_mb?: number; max_files?: number };
  /** AM2: "rules" = the package's own summary, no model step (no UI). */
  narrative?: string;
  /** AM2: the install keeps a duplicate index of recorded documents (no UI). */
  document_index?: { retention_days?: number } | null;
  /** AM2: shown under the attached files when one of them is a photo. */
  photo_notice?: string;
}

export interface CatalogAgent {
  slug: string;
  version: string;
  name: string;
  manifest: Manifest;
  install_id?: string;
  sample_run_id?: string;
}

export interface CatalogItem {
  slug: string;
  version: string;
  name: string;
  manifest: Manifest;
  checksum?: string;
}

export interface Install {
  id: string;
  definition_id?: string;
  slug: string;
  definition_version: string;
  checksum?: string;
  title: string;
  binding_version?: number;
  budget_credits?: number;
  status?: string;
  self_test_run_id?: string;
  created_at: string;
  updated_at?: string;
}

export interface InstallRow extends Install {
  icon?: string;
  last_run?: Run;
  attention?: boolean;
}

export type DefaultSource = "install" | "answer" | "settings";

export interface Default {
  key: string;
  value: unknown;
  source: DefaultSource | string;
  label: string;
  note: string;
  from_run_id?: string;
  updated_at: string;
}

export type RunStatus = "queued" | "running" | "awaiting_input" | "succeeded" | "failed" | "cancelled";
export type RunKind = "run" | "self_test" | "render" | "reply" | "mapping_preview";

export interface Assumption {
  code: string;
  source: string;
  text: string;
  changes_numbers?: boolean;
}

export interface Run {
  id: string;
  install_id: string;
  parent_run_id?: string;
  kind: RunKind | string;
  status: RunStatus | string;
  status_reason?: string;
  period?: string | null;
  message?: string;
  definition_version?: string;
  formats?: unknown;
  answers?: unknown;
  assumptions?: Assumption[] | null;
  credits_spent?: number;
  model_used?: string | null;
  model_self_hosted?: boolean | null;
  awaiting_ms?: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface Step {
  id: string;
  run_id?: string;
  seq: number;
  name: string;
  kind: string;
  status: string;
  input?: unknown;
  output?: unknown;
  script_id?: string;
  model_id?: string;
  error?: string;
  attempt?: number;
  credits?: number;
  started_at?: string;
  finished_at?: string | null;
  duration_ms?: number | null;
}

export interface QualityCheck {
  name?: string;
  label?: string;
  ok: boolean;
  detail?: string;
}

export interface Quality {
  passed?: boolean;
  checks?: QualityCheck[];
}

export interface Artifact {
  id: string;
  run_id?: string;
  format: string;
  kind?: string;
  name: string;
  version: number;
  size: number;
  sha256?: string;
  theme_id?: string;
  theme_version?: number;
  validation?: Quality | null;
  citations?: unknown;
  created_at: string;
}

export interface AmFile {
  id: string;
  install_id?: string;
  run_id?: string;
  name: string;
  content_type?: string;
  size: number;
  sha256?: string;
  status?: string;
  client_id?: string;
  created_at?: string;
}

export interface QuestionOption {
  label: string;
  description?: string;
  value?: unknown;
}

export interface FileRequest {
  role?: string;
  label: string;
  why?: string;
  required?: boolean;
  accept?: string[];
}

export type InputKind = "money" | "tax_id" | "date" | "rate" | "text";

/** AM2: a question that also takes a typed value (the card's own "something
 *  else" box). The server records the typed words as {"typed": text} and
 *  the package that asked checks them again. */
export interface QuestionInput {
  kind: InputKind | string;
  hint?: string;
}

export interface Question {
  key: string;
  code: string;
  severity: "blocking" | "cosmetic" | string;
  header: string;
  question: string;
  multiSelect?: boolean;
  options: QuestionOption[];
  default?: unknown;
  default_label?: string;
  evidence?: unknown;
  rememberable?: boolean;
  request?: FileRequest[] | null;
  input?: QuestionInput | null;
}

export interface AnswerRecord {
  value?: unknown;
  label?: string;
  via?: string;
  moment?: string;
  card_id?: string;
  cosmetic?: boolean;
  code?: string;
}

export type CardKind = "questions" | "request_files" | "late_file" | "output_format";
export type CardStatus = "open" | "interpreting" | "answered" | "defaulted" | "superseded" | "cancelled";

/** A clarification card as the run detail returns it (a DB row). */
export interface Clarification {
  id: string;
  run_id: string;
  moment: string;
  seq: number;
  kind: CardKind | string;
  blocking: boolean;
  questions: Question[] | null;
  status: CardStatus | string;
  answer?: Record<string, AnswerRecord> | null;
  answer_text?: string | null;
  answered_via?: string | null;
  answered_at?: string | null;
  attempts: number;
  created_at: string;
}

// ── thread ─────────────────────────────────────────────────────────────

export type ThreadKind = "greeting" | "user_text" | "user_files" | "agent_text" | "notice" | "card" | "progress" | "outputs";

export interface ThreadItem {
  id: string;
  install_id: string;
  run_id?: string;
  kind: ThreadKind | string;
  payload: unknown;
  client_id?: string;
  rev: number;
  created_at: string;
  updated_at?: string;
}

export interface GreetingPayload {
  text?: string;
  role?: string;
  tagline?: string;
  needs?: { role?: string; label: string; note?: string; required?: boolean }[] | null;
  produces?: { format: string; label: string }[] | null;
  cannot?: string;
  icon?: string;
  slug?: string;
  version?: string;
}

export interface UserTextPayload {
  text?: string;
  status?: "pending" | "handled" | string;
  handled_as?: string;
}

export interface UserFilesPayload {
  text?: string;
  files?: { id: string; name: string; size: number }[] | null;
}

export interface AgentTextPayload {
  text?: string;
  run_id?: string;
  tone?: "error" | string;
}

export interface NoticePayload {
  text?: string;
  link?: "defaults" | string;
}

export interface CardPayload {
  card_id: string;
  run_id: string;
  kind: CardKind | string;
  moment?: string;
  blocking?: boolean;
  status: CardStatus | string;
  intro?: string;
  questions: Question[] | null;
  answer?: Record<string, AnswerRecord> | null;
  answered_via?: string;
  attempts?: number;
  note?: string;
  files?: { id: string; name: string; size: number }[] | null;
}

export type ProgressStepStatus = "pending" | "running" | "done" | "waiting" | "failed" | "skipped";

export interface ProgressPayload {
  run_id: string;
  kind?: string;
  status: RunStatus | string;
  reason?: string;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_ms?: number;
  awaiting_ms?: number;
  steps?: { key: string; label: string; status: ProgressStepStatus | string; detail?: string }[] | null;
}

export interface OutputArtifact {
  id: string;
  name: string;
  format: string;
  size: number;
  version: number;
  quality?: Quality | null;
}

export interface OutputsPayload {
  run_id: string;
  period_label?: string;
  artifacts?: OutputArtifact[] | null;
  summary?: {
    period_label?: string;
    revenue?: string | null;
    total_expenses?: string | null;
    net_profit?: string | null;
    rows_used?: number;
    exceptions?: number;
    [k: string]: unknown;
  } | null;
  model?: { model?: string; self_hosted?: boolean } | null;
  credits?: number;
  rerender?: boolean;
}

// ── responses ──────────────────────────────────────────────────────────

export interface Availability {
  available: boolean;
  runner?: { healthy?: boolean; seen_at?: string; version?: string };
}

export interface InstallResponse {
  install: Install;
  created: boolean;
  self_test_run_id?: string;
}

export interface InstallDetail {
  install: Install;
  manifest: Manifest;
  defaults: Default[] | null;
  budget: { credits: number; spent_this_month: number };
}

export interface RunDetail {
  run: Run;
  steps: Step[] | null;
  clarifications: Clarification[] | null;
  artifacts: Artifact[] | null;
  files: AmFile[] | null;
  children: Run[] | null;
  install: { id: string; title: string; slug: string };
  budget: { credits: number; spent_this_month: number };
}

export interface ThemePreset {
  id: string;
  name: string;
}

export interface ThemeResponse {
  theme: { id?: string; company?: string; [k: string]: unknown } | null;
  version: number;
  presets: ThemePreset[];
}

export interface MappingPreview {
  sheet?: string;
  header_row?: number;
  headers: string[];
  rows: string[][];
  proposal: Record<string, { header: string; source?: string | null }>;
  candidates?: Record<string, unknown>;
  fields?: Record<string, string>;
  doc_type?: string;
}

export interface Submitted {
  labels?: string[];
  text?: string;
  /** Skip: the labels are the recommended default, not the person's pick. */
  skipped?: boolean;
}

export interface AnswerBody {
  answers: Record<string, Submitted>;
  text?: string;
}

// computed.json (run detail "ตารางตัวเลข")
export interface ComputedLine {
  key: string;
  label: string;
  current: string | null;
  prior?: string | null;
  budget?: string | null;
  var_prior?: string | null;
  var_prior_pct?: string | null;
  var_budget?: string | null;
  var_budget_pct?: string | null;
  cite?: string | null;
}

export interface ComputedRatio {
  key: string;
  label: string;
  current: string | null;
  prior?: string | null;
  change_points?: string | null;
  cite?: string | null;
}

export interface ComputedAccount {
  code: string;
  name?: string;
  group?: string;
  current?: string | null;
  prior?: string | null;
  budget?: string | null;
  var_prior?: string | null;
  var_prior_pct?: string | null;
  var_budget?: string | null;
  rows?: number;
  cite?: string | null;
}

export interface ComputedException {
  code: string;
  title: string;
  detail?: string;
  account?: string | null;
  amount?: string | null;
  rows?: number[] | null;
  cite?: string | null;
}

export interface Citation {
  id: string;
  label: string;
  value?: string | null;
  sources?: { file?: string; sheet?: string; cells?: string; rows?: unknown }[] | null;
  derived_from?: string[] | null;
  script?: string;
  formula?: string;
}

export interface Computed {
  period_label?: string;
  prior_label?: string;
  has_prior?: boolean;
  has_budget?: boolean;
  lines?: ComputedLine[] | null;
  ratios?: ComputedRatio[] | null;
  accounts?: ComputedAccount[] | null;
  exceptions?: ComputedException[] | null;
  excluded?: { code: string; reason: string; rows?: number | number[] }[] | null;
  citations?: Citation[] | null;
  source?: { files?: { file: string; sha256?: string; role?: string; rows?: number }[] } | null;
  balance?: { debit?: string; credit?: string; diff?: string } | null;
}

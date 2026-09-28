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
  /** AM2: what the agent can put into the customer's own template. */
  templates?: TemplateSpec | null;
  /** AM3: what a stranger reads before installing. */
  guide?: ManifestGuide | null;
}

// ── the customer's own template (AM2 Phase E, agentmarket/template.go) ──

export interface TemplateField {
  label?: string;
  aliases?: string[];
}

export interface TemplateList {
  label?: string;
  aliases?: string[];
  /** column key → its Thai label */
  columns?: Record<string, string>;
}

/** manifest.templates: single values (fields) and row lists (lists). */
export interface TemplateSpec {
  fields?: Record<string, TemplateField | string>;
  lists?: Record<string, TemplateList>;
}

export type TemplateSlotKind = "marker" | "list" | "control" | "name" | "table" | "target";

/** One place a template takes a value (deliverables/template_out.slots). */
export interface TemplateSlot {
  key: string;
  kind: TemplateSlotKind | string;
  label: string;
  where?: string;
  fillable: boolean;
  /** why it cannot be filled: formula_cell, protected_sheet, not_plain_text, … */
  reason: string | null;
  /** a list's {{name.x}} sub-keys, a table's column letters */
  columns?: string[];
}

export interface TemplateDiff {
  first: boolean;
  kept: string[];
  added: string[];
  removed: string[];
}

/** The inspect job's step output (the step named "template:<file>"). */
export interface TemplateInspect {
  inspection?: Record<string, unknown>;
  slots: TemplateSlot[];
  /** slot → field / list; "slot.column" → "list.column"; the saved choice for kept slots */
  proposal: Record<string, string>;
  diff: TemplateDiff;
}

/** The saved template (install default "template"). */
export interface SavedTemplate {
  file_id: string;
  name: string;
  kind: string;
  sha256?: string;
  version: number;
  slots: TemplateSlot[] | null;
  mapping: Record<string, string> | null;
  saved_at: string;
}

export interface CatalogAgent {
  slug: string;
  version: string;
  name: string;
  manifest: Manifest;
  install_id?: string;
  sample_run_id?: string;
  /** AM3: the organisation's catalog decision (absent before AM3 = published). */
  status?: CatalogStatus | string;
  status_reason?: string;
  sample?: SampleSummary | null;
  stats?: AgentStats | null;
}

export interface CatalogItem {
  slug: string;
  version: string;
  name: string;
  manifest: Manifest;
  checksum?: string;
  status?: CatalogStatus | string;
  status_reason?: string;
  install_id?: string | null;
  sample?: SampleSummary | null;
  stats?: AgentStats | null;
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
  /** AM3: pinned to the top of the rail. */
  pinned_at?: string | null;
  /** AM4: the model the person picked for reading documents ("auto" or a curated choice). */
  model_choice?: string;
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
export type RunKind = "run" | "self_test" | "render" | "reply" | "mapping_preview" | "review";

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
  /** AM4: the thread (absent = the first), the model choice it ran with, an admin's removal of its files. */
  thread_id?: string | null;
  model_choice?: string | null;
  evidence_removed_at?: string | null;
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

export type ThreadKind = "greeting" | "user_text" | "user_files" | "agent_text" | "notice" | "card" | "progress" | "outputs" | "intent";

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
  /** AM4: absent = the install's first thread. */
  thread_id?: string | null;
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
  /** AM3: "warning" / "success" notices; "resume_budget" offers ลองต่อ. */
  tone?: "warning" | "success" | string;
  action?: "resume_budget" | string;
  run_id?: string;
  reason?: string;
  contact?: Contact | null;
  /** AM4: "เริ่มรอบใหม่" was pressed; an admin opened this run (with a reason). */
  round_start?: boolean;
  access?: boolean;
}

/** AM4: the question at a round boundary — start a new round, or ask about the last one. */
export interface IntentPayload {
  status: "open" | "chosen" | string;
  choice?: "new_round" | "about_last" | string;
  text?: string;
  files?: { id: string; name: string; size: number }[] | null;
  item_id?: string;
  last_round?: { run_id?: string; period_label?: string } | null;
  fresh?: boolean;
  model_choice?: string;
  run_id?: string;
  note?: string;
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
  model?: OutputsModel | null;
  credits?: number;
  rerender?: boolean;
  /** AM3: these files carry a reviewer's sign-off on their face. */
  review?: { id: string; reviewer: string; at_label: string; note?: string; skipped?: string[] | null } | null;
}

/** AM4: what the files card always says about models. */
export interface OutputsModel {
  model?: string;
  self_hosted?: boolean;
  /** what a model does for this agent: vision (reads scans) | text (writes the words) | none */
  uses?: "vision" | "text" | "none" | string;
  choice?: string;
  chosen_model?: string;
  not_chosen?: boolean;
  vision_model?: string;
  vision_self_hosted?: boolean;
  written_by_rules?: boolean;
}

/** AM4: one conversation with an installed agent (the first has no id). */
export interface AgentThread {
  id: string | null;
  title: string;
  created_at: string;
  last_at: string;
  rounds: number;
  waiting: boolean;
}

/** AM4: one row of the model picker. */
export interface ModelOption {
  id: string;
  outcome: "auto" | "fast" | "accurate" | string;
  label: string;
  model: string;
  self_hosted: boolean;
  cost_thb: number | null;
  seconds: number | null;
  disabled: boolean;
  reason?: string;
}

/** AM5: one model of the runner's catalogue as "เลือกโมเดลเอง" may offer it. */
export interface CustomModel {
  id: string;
  self_hosted: boolean;
  vision: boolean;
  allowed: boolean;
  reason?: string;
}

export interface ModelPicker {
  uses: "vision" | "text" | "none" | string;
  reads: string;
  choice: string;
  options: ModelOption[] | null;
  documents_may_leave: boolean;
  note: string;
  /** AM5: the organisation has one model of its own for this job (notice says so in one line). */
  single_in_house?: boolean;
  notice?: string;
  /** AM5: what "เลือกโมเดลเอง" may offer, and what a model missing from it says. */
  custom?: CustomModel[] | null;
  locked_reason?: string;
}

/** AM4: what an install keeps and for how long. */
export interface StorageInfo {
  storage: { uploads: number; outputs: number; total: number; warn_bytes: number; warn: boolean };
  retention_years: number;
  retention: string;
  not_archive: string;
  personal_delete: string;
}

/** AM4: an admin's opening of someone's run, as its owner sees it. */
export interface AccessEntry {
  admin_name: string;
  admin_email: string;
  reason: string;
  opened_at: string;
  at_label: string;
}

/** AM4: what an admin sees of any run — never its files, figures or answers. */
export interface RunMeta {
  id: string;
  agent: string;
  slug: string;
  person: string;
  email: string;
  status: RunStatus | string;
  reason: string;
  period_label: string;
  credits: number;
  model: string;
  model_self_hosted: boolean | null;
  stopped_at_step?: string;
  created_at: string;
  finished_at?: string | null;
  open_now: boolean;
  times_opened: number;
}

export interface CatalogModel {
  id: string;
  vision: boolean;
  self_hosted: boolean;
  input_price_per_1m: number;
  output_price_per_1m: number;
}

export interface ModelEntry {
  model: string;
  outcome: "fast" | "accurate" | string;
}

export interface AdminModels {
  catalog: CatalogModel[] | null;
  curated: ModelEntry[] | null;
  defaults: { text?: string; text_second?: string; vision?: string };
  self_hosted: Record<string, boolean> | null;
  documents_may_leave: boolean;
  seen_at?: string | null;
}

// ── responses ──────────────────────────────────────────────────────────

export interface Availability {
  available: boolean;
  runner?: { healthy?: boolean; seen_at?: string; version?: string };
  /** AM3: the organisation's switch, this person's place in it, whom to ask. */
  access?: AccessMode | string;
  member?: boolean;
  role?: OrgRole | string;
  admin?: boolean;
  publisher?: boolean;
  contact?: Contact | null;
  pricing?: Pricing | null;
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
  /** AM4: every time an admin opened this run, and why. */
  access_log?: AccessEntry[] | null;
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

// ── AM2 phase F/G: the expense policy, run exceptions, upgrades, the dashboard ──

/** One expense category of the policy table (limits are plain amounts as text). */
export interface PolicyCategory {
  label?: string;
  per_claim?: string | number | null;
  per_person_month?: string | number | null;
  needs_purpose?: boolean;
  /** percent, 0 < rate <= 100 */
  wht_rate?: string | number | null;
  wht_from?: string | number | null;
  [k: string]: unknown;
}

/** Install setting "policy" (schema am2.expense_policy.v1): the only source of a limit. */
export interface ExpensePolicy {
  schema?: string;
  /** "package_sample" until the person edits it, then "install" */
  source?: string;
  currency?: string;
  receipt_required_above?: string | number | null;
  approval_threshold?: string | number | null;
  categories?: Record<string, PolicyCategory>;
  prohibited_merchants?: string[];
  weekend_holiday?: "flag" | "exception" | "allow" | string;
  vat_claim_requires?: "full_tax_invoice" | "abbreviated_tax_invoice" | string;
  [k: string]: unknown;
}

export interface ExceptionChoice {
  label: string;
  value: unknown;
}

export interface KbQuote {
  file?: string;
  paragraph?: number | string;
  text?: string;
  used_as_limit?: boolean;
}

/** What a run exception points at (the package's evidence plus subject / reason / detail). */
export interface ExceptionSourceRef {
  subject?: string;
  reason?: string;
  detail?: string;
  claim?: string;
  sheet_name?: string;
  row?: number | string;
  cell?: string;
  receipt?: string | null;
  page?: number | string | null;
  raw_text?: string | null;
  limit?: string | number | null;
  limit_source?: string;
  fact?: string;
  kb_context?: KbQuote[] | null;
  also_flagged?: { code: string; reason?: string; detail?: string }[] | null;
  answered?: string;
  decision?: { choice?: unknown; label?: string; note?: string } | null;
  [k: string]: unknown;
}

export interface RunExceptionRow {
  id: string;
  run_id: string;
  severity: "review" | "info" | string;
  reason_code: string;
  proposed_action?: string;
  choices?: ExceptionChoice[] | null;
  status: "open" | "resolved" | "noted" | string;
  resolved_at?: string | null;
  created_at: string;
  source_ref?: ExceptionSourceRef | null;
}

export interface DefinitionSummary {
  id: string;
  version: string;
  name?: string;
  checksum?: string;
  released_at?: string;
}

export interface VersionChange {
  what: "manifest" | "capability" | "setting" | "instructions" | string;
  key: string;
  before?: string;
  after?: string;
}

export interface UpgradeInfo {
  current: DefinitionSummary;
  previous?: DefinitionSummary | null;
  available: DefinitionSummary | null;
  changes?: VersionChange[] | null;
}

export interface DashCount {
  key: string;
  count: number;
  open?: number;
}

/** GET /installs/{id}/dashboard (agentmarket/dashboard.go). */
export interface AgentDashboard {
  since: string;
  days: number;
  version: string;
  runs: {
    total: number;
    by_status: Record<string, number>;
    succeeded: number;
    first_pass: number;
    first_pass_rate: number | null;
  };
  time: {
    queue_p50_s: number | null;
    queue_p90_s: number | null;
    work_p50_s: number | null;
    work_p90_s: number | null;
    human_wait_p50_s: number | null;
    human_wait_p90_s: number | null;
  };
  waiting: { runs: number; oldest_run_s: number | null; open_exceptions: number; oldest_exception_s: number | null };
  questions: DashCount[] | null;
  exceptions: { open: number; resolved: number; noted: number; by_reason: DashCount[] | null; choices: DashCount[] | null };
  failures: DashCount[] | null;
  confidence: Record<string, number> | null;
  tiers: Record<string, number> | null;
  documents: number;
  artifacts: DashCount[] | null;
  versions: { version: string; runs: number; last_run_at: string }[] | null;
  spend: { credits: number; per_succeeded_run: number | null; per_document: number | null; budget_credits: number };
  models: { model: string; self_hosted: boolean; runs: number }[] | null;
  last_self_test: { status: string; reason?: string; version?: string; finished_at?: string | null } | null;
}

// ── AM3: self-serve (agentmarket/org.go, catalog.go, rail.go, budget.go,
//    share.go, review.go, problems.go, admin.go) ────────────────────────

export type AccessMode = "off" | "list" | "everyone";
export type OrgRole = "member" | "publisher" | "admin" | "";

export interface Contact {
  name: string;
  email: string;
}

export interface Pricing {
  credits_per_usd: number;
  thb_per_usd: number;
}

/** manifest.guide — what a stranger reads before installing (all optional). */
export interface ManifestGuide {
  for?: string;
  how?: string[];
  inputs?: Record<string, { good?: string[]; avoid?: string[]; sample?: string[] }>;
  outputs?: Record<string, string>;
  questions?: string;
  model_inputs?: Record<string, string>;
  never?: string[];
  wrong_file?: string;
  time_note?: string;
}

export type CatalogStatus = "published" | "unpublished" | "disabled";

export interface SampleSummary {
  run_id: string;
  version: string;
  status: RunStatus | string;
  reason?: string;
  credits: number;
  seconds: number | null;
  finished_at?: string | null;
}

export interface AgentStats {
  runs: number;
  users: number;
  median_credits: number | null;
  median_seconds: number | null;
  per_document: number | null;
  documents: number;
}

export interface CatalogSample {
  sample: SampleSummary;
  artifacts: Artifact[] | null;
  files: AmFile[] | null;
}

export interface DatasheetModel {
  capability: string;
  sees: string;
  model: string;
  role: "primary" | "fallback" | "vision" | string;
  self_hosted: boolean;
}

export interface DatasheetRetention {
  what: string;
  keep: number;
  unit: "hours" | "days" | "years" | "while_installed" | "account_deletion" | string;
}

export interface Datasheet {
  slug: string;
  version: string;
  name: string;
  summary: string;
  reads: { role: string; label: string; note?: string; required?: boolean; accept?: string[] }[] | null;
  limits: { max_file_mb?: number; max_files?: number } | null;
  produces: { format: string; label: string; default?: boolean }[] | null;
  models: DatasheetModel[] | null;
  no_model: string[] | null;
  storage: { backend?: string; prefix?: string } | null;
  retention: DatasheetRetention[] | null;
  isolation: string[] | null;
  permissions: { default?: string; denied?: string[] } | null;
  never: string[] | null;
  cannot: string;
  status: CatalogStatus | string;
  runner: { healthy?: boolean; version?: string; seen_at?: string } | null;
  generated_at: string;
  /** AM4: e.g. not_archive — this is not the company's document archive. */
  notes?: Record<string, string> | null;
}

export interface RailInstall {
  id: string;
  slug: string;
  title: string;
  icon: string;
  version: string;
  pinned_at: string | null;
  last_used_at: string | null;
  waiting: number;
  disabled: boolean;
}

export interface RailPrefs {
  rail_collapsed?: boolean;
  rail_show_all?: boolean;
}

export interface RailResponse {
  installs: RailInstall[] | null;
  waiting_total: number;
  prefs: RailPrefs | null;
  shared_unopened?: number;
}

export interface BudgetState {
  user_limit: number;
  user_spent: number;
  user_own_limit: boolean;
  department?: string;
  department_limit?: number | null;
  department_spent: number;
  install_limit: number;
  install_spent: number;
  exhausted: "" | "user" | "department" | "install" | string;
  contact: Contact;
}

export interface Review {
  id: string;
  run_id: string;
  reviewer_email: string;
  reviewer_name: string;
  note: string;
  via_share_id?: string;
  stamp_run_id?: string;
  artifact_versions?: Record<string, number> | null;
  created_at: string;
  stamp_status?: RunStatus | string;
}

export interface Share {
  id: string;
  run_id: string;
  recipient_email: string;
  can_review: boolean;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_opened_at: string | null;
  opens: number;
}

export interface SharedItem {
  id: string;
  can_review: boolean;
  created_at: string;
  expires_at: string | null;
  last_opened_at: string | null;
  run_id: string;
  run_status: string;
  period: string | null;
  finished_at: string | null;
  agent: string;
  slug: string;
  owner: string;
}

export interface SharedRun {
  share: Share;
  owner: string;
  agent: { title: string; name: string; slug: string; version: string };
  run: {
    id: string;
    status: string;
    status_reason?: string;
    period: string | null;
    created_at: string;
    finished_at: string | null;
    assumptions?: Assumption[] | null;
    model_used?: string | null;
    model_self_hosted?: boolean | null;
  };
  artifacts: Artifact[] | null;
  exceptions: RunExceptionRow[] | null;
  reviews: Review[] | null;
  outputs?: OutputsPayload | null;
}

export interface Person {
  email: string;
  name: string;
}

export interface ProblemReport {
  id: string;
  ref: string;
  email: string;
  install_id?: string;
  run_id?: string;
  slug: string;
  version: string;
  run_status: string;
  run_reason: string;
  step: string;
  step_error: string;
  page: string;
  text: string;
  status: "open" | "closed" | string;
  admin_note: string;
  closed_by: string;
  created_at: string;
  closed_at?: string | null;
}

export interface OrgSettings {
  org_id: string;
  name: string;
  access: AccessMode | string;
  domains: string[] | null;
  user_monthly_credits: number;
  contact_name: string;
  contact_email: string;
  updated_by: string;
  updated_at: string;
  /** AM4 */
  documents_may_leave?: boolean;
  agent_models?: ModelEntry[] | null;
  retention_years?: number;
  storage_warn_mb?: number;
}

export interface OrgMember {
  email: string;
  user_id?: string;
  display_name: string;
  department: string;
  role: OrgRole | string;
  monthly_credits: number | null;
  added_by: string;
  created_at?: string;
  updated_at?: string;
  env_admin?: boolean;
}

export interface OrgDepartment {
  name: string;
  monthly_credits: number | null;
  contact_name: string;
  contact_email: string;
  members: number;
}

export interface AdminOrg {
  org: OrgSettings;
  members: OrgMember[] | null;
  departments: OrgDepartment[] | null;
  pricing?: Pricing;
}

export interface SpendRow {
  key: string;
  email?: string;
  name?: string;
  department?: string;
  runs: number;
  succeeded: number;
  failed: number;
  waiting: number;
  users?: number;
  credits: number;
  month_spent: number;
  monthly_limit?: number | null;
  last_run_at: string | null;
}

export interface AdminSpend {
  days: number;
  since: string;
  by_user: SpendRow[] | null;
  by_agent: SpendRow[] | null;
  total_credits: number;
  catalog_samples_credits: number;
  pricing?: Pricing;
}

export interface AdminCatalogRow extends CatalogAgent {
  changed_by?: string;
  changed_at?: string | null;
  installs: number;
}

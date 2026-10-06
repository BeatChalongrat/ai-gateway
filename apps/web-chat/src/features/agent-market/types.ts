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
  /** AM11: the languages the package itself speaks — its questions, its figures' labels, its documents
   *  (none said = Thai only). The screen around an agent follows the app's language either way. */
  languages?: string[];
  schema_version?: string;
  id?: string;
  version?: string;
  /** AM12: "request" — a run of the agent starts from what a person types, not from a file. */
  starts_from?: string;
  name?: string;
  name_en?: string;
  short_name?: string;
  category?: string;
  icon?: string;
  /** AM10: the package's own icon (a file of the package) and its colour per theme; `icon_svg` is
   *  the file as the runner registered it (checked server-side; ui.tsx checks it again). */
  icon_file?: string;
  accent?: Accent | null;
  icon_svg?: string;
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
  /** AM6: whom the agent is made for, and whose law and tax it follows. */
  scope?: AgentScope | string;
  jurisdiction?: string;
}

/** AM10: how an agent looks — its colour per theme and its icon's shape. */
export interface Accent {
  light: string;
  dark: string;
}

export interface Look {
  icon_svg?: string;
  accent?: Accent | null;
}

/** AM6: generic = any company; org-specific = written for this organisation. */
export type AgentScope = "generic" | "org-specific";

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
  /** AM12: what else the agent reads in a form with no marker ("rows", "lines") and whether it takes a
   *  PDF with fields as a form ("pdf") */
  reads?: string[];
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
  /** AM8: the text around the slot (what the model read to propose a field for it) */
  context?: string;
  /** AM8: this slot stops the template being used at all (a chart left with old numbers, a marker the
   *  filler cannot reach) — refused before any work */
  blocking?: boolean;
  /** AM12: the rows of a table of items in a form with no marker — the heading over each column, and how
   *  many rows the form has for lines */
  column_labels?: Record<string, string>;
  rows?: number;
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
  /** AM8: the model that proposed the mapping (the person confirms it) */
  proposed_by?: string | null;
  /** AM8: the slots that stop the template being used */
  blocking?: TemplateSlot[] | null;
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
  /** AM6 */
  scope?: AgentScope | string;
  jurisdiction?: string;
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
  /** AM6 */
  scope?: AgentScope | string;
  jurisdiction?: string;
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
  /** AM4: the agent's model ("auto" or a model id). AM6: set from the model menu, the way a chat's is. */
  model_choice?: string;
}

export interface InstallRow extends Install, Look {
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
/** AM6: what a run came to — or, while it has not ended, its status. A round
 *  that delivered its draft while the agent's gate did not pass is
 *  completed_open: finished, charged, and not a failure. */
export type RunOutcome = "completed" | "completed_open" | "failed" | "cancelled" | RunStatus;
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
  /** AM6: absent from a server older than AM6 (the status then says it). */
  outcome?: RunOutcome | string;
  status_reason?: string;
  period?: string | null;
  message?: string;
  definition_version?: string;
  formats?: unknown;
  answers?: unknown;
  assumptions?: Assumption[] | null;
  credits_spent?: number;
  /** AM6: what the platform paid; the round ended on its gate with its draft delivered; when it delivered. */
  credits_cost?: number;
  gate_open?: boolean;
  delivered_at?: string | null;
  model_used?: string | null;
  model_self_hosted?: boolean | null;
  awaiting_ms?: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  /** AM4: the thread (absent = the first), an admin's removal of its files. */
  thread_id?: string | null;
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
  /** AM8: how many values this check read back out of the file and compared */
  values?: number;
}

/** AM8: a slot of the customer's form that does not hold its value, by the form's own name, and why. */
export interface UnfilledSlot {
  label: string;
  why?: string;
}

export interface Quality {
  passed?: boolean;
  checks?: QualityCheck[];
  /** AM8: a filled template's slots without their value */
  unfilled?: UnfilledSlot[] | null;
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
  /** AM5: the answer the card recommends (absent: it recommends nothing) —
   *  not what skipping it records, which is `default`. */
  recommended_label?: string;
  /** AM5: skipping the question leaves its subject out of the result. */
  leaves_out?: boolean;
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

export type ThreadKind = "greeting" | "user_text" | "user_files" | "agent_text" | "notice" | "card" | "progress" | "outputs" | "intent" | "email_draft" | "form";

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

/** AM10: the round's own form, as its thread item follows it (roundform.go FormPayload). */
export interface FormPayload {
  run_id: string;
  file_id: string;
  name: string;
  status: "inspecting" | "needs_mapping" | "ready" | "filled" | "unfilled" | "failed" | "refused" | string;
  /** slots a person still has to map */
  open?: number;
  /** labels of the fields that could not be filled — of a delivered draft, or of a refused form (the customer's own words) */
  unfilled?: string[] | null;
  /** AM11: why each of them has no value, in the same order (the server's sentence) */
  unfilled_why?: string[] | null;
  /** AM11: why a form was refused ("too_few_fields", "no_fields") or cannot be used (the filler's code) */
  reason?: string;
  /** AM11: how many fields the form expects filled, and how many the agent has a value for */
  fields?: number;
  mapped?: number;
  artifact_id?: string;
  why?: string;
  text?: string;
}

/** AM10: GET /runs/{id}/template — the round's form for the mapping screen. */
export interface RunFormDetail {
  form: { file_id: string; name: string; kind: string; status: string; slots?: TemplateSlot[]; mapping?: Record<string, string> | null };
  spec: TemplateSpec;
  inspect?: { slots?: TemplateSlot[]; proposal?: Record<string, string> | null; proposed_by?: string | null };
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

/** AM5: a failed round as a person reads it (the server builds it from the
 *  reason's code, never from an error's text; `reason` is never shown). */
export interface Failure {
  reason: string;
  what: string;
  next: string;
  files: string;
  charged: string;
  credits: number;
  can_retry: boolean;
  failed_checks?: string[] | null;
}

/** AM6: what an answer drew from a knowledge base (asked for in ask mode only). */
export interface AgentKnowledge {
  base: string;
  sources: { title: string; locator?: string }[] | null;
}

/** AM6: a round that delivered its work while the agent's gate did not pass
 *  (a reconciliation that does not tie). A result, not a failure; `code` is
 *  never shown. */
export interface Completion {
  code: string;
  title: string;
  what: string;
  next: string;
  files: string;
  charged: string;
  credits: number;
}

export interface AgentTextPayload {
  text?: string;
  run_id?: string;
  /** error: the round failed; open (AM6): it finished with its gate open */
  tone?: "error" | "open" | string;
  failure?: Failure | null;
  completion?: Completion | null;
  /** AM6: the answer used a knowledge base — never part of a deliverable. */
  knowledge?: AgentKnowledge | null;
}

/** AM5: a document the round left out ("เอกสารที่ยังไม่ได้รวม"). */
export interface NotIncluded {
  doc_key: string;
  name: string;
  reason: string;
  detail?: string;
  how: string;
  can_restore: boolean;
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

/** AM6: open = the step finished and left a note (a gate that was checked and did not pass). */
export type ProgressStepStatus = "pending" | "running" | "done" | "waiting" | "failed" | "skipped" | "open";

export interface ProgressPayload {
  run_id: string;
  kind?: string;
  status: RunStatus | string;
  outcome?: RunOutcome | string;
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
    /** AM5: the documents this round left out. */
    not_included?: NotIncluded[] | null;
    [k: string]: unknown;
  } | null;
  model?: OutputsModel | null;
  credits?: number;
  rerender?: boolean;
  /** AM3: these files carry a reviewer's sign-off on their face. */
  review?: { id: string; reviewer: string; at_label: string; note?: string; skipped?: string[] | null } | null;
  /** AM5: the agent's own gate did not pass (a reconciliation that does not tie) — the files are a draft. */
  draft?: boolean;
  /** AM5: what the agent's gate said, in its words. */
  gate?: { passed: boolean; what: string; next?: string } | null;
  /** AM8: the formats this agent can still make from the round's numbers (absent on older cards) */
  more_formats?: string[] | null;
}

/** AM4: what the files card always says about models. */
export interface OutputsModel {
  model?: string;
  self_hosted?: boolean;
  /** what a model does for this agent: vision (reads scans) | text (writes the words) | none */
  uses?: "vision" | "text" | "none" | "web" | string;
  choice?: string;
  chosen_model?: string;
  not_chosen?: boolean;
  vision_model?: string;
  vision_self_hosted?: boolean;
  written_by_rules?: boolean;
  /** AM12 (uses "web"): the models that read the request and the saved pages, in the order they answered */
  web?: { model: string; self_hosted?: boolean }[] | null;
}

/** AM4: one conversation with an installed agent (the first has no id). */
export interface AgentThread {
  id: string | null;
  title: string;
  created_at: string;
  last_at: string;
  rounds: number;
  waiting: boolean;
  /** AM10: pinned chats come first; `named` = the title is the person's own; `period` = the latest round's. */
  pinned_at?: string | null;
  named?: boolean;
  period?: string;
}

/** AM6: the agent's model, and what the chat's model menu needs to know to
 *  serve as the agent's (GET /installs/{id}/models, PUT /installs/{id}/model). */
export interface AgentModels {
  /** what a model does for this agent; none: it sends nothing to a model, and has no model control */
  uses: "vision" | "text" | "none" | string;
  /** the install's model: "auto" or a model id */
  choice: string;
  /** what "อัตโนมัติ" resolves to now ("" when unknown) */
  auto_model: string;
  /** every model that can do this agent's job; allowed false: the organisation's policy does not allow it */
  models: { id: string; allowed: boolean }[] | null;
  /** why a model is not allowed, said once ("" when nothing is blocked) */
  blocked_reason: string;
  /** why a model cannot do this agent's job */
  hidden_reason: string;
  /** what the model does for this agent (the settings page's sentence) */
  reads: string;
  note: string;
  /** AM9: may this agent's documents go to a model outside the organisation —
   *  the organisation's switch (the ceiling) and the agent's own under it */
  egress?: AgentEgress;
}

/** AM9: document egress for one agent, both levels. */
export interface AgentEgress {
  org: boolean;
  agent: boolean;
  /** no admin has set the agent's own value: it is its default */
  agent_default: boolean;
  /** the agent touches accounting data (its default is off) */
  accounting: boolean;
  effective: boolean;
  /** the server's sentence for both levels */
  words: string;
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

/** AM12: whether an agent that works on the web may search and open pages now, and why not. */
export interface WebState {
  uses: boolean;
  open: boolean;
  /** the runner has a search provider; without one a run opens only the links the person sends */
  search: boolean;
  why?: string;
  /** the sentence for the person, in the screen's language */
  words?: string;
}

/** AM12: a value a page was read for — the words as printed and the block they are printed in. */
export interface PageQuote {
  quote: string;
  block?: number;
}

export interface ComparisonCandidate {
  no: number;
  key: string;
  host: string;
  url: string;
  kind: string;
  observed_at?: string;
  name: string;
  model?: PageQuote | null;
  variant?: PageQuote | null;
  seller?: PageQuote | null;
  price?: { display?: string; amount?: string; currency?: string | null; block?: number; beside?: string[] } | null;
  price_basis?: PageQuote | null;
  subtotal?: string | null;
  net?: string | null;
  vat_part?: string | null;
  total?: string | null;
  vat?: { says: string; quote: string } | null;
  shipping?: PageQuote | null;
  lead_time?: PageQuote | null;
  minimum_order?: PageQuote | null;
  stock?: PageQuote | null;
  warranty?: PageQuote | null;
  tax_id?: PageQuote | null;
  legal_name?: PageQuote | null;
  on_list?: { name: string; by: string } | null;
  requirements?: { need: string; verdict: string; quote?: string | null }[];
  open: string[];
  not_found?: { field: string; why: string }[];
  picture?: { name: string; width: number; height: number; box?: number[] } | null;
  source: string;
  /** the package's own words for this row, in the language the run was made in */
  words?: {
    kind?: string;
    vat?: string;
    on_list?: string;
    requirements?: string;
    open?: { code: string; what: string; why: string }[];
    not_found?: { field: string; why: string }[];
  };
}

/** AM12: what a choice card asks about (a question's evidence.comparison). */
export interface Comparison {
  item: { key: string; name: string; meant?: string; quantity?: string | null; quantity_assumed?: boolean; unit?: string };
  candidates: ComparisonCandidate[];
  dropped: { host: string; url: string; why: string; words?: string }[];
  sorted_by?: string;
  unknown?: string;
  round?: { cap_hit?: string[]; search?: string; used?: Record<string, number>; caps?: Record<string, number>; lines?: string[] };
}

/** AM12: one search or one address a run asked for, and what came of it. */
export interface WebCall {
  run_id: string;
  kind: "search" | "fetch" | string;
  source: string;
  host: string;
  url: string;
  outcome: string;
  reason: string;
  /** why it was not opened or not kept, in words */
  words?: string;
  bytes: number;
  results: number;
  provider: string;
  started_at: string;
  duration_ms: number;
  used?: boolean;
  purge_after?: string;
  purged_at?: string;
}

export interface InstallDetail {
  install: Install;
  manifest: Manifest;
  /** AM12: for an agent that works on the web */
  web?: WebState;
  /** AM10: the agent's look (the newest version's, so an older install shows it too). */
  look?: Look;
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
  /** AM6: the agent's own verdict on the round, and the receipts a person may confirm here. */
  gate?: { passed: boolean; code?: string; what?: string; next?: string } | null;
  receipt_offers?: ReceiptOffer[] | null;
  citations?: Citation[] | null;
  source?: { files?: { file: string; sha256?: string; role?: string; rows?: number }[] } | null;
  balance?: { debit?: string; credit?: string; diff?: string } | null;
}

/** AM6: a purchase-order line billed without a receipt on file — a person who
 *  knows the goods came may say so here (offered, never required). */
export interface ReceiptOffer {
  akey?: string;
  key: string;
  po: string;
  line_no?: number | string | null;
  item_code?: string | null;
  description?: string | null;
  ordered?: string | number | null;
  unit?: string | null;
  billed?: string | number | null;
  file?: string;
  sheet?: string;
  row?: number | string | null;
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
  /** AM11: what the row counts, in words (a question's heading, a failure as a person is told it). */
  label?: string;
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
    /** AM6: rounds that delivered their draft with the gate open; failed then counts what delivered nothing. */
    completed_open?: number;
    failed?: number;
    by_outcome?: Record<string, number>;
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
  /** AM6: the rounds that finished with their gate open, by the gate's code. */
  open?: DashCount[] | null;
  confidence: Record<string, number> | null;
  tiers: Record<string, number> | null;
  documents: number;
  artifacts: DashCount[] | null;
  versions: { version: string; runs: number; last_run_at: string }[] | null;
  /** cost_not_charged (AM6): what the platform paid for rounds that delivered nothing. */
  spend: { credits: number; cost_not_charged?: number; per_succeeded_run: number | null; per_document: number | null; budget_credits: number };
  models: { model: string; self_hosted: boolean; runs: number }[] | null;
  last_self_test: { status: string; outcome?: string; reason?: string; why?: string; version?: string; finished_at?: string | null } | null;
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
  outcome?: RunOutcome | string;
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
  /** AM6 */
  scope?: AgentScope | string;
  jurisdiction?: string;
}

export interface RailInstall extends Look {
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
    outcome?: RunOutcome | string;
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

// ── AM6: the cost before a round, declared settings, reminders, notifications, e-mail ──

/** GET /installs/{id}/estimate — `line` is the one sentence shown before a round starts. */
export interface Estimate {
  basis: "own_runs" | "sample" | "none" | string;
  basis_runs: number;
  credits: number | null;
  thb: number | null;
  seconds: number | null;
  line: string;
}

export type SettingKind = "money" | "percent" | "int" | "ratio" | "choice" | "text";

/** A setting the agent's manifest declares, with its current value. */
export interface DeclaredSetting {
  key: string;
  label: string;
  kind: SettingKind | string;
  min?: number | null;
  max?: number | null;
  default?: string | null;
  help?: string;
  choices?: { value: string; label: string }[] | null;
  max_length?: number | null;
  value: unknown;
  set: boolean;
}

/** weekday 1 = Monday; the time is Thai time */
export type ReminderRule = { every: "month"; day: number; time: string } | { every: "week"; weekday: number; time: string };

export interface Reminder {
  id: string;
  install_id: string;
  label: string;
  rule: ReminderRule;
  rule_text: string;
  status: "active" | "stopped" | string;
  next_due_at?: string | null;
  next_due_text: string;
  snoozed_until: string | null;
  skipped_period: string | null;
  waiting_run_id: string | null;
  period_label: string | null;
}

export interface ReminderReaches {
  in_app?: boolean;
  desktop?: boolean;
  browser?: boolean;
  email?: boolean;
}

export interface RemindersResponse {
  reminders: Reminder[] | null;
  reaches: ReminderReaches | null;
  note: string;
}

export type NotificationKind = "reminder_due" | "reminder_followup" | "waiting" | "waiting_followup";

export interface AmNotification {
  id: string;
  kind: NotificationKind | string;
  title: string;
  body: string;
  /** a path of the web app */
  link: string;
  install_id: string;
  run_id: string | null;
  reminder_id: string | null;
  agent_name: string;
  created_at: string;
}

export interface EmailSend {
  id: string;
  draft_id: string;
  to: string[] | null;
  cc: string[] | null;
  status: "sent" | "failed" | string;
  at: string;
  approved_by: { name: string; email: string } | null;
  /** AM9: what carried it */
  transport?: "mailbox" | "organisation" | string;
}

/** A drafted e-mail. It never carries a recipient: those come from the person. */
export interface EmailDraft {
  id: string;
  run_id: string;
  subject: string;
  body: string;
  attachments: { artifact_id: string; name: string; size: number; format: string }[] | null;
  can_send: boolean;
  cannot_send_reason?: string;
  body_sha256: string;
  created_at: string;
  sent: EmailSend[] | null;
  /** AM9: how an approved message goes now — the person's own mailbox, the organisation's relay on
   *  their behalf, or "" (copy it) — and whether their mailbox can be connected / is connected. */
  transport?: "mailbox" | "organisation" | "";
  mailbox?: { available: boolean; connected: boolean };
}

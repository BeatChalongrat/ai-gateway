// AM7 — Agent Market's admin page in admin-ui.
//
// Every call goes through the existing proxy (/api/chat-edge/* → chat-edge
// /api/v1/admin/*, see app/api/chat-edge/[...path]/route.ts): the admin
// bearer never reaches the browser, and the proxy names the signed-in admin
// to chat-edge. chat-edge serves the same handlers the chat app's page used,
// against the same agent_market.org_settings row — one source of truth.
//
// Wire shapes match services/chat-edge-api/internal/agentmarket (admin.go,
// access.go, admindoor.go, mail.go).

import { chatEdge, ChatEdgeApiError } from "./chatEdgeApi";
import type { DictKey } from "./i18n/dict";

const AM = "agent-market";
const seg = (s: string) => encodeURIComponent(s).replace(/%40/g, "@");

// ── shapes ───────────────────────────────────────────────────────────────

export type AccessMode = "off" | "list" | "everyone";
export type OrgRole = "member" | "publisher" | "admin";
export type Section = "access" | "departments" | "spend" | "models" | "web" | "catalog" | "problems" | "runs" | "mail";

/** GET agent-market/me — who is signed in and which sections they may open. */
export interface AmMe {
  email: string;
  name: string;
  admin_role: string;
  role: "admin" | "publisher" | string;
  org_id: string;
  sections: Section[];
  can_disable: boolean;
}

export interface Pricing {
  credits_per_usd: number;
  thb_per_usd: number;
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
  documents_may_leave: boolean;
  retention_years: number;
  storage_warn_mb: number;
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

export type OrgPatch = Partial<{
  name: string;
  access: AccessMode | string;
  domains: string[];
  user_monthly_credits: number | string;
  contact_name: string;
  contact_email: string;
  documents_may_leave: boolean;
  retention_years: number;
  storage_warn_mb: number;
}>;

export interface SpendRow {
  key: string;
  email?: string;
  name?: string;
  department?: string;
  runs: number;
  succeeded: number;
  completed_open?: number;
  failed: number;
  waiting: number;
  users?: number;
  credits: number;
  /** AM12: the part of `credits` that was web searches */
  search_credits?: number;
  cost_not_charged?: number;
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
  total_cost_not_charged?: number;
  catalog_samples_credits: number;
  pricing?: Pricing;
}

export interface SampleSummary {
  run_id: string;
  version: string;
  status: string;
  outcome?: string;
  reason?: string;
  credits: number;
  seconds: number | null;
  finished_at?: string | null;
}

/** AM10: how an agent looks — the package's own icon (checked by the runner, checked again
 *  before it is drawn) and its colour per theme. */
export interface AgentLook {
  icon_svg?: string;
  accent?: { light: string; dark: string } | null;
}

export interface AdminCatalogRow {
  slug: string;
  version: string;
  name: string;
  /** `name` above is the manifest's Thai name; `name_en` is its English one (AM10: shown when the page is in English). */
  manifest?: { icon?: string; short_name?: string; description?: string; name_en?: string } & AgentLook & Record<string, unknown>;
  status?: "published" | "unpublished" | "disabled" | string;
  status_reason?: string;
  sample?: SampleSummary | null;
  changed_by?: string;
  changed_at?: string | null;
  installs: number;
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

/** What an admin sees of any run: never its files, figures or answers. */
export interface RunMeta {
  id: string;
  agent: string;
  slug: string;
  person: string;
  email: string;
  status: string;
  outcome?: string;
  reason: string;
  period_label: string;
  credits: number;
  cost?: number;
  model: string;
  model_self_hosted: boolean | null;
  stopped_at_step?: string;
  created_at: string;
  finished_at?: string | null;
  open_now: boolean;
  times_opened: number;
}

export interface AccessEntry {
  admin_name: string;
  admin_email: string;
  reason: string;
  opened_at: string;
  at_label: string;
}

export interface RunStep {
  id: string;
  seq: number;
  name: string;
  kind: string;
  status: string;
  error?: string;
  model_id?: string;
  credits?: number;
  started_at?: string;
  finished_at?: string | null;
  duration_ms?: number | null;
}

export interface RunArtifact {
  id: string;
  format: string;
  kind?: string;
  name: string;
  version: number;
  size: number;
  created_at: string;
}

export interface RunFile {
  id: string;
  name: string;
  content_type?: string;
  size: number;
  status?: string;
  created_at?: string;
}

/** A run's content, once an admin opened it with a reason (its owner is told). */
export interface RunDetail {
  run: {
    id: string;
    status: string;
    outcome?: string;
    status_reason?: string;
    period?: string | null;
    credits_spent?: number;
    credits_cost?: number;
    model_used?: string | null;
    model_self_hosted?: boolean | null;
    created_at: string;
    finished_at?: string | null;
    evidence_removed_at?: string | null;
  };
  steps: RunStep[] | null;
  artifacts: RunArtifact[] | null;
  files: RunFile[] | null;
  install: { id: string; title: string; slug: string };
  access_log?: AccessEntry[] | null;
}

/** The organisation's mail relay (AM7 phase 5). The password never leaves .env. */
export interface MailSettings {
  host: string;
  port: number;
  tls_mode: "starttls" | "tls" | "none" | string;
  username: string;
  sender: string;
  updated_by?: string;
  updated_at?: string | null;
}

export interface MailState {
  settings: MailSettings;
  /** true when AGENT_MARKET_SMTP_PASSWORD is set (the value is never sent). */
  password_set: boolean;
  /** the name of the .env key that holds the password */
  password_env: string;
  ready: boolean;
  /** why a message cannot be sent yet (a Thai sentence), when not ready */
  not_ready?: string;
}

/** AM9: one agent's document egress — the organisation's switch is the ceiling, the agent's own
 *  setting can only narrow it (off by default for an agent that touches accounting). */
export interface AgentEgressRow {
  slug: string;
  name: string;
  org: boolean;
  agent: boolean;
  agent_default: boolean;
  accounting: boolean;
  /** AM12: the agent works on the web (it searches and opens pages outside the organisation): off by default too */
  web?: boolean;
  effective: boolean;
  words: string;
  /** AM16: the admin's own masking switch for the agent — off unless turned on; it does not follow `agent` */
  masked?: boolean;
  /** AM16: who outside the organisation this agent's documents reach — its own ticks that the organisation's list also holds, while `effective`. Empty is nobody. */
  recipients?: string[];
  /** AM16: the agent's own ticks, whether or not the organisation's list still holds them */
  agent_recipients?: string[];
  org_recipients?: string[];
  /** AM16: a page image may be read by an outside model — needs `effective` and masking off (an image cannot be masked) */
  pages_may_leave?: boolean;
}

// ── AM12: agents that work on the web ────────────────────────────────────

export interface WebCaps {
  searches: number;
  pages: number;
  pages_per_host: number;
  seconds: number;
  source_mb: number;
  results: number;
}

/** What an admin set for one run of an agent on the web: a blank is the agent's own ceiling. */
export interface WebPolicy {
  max_searches: number | null;
  max_pages: number | null;
  max_seconds: number | null;
  max_source_mb: number | null;
  unused_keep_days: number;
  updated_by?: string;
  updated_at?: string;
}

export interface WebDenyHost {
  host: string;
  note: string;
  added_by: string;
  added_at: string;
}

export interface WebSupplier {
  id: string;
  name: string;
  tax_id: string;
  website: string;
  note: string;
  added_by?: string;
  updated_at?: string;
}

export interface WebAgentRow extends AgentEgressRow {
  caps: WebCaps;
}

export interface AdminWeb {
  policy: WebPolicy;
  deny: WebDenyHost[];
  suppliers: WebSupplier[];
  agents: WebAgentRow[];
  /** the most any run may use, whatever an agent or an admin asks */
  ceiling: WebCaps;
  /** the runner has a search provider / a page fetcher */
  search: boolean;
  fetcher: boolean;
}

/** Where one run went: the sites it reached, and each address that was not opened with why (both languages). */
export interface WebRunRow {
  run_id: string;
  slug: string;
  agent: string;
  user: string;
  started_at: string;
  searches: number;
  fetches: number;
  reached: string[];
  refused: { host: string; reason: string; words?: Record<string, string> }[];
  bytes: number;
  search_credits: number;
}

/** AM16: one recipient an admin can tick — the route, and the company that runs the model behind it. */
export interface RecipientOption {
  key: string;
  route: string;
  company: string;
  models: string[];
}

// ── calls ────────────────────────────────────────────────────────────────

export const amAdmin = {
  me: (): Promise<AmMe> => chatEdge.get(`${AM}/me`),
  org: (): Promise<AdminOrg> => chatEdge.get(`${AM}/org`),
  putOrg: (body: OrgPatch): Promise<{ org: OrgSettings; resumed: number }> => chatEdge.put(`${AM}/org`, body),
  // AM9 — per-agent egress under the organisation's switch
  egress: (): Promise<{ org: boolean; agents: AgentEgressRow[]; org_recipients?: string[]; available?: RecipientOption[] }> =>
    chatEdge.get(`${AM}/egress`),
  // AM16 — to whom: the organisation's own list (the ceiling), and one agent's under it. The whole list
  // is sent each time; each recipient added or removed is its own line in the audit log.
  putOrgRecipients: (recipients: string[]): Promise<{ org_recipients: string[] }> => chatEdge.put(`${AM}/egress/recipients`, { recipients }),
  putRecipients: (slug: string, recipients: string[]): Promise<{ slug: string; egress: AgentEgressRow }> =>
    chatEdge.put(`${AM}/egress/${seg(slug)}`, { recipients }),
  putEgress: (slug: string, documentsMayLeave: boolean): Promise<{ slug: string; egress: AgentEgressRow }> =>
    chatEdge.put(`${AM}/egress/${seg(slug)}`, { documents_may_leave: documentsMayLeave }),
  // AM16 — the agent's masking switch: its own request, and its own line in the audit log
  putMasked: (slug: string, masked: boolean): Promise<{ slug: string; egress: AgentEgressRow }> =>
    chatEdge.put(`${AM}/egress/${seg(slug)}`, { masked }),
  // AM12 — agents that work on the web: one run's ceilings, the sites never opened, the organisation's
  // suppliers, and where each run went
  web: (): Promise<AdminWeb> => chatEdge.get(`${AM}/web`),
  putWebPolicy: (body: {
    max_searches: number | null;
    max_pages: number | null;
    max_seconds: number | null;
    max_source_mb: number | null;
    unused_keep_days: number | null;
  }): Promise<{ policy: WebPolicy }> => chatEdge.put(`${AM}/web/policy`, body),
  putWebDeny: (host: string, note: string): Promise<{ deny: WebDenyHost[] }> => chatEdge.put(`${AM}/web/deny`, { host, note }),
  deleteWebDeny: (host: string): Promise<{ deny: WebDenyHost[] }> => chatEdge.delete(`${AM}/web/deny/${seg(host)}`),
  putWebSupplier: (body: { id?: string; name: string; tax_id?: string; website?: string; note?: string }): Promise<{ suppliers: WebSupplier[] }> =>
    chatEdge.put(`${AM}/web/suppliers`, body),
  deleteWebSupplier: (id: string): Promise<{ suppliers: WebSupplier[] }> => chatEdge.delete(`${AM}/web/suppliers/${seg(id)}`),
  webCalls: (days: number): Promise<{ days: number; runs: WebRunRow[] | null }> => chatEdge.get(`${AM}/web/calls?days=${days}`),
  putMember: (
    email: string,
    body: { display_name?: string; department?: string; role?: string; monthly_credits?: number | string | null },
  ): Promise<{ member: OrgMember; created: boolean; resumed: number }> => chatEdge.put(`${AM}/members/${seg(email)}`, body),
  deleteMember: (email: string): Promise<{ removed: boolean }> => chatEdge.delete(`${AM}/members/${seg(email)}`),
  putDepartment: (
    name: string,
    body: { monthly_credits?: number | string | null; contact_name?: string; contact_email?: string },
  ): Promise<{ department: OrgDepartment; resumed: number }> => chatEdge.put(`${AM}/departments/${seg(name)}`, body),
  deleteDepartment: (name: string): Promise<{ removed: boolean; resumed: number }> =>
    chatEdge.delete(`${AM}/departments/${seg(name)}`),
  spend: (days: number): Promise<AdminSpend> => chatEdge.get(`${AM}/spend?days=${days}`),
  problems: (status: "open" | "closed" | "all" = "all"): Promise<{ problems: ProblemReport[] | null }> =>
    chatEdge.get(`${AM}/problems?status=${status}`),
  closeProblem: (id: string, note: string): Promise<{ problem: ProblemReport }> =>
    chatEdge.post(`${AM}/problems/${seg(id)}/close`, { note }),
  catalog: (): Promise<{ agents: AdminCatalogRow[] | null; admin: boolean }> => chatEdge.get(`${AM}/catalog`),
  putCatalog: (
    slug: string,
    body: { status: string; reason?: string },
  ): Promise<{ state: { slug: string; status: string; reason: string }; stopped_runs: number }> =>
    chatEdge.put(`${AM}/catalog/${seg(slug)}`, body),
  remakeSample: (slug: string): Promise<{ run: { id: string } }> => chatEdge.post(`${AM}/catalog/${seg(slug)}/sample`, {}),
  runs: (status = "all"): Promise<{ runs: RunMeta[] | null; grant_minutes: number }> =>
    chatEdge.get(`${AM}/runs?status=${encodeURIComponent(status)}`),
  openRun: (id: string, reason: string): Promise<{ opened: boolean; expires_at: string }> =>
    chatEdge.post(`${AM}/runs/${seg(id)}/open`, { reason }),
  run: (id: string): Promise<RunDetail> => chatEdge.get(`${AM}/runs/${seg(id)}`),
  removeEvidence: (id: string, reason: string): Promise<{ removed_objects: number }> =>
    chatEdge.post(`${AM}/runs/${seg(id)}/remove-evidence`, { reason }),
  /** A plain link the browser downloads through the proxy (same origin, the session cookie rides along). */
  artifactHref: (runId: string, artifactId: string) =>
    `/api/chat-edge/${AM}/runs/${seg(runId)}/artifacts/${seg(artifactId)}/download`,
  fileHref: (runId: string, fileId: string) => `/api/chat-edge/${AM}/runs/${seg(runId)}/files/${seg(fileId)}/download`,
  // phase 5 — the mail relay
  mail: (): Promise<MailState> => chatEdge.get(`${AM}/mail`),
  putMail: (body: Omit<MailSettings, "updated_by" | "updated_at">): Promise<MailState> => chatEdge.put(`${AM}/mail`, body),
  testMail: (to: string): Promise<{ sent: boolean; to: string }> => chatEdge.post(`${AM}/mail/test`, { to }),
};

type Translate = (key: DictKey, vars?: Record<string, string | number>) => string;

/** chat-edge's refusal, from a ChatEdgeApiError body `{error:{code,message}}`. */
function refusalOf(err: unknown): { code?: string; message?: string } | null {
  if (!(err instanceof ChatEdgeApiError)) return null;
  const b = err.body as { error?: { code?: unknown; message?: unknown } } | string | null;
  if (!b || typeof b !== "object" || !b.error || typeof b.error !== "object") return null;
  return {
    code: typeof b.error.code === "string" ? b.error.code : undefined,
    message: typeof b.error.message === "string" ? b.error.message : undefined,
  };
}

/** chat-edge's refusal code (ACCESS_REASON_NEEDED, AUDIT_UNAVAILABLE …), when the error carries one. */
export function amErrorCode(err: unknown): string | null {
  return refusalOf(err)?.code ?? null;
}

// AM10 F — chat-edge writes every refusal as a Thai sentence for a person
// (internal/agentmarket/messages.go, reminders.go) and sends its code beside
// it. The codes below are the ones this page's endpoints answer with a
// sentence that is the same every time, so the page can say it in the
// language it is in. A code is NOT listed when its sentence carries what
// only the server knows — which thing was not found (NOT_FOUND), which
// value was wrong (BAD_VALUE), the organisation's contact person (NOT_ADMIN,
// NOT_PUBLISHER, RUNNER_DOWN, CAPABILITY_MISSING), the address a relay
// refused (MAIL_RECIPIENT) — or was written for the round's owner rather
// than for this page (ROUND_IN_PROGRESS, MAIL_FAILED). Those, and any code
// added later, are shown as received.
const BAD_REQUEST: DictKey = "am.err.BAD_REQUEST";
const AM_ERROR_KEYS: Record<string, DictKey> = {
  AGENT_MARKET_BUSY: "am.err.AGENT_MARKET_BUSY",
  AGENT_MARKET_ERROR: "am.err.AGENT_MARKET_ERROR",
  AUDIT_UNAVAILABLE: "am.err.AUDIT_UNAVAILABLE",
  // a request the page never sends on its own: one sentence, naming the code
  BAD_JSON: BAD_REQUEST,
  BAD_ID: BAD_REQUEST,
  BAD_NAME: BAD_REQUEST,
  // the organisation, its people and its departments
  BAD_ACCESS: "am.err.BAD_ACCESS",
  BAD_ROLE: "am.err.BAD_ROLE",
  BAD_BUDGET: "am.err.BAD_BUDGET",
  BAD_EMAIL: "am.err.BAD_EMAIL",
  LAST_ADMIN: "am.err.LAST_ADMIN",
  // the catalogue
  BAD_STATUS: "am.err.BAD_STATUS",
  REASON_REQUIRED: "am.err.REASON_REQUIRED",
  // someone else's run
  ACCESS_REASON_REQUIRED: "am.err.ACCESS_REASON_REQUIRED",
  ACCESS_REASON_NEEDED: "am.err.ACCESS_REASON_NEEDED",
  REMOVE_REASON_REQUIRED: "am.err.REMOVE_REASON_REQUIRED",
  // the mail relay and its test message
  MAIL_BAD_FIELDS: "am.err.MAIL_BAD_FIELDS",
  MAIL_NOT_CONFIGURED: "am.err.MAIL_NOT_CONFIGURED",
  MAIL_NO_PASSWORD: "am.err.MAIL_NO_PASSWORD",
  MAIL_CONNECT: "am.err.MAIL_CONNECT",
  MAIL_TLS: "am.err.MAIL_TLS",
  MAIL_NO_STARTTLS: "am.err.MAIL_NO_STARTTLS",
  MAIL_CLEAR_AUTH: "am.err.MAIL_CLEAR_AUTH",
  MAIL_AUTH: "am.err.MAIL_AUTH",
  MAIL_SENDER: "am.err.MAIL_SENDER",
  MAIL_DATA: "am.err.MAIL_DATA",
  BAD_RECIPIENT: "am.err.BAD_RECIPIENT",
  // AM12: agents that work on the web
  BAD_HOST: "am.err.BAD_HOST",
  BAD_WEB_POLICY: "am.err.BAD_WEB_POLICY",
  BAD_TAX_ID: "am.err.BAD_TAX_ID",
  SUPPLIER_EXISTS: "am.err.SUPPLIER_EXISTS",
};

/** Every refusal code this page says in its own language (for the tests). */
export function amKnownErrorCodes(): string[] {
  return Object.keys(AM_ERROR_KEYS);
}

/** What a refusal says to the person: the dictionary's sentence for a code
 *  this page knows (in the language of the page), else the sentence
 *  chat-edge wrote (every refusal carries one), else the caller's fallback. */
export function amErrorMessage(err: unknown, fallback: string, t: Translate): string {
  const r = refusalOf(err);
  if (!r) return fallback;
  const key = r.code && Object.prototype.hasOwnProperty.call(AM_ERROR_KEYS, r.code) ? AM_ERROR_KEYS[r.code] : undefined;
  if (key) return t(key, { code: r.code ?? "" });
  return r.message || fallback;
}

/** chat-edge answers 404 on the whole door while Agent Market is switched off. */
export function amIsOff(err: unknown): boolean {
  return err instanceof ChatEdgeApiError && err.status === 404;
}

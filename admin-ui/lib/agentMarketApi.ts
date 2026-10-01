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

const AM = "agent-market";
const seg = (s: string) => encodeURIComponent(s).replace(/%40/g, "@");

// ── shapes ───────────────────────────────────────────────────────────────

export type AccessMode = "off" | "list" | "everyone";
export type OrgRole = "member" | "publisher" | "admin";
export type Section = "access" | "departments" | "spend" | "models" | "catalog" | "problems" | "runs" | "mail";

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

export interface AdminCatalogRow {
  slug: string;
  version: string;
  name: string;
  manifest?: { icon?: string; short_name?: string; description?: string } & Record<string, unknown>;
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
  effective: boolean;
  words: string;
}

// ── calls ────────────────────────────────────────────────────────────────

export const amAdmin = {
  me: (): Promise<AmMe> => chatEdge.get(`${AM}/me`),
  org: (): Promise<AdminOrg> => chatEdge.get(`${AM}/org`),
  putOrg: (body: OrgPatch): Promise<{ org: OrgSettings; resumed: number }> => chatEdge.put(`${AM}/org`, body),
  // AM9 — per-agent egress under the organisation's switch
  egress: (): Promise<{ org: boolean; agents: AgentEgressRow[] }> => chatEdge.get(`${AM}/egress`),
  putEgress: (slug: string, documentsMayLeave: boolean): Promise<{ slug: string; egress: AgentEgressRow }> =>
    chatEdge.put(`${AM}/egress/${seg(slug)}`, { documents_may_leave: documentsMayLeave }),
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

/** The sentence chat-edge wrote for a person (every refusal carries one), else a fallback. */
export function amErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ChatEdgeApiError) {
    const b = err.body as { error?: { message?: string } } | string | null;
    if (b && typeof b === "object" && b.error?.message) return b.error.message;
  }
  return fallback;
}

/** chat-edge answers 404 on the whole door while Agent Market is switched off. */
export function amIsOff(err: unknown): boolean {
  return err instanceof ChatEdgeApiError && err.status === 404;
}

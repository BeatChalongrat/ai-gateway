// Thin client for the chat-edge admin endpoints. Goes through the
// Next.js /api/chat-edge proxy so the chat-edge admin bearer never
// touches the browser. Same contract as lib/api.ts: GET / POST /
// PATCH helpers, ApiError on non-2xx.

export class ChatEdgeApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(`ChatEdge API error ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request(path: string, options: RequestInit = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json; charset=utf-8",
    ...(options.headers as Record<string, string>),
  };
  const res = await fetch(`/api/chat-edge/${path.replace(/^\//, "")}`, {
    ...options,
    method,
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    // Read the body exactly once. A Response stream can only be
    // consumed a single time, so we take the text and *then* try to
    // parse it as JSON — calling res.json() and res.text() on the
    // same Response throws "body stream already read" (e.g. when an
    // upstream 404 returns a plain-text body, not JSON).
    const raw = await res.text();
    let body: unknown = raw;
    try {
      body = JSON.parse(raw);
    } catch {
      // Non-JSON error body (plain text / HTML) — keep the raw string.
    }
    throw new ChatEdgeApiError(res.status, body);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const chatEdge = {
  get: (path: string) => request(path),
  post: (path: string, data: unknown) =>
    request(path, { method: "POST", body: JSON.stringify(data) }),
  patch: (path: string, data: unknown) =>
    request(path, { method: "PATCH", body: JSON.stringify(data) }),
  put: (path: string, data: unknown) =>
    request(path, { method: "PUT", body: JSON.stringify(data) }),
  delete: (path: string) => request(path, { method: "DELETE" }),
};

// ── Typed wire shapes (matches chat-edge handler/admin.go) ──

export type AnalyticsPeriod = "today" | "7d" | "30d" | "90d";

export interface AnalyticsTotals {
  conversations: number;
  messages: number;
  tokens_used: number;
  estimated_cost_usd: number;
  active_users: number;
  files_uploaded: number;
}

export interface AnalyticsDailyRow {
  date: string;
  conversations: number;
  messages: number;
  tokens_used: number;
  active_users: number;
}

export interface AnalyticsByModelRow {
  model_id: string;
  messages: number;
  tokens_used: number;
  estimated_cost_usd: number;
}

export interface AnalyticsResponse {
  period: AnalyticsPeriod;
  totals: AnalyticsTotals;
  daily: AnalyticsDailyRow[];
  by_model: AnalyticsByModelRow[];
}

export interface AdminConversationRow {
  id: string;
  title: string;
  user_id: string;
  user_email: string;
  model_id: string;
  message_count: number;
  token_count: number;
  has_files: boolean;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminConversationListResponse {
  conversations: AdminConversationRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminMessageRow {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model_id?: string | null;
  created_at: string;
  token_input?: number | null;
  token_output?: number | null;
  has_files: boolean;
}

export interface AdminFileRow {
  id: string;
  message_id?: string;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  created_at: string;
}

export interface AdminConversationDetail {
  conversation: {
    id: string;
    title: string;
    user_id: string;
    user_email: string;
    user_name: string;
    model_id: string;
    message_count: number;
    created_at: string;
    updated_at: string;
  };
  messages: AdminMessageRow[];
  files: AdminFileRow[];
}

export interface AdminUserRow {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  conversation_count: number;
  message_count: number;
  token_count: number;
  last_active_at?: string;
  created_at: string;
}

export interface AdminUserListResponse {
  users: AdminUserRow[];
  total: number;
  limit: number;
  offset: number;
}

// ── High-level fetchers ──

export function fetchAnalytics(period: AnalyticsPeriod) {
  return chatEdge.get(
    `analytics/usage?period=${encodeURIComponent(period)}`,
  ) as Promise<AnalyticsResponse>;
}

export interface AdminConvQuery {
  limit?: number;
  offset?: number;
  q?: string;
  user_id?: string;
  model_id?: string;
  date_from?: string;
  date_to?: string;
}

export function fetchAdminConversations(q: AdminConvQuery = {}) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v == null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return chatEdge.get(
    `conversations${qs ? `?${qs}` : ""}`,
  ) as Promise<AdminConversationListResponse>;
}

export function fetchAdminConversation(id: string) {
  return chatEdge.get(
    `conversations/${encodeURIComponent(id)}`,
  ) as Promise<AdminConversationDetail>;
}

export function fetchAdminUsers(opts: { limit?: number; offset?: number; q?: string } = {}) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) {
    if (v == null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return chatEdge.get(
    `users${qs ? `?${qs}` : ""}`,
  ) as Promise<AdminUserListResponse>;
}

export function patchAdminUser(
  id: string,
  body: { is_active?: boolean; allowed_models?: string[] },
) {
  return chatEdge.patch(`users/${encodeURIComponent(id)}`, body);
}

// ── Knowledge bases (admin) ──────────────────────────────────
//
// Cross-tenant visibility. The admin proxy at /api/chat-edge/* injects
// the bearer secret; these helpers just speak the wire contract.

export interface AdminKBRow {
  id: string;
  tenant_id: string;
  tenant_name?: string;
  name: string;
  description: string;
  embedding_model: string;
  chunk_size: number;
  chunk_overlap: number;
  is_active: boolean;
  document_count: number;
  total_chunks: number;
  created_at: string;
  updated_at: string;
}

export interface AdminKBListResponse {
  knowledge_bases: AdminKBRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminKBDocRow {
  id: string;
  knowledge_base_id: string;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  status: "pending" | "processing" | "indexed" | "failed";
  chunk_count: number;
  char_count: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminKBSampleChunk {
  id: string;
  document_id: string;
  filename: string;
  chunk_index: number;
  content: string;
}

export interface AdminKBDetailResponse {
  knowledge_base: AdminKBRow;
  documents: AdminKBDocRow[];
  sample_chunks: AdminKBSampleChunk[];
}

export interface AdminKBQuery {
  q?: string;
  tenant_id?: string;
  limit?: number;
  offset?: number;
}

export function fetchAdminKnowledgeBases(q: AdminKBQuery = {}) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v == null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  // The /api/chat-edge proxy already prepends `/admin/`, so paths here
  // are relative to it — `knowledge-bases`, not `admin/knowledge-bases`
  // (the latter produced `/admin/admin/knowledge-bases` → 404).
  return chatEdge.get(
    `knowledge-bases${qs ? `?${qs}` : ""}`,
  ) as Promise<AdminKBListResponse>;
}

export function fetchAdminKnowledgeBase(id: string) {
  return chatEdge.get(
    `knowledge-bases/${encodeURIComponent(id)}`,
  ) as Promise<AdminKBDetailResponse>;
}

export function deleteAdminKnowledgeBase(id: string) {
  return chatEdge.delete(`knowledge-bases/${encodeURIComponent(id)}`);
}

export function reindexAdminKnowledgeBase(id: string) {
  return chatEdge.post(
    `knowledge-bases/${encodeURIComponent(id)}/reindex`,
    {},
  ) as Promise<{ reindexed: number; failed: number; total: number }>;
}

// ── Retention & deletion program ─────────────────────────────────────

export interface RetentionSettings {
  tenant_id: string;
  transcript_retention_enabled: boolean;
  transcript_retention_days?: number | null;
  deleted_conversation_purge_days: number;
  dry_run: boolean;
}

export interface RetentionSettingsResponse {
  settings: RetentionSettings;
  min_days: number;
  effective_at: string;
}

export interface DeletionAuditEntry {
  id: string;
  tenant_id: string;
  user_id?: string | null;
  actor: string;
  action: string;
  scope: string;
  resource_id?: string | null;
  rule: string;
  counts: Record<string, number>;
  details?: Record<string, unknown>;
  created_at: string;
}

export function fetchRetentionSettings(tenantId: string) {
  return chatEdge.get(
    `retention-settings?tenant_id=${encodeURIComponent(tenantId)}`,
  ) as Promise<RetentionSettingsResponse>;
}

export function putRetentionSettings(body: RetentionSettings) {
  return chatEdge.put(`retention-settings`, body) as Promise<{
    settings: RetentionSettings;
    effective_at: string;
    audit_ok: boolean;
  }>;
}

export function fetchDeletionAudit(tenantId: string, action = "") {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (action) qs.set("action", action);
  return chatEdge.get(`deletion-audit?${qs.toString()}`) as Promise<{
    entries: DeletionAuditEntry[];
  }>;
}

export function fetchRetentionPreview(tenantId: string) {
  return chatEdge.get(
    `retention-preview?tenant_id=${encodeURIComponent(tenantId)}`,
  ) as Promise<{ tenant_id: string; counts: Record<string, number>; next_run: string }>;
}

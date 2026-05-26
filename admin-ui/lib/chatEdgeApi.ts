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
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
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

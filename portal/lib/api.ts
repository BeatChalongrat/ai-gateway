// Client-side API wrapper for the user portal.
//
// All calls go through ``/api/...`` so Next.js' rewrite rule
// (see ``next.config.js``) proxies them to the backend at
// ``http://localhost:8080``. This avoids CORS entirely in dev and
// makes the client agnostic to where the backend runs.
//
// 401 handling:
//   - Once per request we try a refresh using the stored refresh
//     token. If refresh succeeds, we retry the original call.
//   - If refresh fails, we clear tokens and punt to /login.

import { clearTokens, getRefreshToken, getToken, setAccessToken } from "./auth";
import type { ApiError } from "./types";

const PREFIX = "/api";

export class PortalApiError extends Error {
  status: number;
  code: string;
  details?: Record<string, unknown>;

  constructor(status: number, body: unknown) {
    const err = (body && typeof body === "object" && "error" in body
      ? (body as { error: ApiError }).error
      : null);
    const code = err?.code ?? `http_${status}`;
    const message = err?.message ?? `Request failed (${status})`;
    super(message);
    this.status = status;
    this.code = code;
    this.details = err?.details;
  }
}

let refreshInFlight: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  try {
    const r = await fetch(`${PREFIX}/portal/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!r.ok) return false;
    const body = await r.json();
    if (!body?.access_token) return false;
    setAccessToken(body.access_token);
    return true;
  } catch {
    return false;
  }
}

function sharedRefresh(): Promise<boolean> {
  // If multiple calls 401 at the same time (parallel fetches on a
  // page mount), they all wait on one refresh attempt. Without this
  // they'd each hit /refresh, and some would lose the race and still
  // end up punted to /login with a stale error banner.
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

interface CallOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  json?: unknown;
  auth?: boolean; // default true
  signal?: AbortSignal;
}

async function call<T = unknown>(
  path: string,
  opts: CallOptions = {},
  _retried = false
): Promise<T> {
  const token = opts.auth === false ? null : getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${PREFIX}${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
    signal: opts.signal,
    cache: "no-store",
  });

  if (res.status === 401 && opts.auth !== false && !_retried) {
    const refreshed = await sharedRefresh();
    if (refreshed) {
      return call<T>(path, opts, true);
    }
    clearTokens();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new PortalApiError(401, { error: { code: "session_expired", message: "Session expired." } });
  }

  if (res.status === 204) return undefined as T;

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    throw new PortalApiError(res.status, body);
  }
  return body as T;
}

async function upload<T = unknown>(
  path: string,
  formData: FormData,
  _retried = false
): Promise<T> {
  const token = getToken();
  // Do NOT set Content-Type — the browser fills in the multipart boundary.
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${PREFIX}${path}`, {
    method: "POST",
    headers,
    body: formData,
    cache: "no-store",
  });

  if (res.status === 401 && !_retried) {
    const refreshed = await sharedRefresh();
    if (refreshed) return upload<T>(path, formData, true);
    clearTokens();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new PortalApiError(401, { error: { code: "session_expired", message: "Session expired." } });
  }

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) throw new PortalApiError(res.status, body);
  return body as T;
}

export const api = {
  get: <T = unknown>(path: string, opts?: Omit<CallOptions, "method" | "json">) =>
    call<T>(path, { ...opts, method: "GET" }),
  post: <T = unknown>(path: string, json?: unknown, opts?: Omit<CallOptions, "method" | "json">) =>
    call<T>(path, { ...opts, method: "POST", json }),
  patch: <T = unknown>(path: string, json?: unknown, opts?: Omit<CallOptions, "method" | "json">) =>
    call<T>(path, { ...opts, method: "PATCH", json }),
  delete: <T = unknown>(path: string, opts?: Omit<CallOptions, "method" | "json">) =>
    call<T>(path, { ...opts, method: "DELETE" }),
  upload: <T = unknown>(path: string, formData: FormData) => upload<T>(path, formData),
};

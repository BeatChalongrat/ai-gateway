// Client-side API wrapper for the user portal.
//
// All calls go through ``/api/...`` so Next.js' rewrite rule
// (see ``next.config.js``) proxies them to the backend at
// ``http://localhost:8080``. This avoids CORS entirely in dev and
// makes the client agnostic to where the backend runs.
//
// Auth: HttpOnly cookie migration. There are NO tokens in JS — the
// backend sets ``portal_access_token`` / ``portal_refresh_token`` as
// HttpOnly cookies and the browser sends them automatically once we
// pass ``credentials: "include"``. The proxy is same-origin so cookies
// flow without CORS.
//
// 401 handling:
//   - Once per request we try a refresh (the refresh cookie carries the
//     token; no body needed). If refresh succeeds, we retry the call.
//   - If refresh fails, the backend has already cleared the cookies; we
//     punt to /login.

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
  try {
    // The refresh token rides the HttpOnly cookie; no body required.
    // On success the backend sets rotated access + refresh cookies.
    const r = await fetch(`${PREFIX}/portal/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
    return r.ok;
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
  const headers: Record<string, string> = {
    // Explicit charset matches the backend's UTF8JSONResponse
    // contract — browsers default to UTF-8 anyway, but pinning it
    // here keeps the wire signal unambiguous if a proxy ever
    // synthesises a different default.
    "Content-Type": "application/json; charset=utf-8",
  };

  const res = await fetch(`${PREFIX}${path}`, {
    method: opts.method ?? "GET",
    headers,
    // Send the HttpOnly auth cookies. Same-origin via the Next proxy,
    // but explicit so intent is unambiguous.
    credentials: "include",
    body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
    signal: opts.signal,
    cache: "no-store",
  });

  if (res.status === 401 && opts.auth !== false && !_retried) {
    const refreshed = await sharedRefresh();
    if (refreshed) {
      return call<T>(path, opts, true);
    }
    // Refresh failed → backend already cleared the cookies. Punt to login.
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
  // Do NOT set Content-Type — the browser fills in the multipart boundary.
  const res = await fetch(`${PREFIX}${path}`, {
    method: "POST",
    credentials: "include",
    body: formData,
    cache: "no-store",
  });

  if (res.status === 401 && !_retried) {
    const refreshed = await sharedRefresh();
    if (refreshed) return upload<T>(path, formData, true);
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

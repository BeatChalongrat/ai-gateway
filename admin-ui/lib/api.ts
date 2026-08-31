// Admin UI API client (Phase 2A Commit 6).
//
// Auth model: session cookie (HttpOnly ``admin_session``) + double-submit
// CSRF (``csrf_token`` cookie echoed in the ``X-CSRF-Token`` header on
// mutations). The cookie is set by POST /auth/session/login and cleared
// by POST /auth/session/logout on the backend.
//
// Critically: no API key is bundled in the browser any more. The legacy
// public admin-API-key env var is removed — anyone can verify by grepping
// the built bundle (``admin-ui/.next/static``) for gw_ prefixes.

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";
const SESSION_CACHE_KEY = "admin_session_cache";
let clearingSession: Promise<void> | null = null;

export class ApiError extends Error {
  status: number;
  body: any;
  constructor(status: number, body: any) {
    super(`API error ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function getApiErrorMessage(error: any, fallback: string): string {
  const body = error?.body;
  const nested = body?.error;
  const candidate =
    (typeof nested?.message === "string" && nested.message) ||
    (typeof nested === "string" && nested) ||
    (typeof body?.detail === "string" && body.detail) ||
    (typeof body?.message === "string" && body.message) ||
    (typeof body === "string" && body.trim()) ||
    "";

  if (candidate) return candidate;
  if (error?.status === 0) return `${fallback}: request timed out`;
  if (typeof error?.status === "number" && error.status > 0) {
    return `${fallback} (HTTP ${error.status})`;
  }
  return `${fallback}: network request failed`;
}

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = name + "=";
  for (const raw of document.cookie.split(";")) {
    const c = raw.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return "";
}

async function clearInvalidSession() {
  if (typeof window === "undefined") return;
  if (!clearingSession) {
    clearingSession = fetch(`${BASE_URL}/auth/session/logout`, {
      method: "POST",
      credentials: "include",
    })
      .catch(() => undefined)
      .finally(() => {
        localStorage.removeItem(SESSION_CACHE_KEY);
        document.cookie = "csrf_token=; Max-Age=0; path=/";
        clearingSession = null;
      });
  }
  await clearingSession;
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

// Dedup: if the same GET is already in-flight, reuse its promise
const _inflight = new Map<string, Promise<any>>();

async function request(path: string, options: RequestInit = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    // Explicit charset matches the backend's UTF8JSONResponse
    // contract — browsers default to UTF-8 anyway, but pinning it
    // here keeps the wire signal unambiguous if a proxy ever
    // synthesises a different default.
    "Content-Type": "application/json; charset=utf-8",
    ...(options.headers as Record<string, string>),
  };

  // Double-submit CSRF: on every mutation, echo the csrf_token cookie
  // into the X-CSRF-Token header. Same-origin JS can read the cookie;
  // cross-site JS cannot, so a forged POST from evil.example is blocked.
  if (method !== "GET" && method !== "HEAD") {
    const csrf = readCookie("csrf_token");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 20000);

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers,
      credentials: "include", // send the admin_session cookie
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!res.ok) {
      let body;
      try { body = await res.json(); } catch { body = await res.text(); }
      if (res.status === 401) {
        void clearInvalidSession();
      }
      throw new ApiError(res.status, body);
    }
    if (res.status === 204) return null;
    return res.json();
  } catch (err: any) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      throw new ApiError(0, { error: { message: "Request timed out" } });
    }
    throw err;
  }
}

function deduplicatedGet(path: string): Promise<any> {
  const existing = _inflight.get(path);
  if (existing) return existing;

  const promise = request(path).finally(() => {
    _inflight.delete(path);
  });
  _inflight.set(path, promise);
  return promise;
}

export const api = {
  get: (path: string) => deduplicatedGet(path),
  post: (path: string, data: any) => request(path, { method: "POST", body: JSON.stringify(data) }),
  put: (path: string, data: any) => request(path, { method: "PUT", body: JSON.stringify(data) }),
  patch: (path: string, data: any) => request(path, { method: "PATCH", body: JSON.stringify(data) }),
  delete: (path: string) => request(path, { method: "DELETE" }),
};

export async function fetchJson(path: string) {
  return api.get(path);
}

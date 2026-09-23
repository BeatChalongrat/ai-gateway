// FIX130 E2 — is this device's session real? Asked by middleware.ts.
//
// Before FIX130 the middleware counted "a portal cookie is present" as
// signed in. A device holding a REVOKED token (signed out elsewhere,
// password reset, token_version bump) was therefore sent from /login
// straight back to the ?redirect= target — Web Chat — which found the token
// dead and sent it back to /login: a loop. The edge has no signing secret
// (PORTAL_JWT_SECRET is not in the Portal container), so the backend — the
// one place that checks signature, the revocation list, user status and
// token_version — is asked, over the compose network (BACKEND_URL), with a
// short timeout:
//
//   1. an access cookie → GET /portal/me. 200 = signed in.
//   2. no access cookie, or /portal/me said 401/403 (expired, revoked,
//      suspended) → POST /portal/auth/refresh with the cookies, the same
//      rotation the page itself does. 200 = signed in, and its Set-Cookie
//      (the rotated pair) is passed through to the browser; 401/403 = signed
//      out, and its Set-Cookie (the backend clearing both cookies, with the
//      right Domain/Path) is passed through too.
//   3. anything else — network error, timeout, 5xx during a redeploy —
//      is "unknown": the backend's answer could not be had, which says
//      nothing about the session.
//
// Mirrors FIX125/FIX129 in Web Chat (apps/web-chat/src/lib/auth.ts): only a
// definite REJECTION ends a session; "unreachable" never does.

export const ACCESS_COOKIE = "portal_access_token";
export const REFRESH_COOKIE = "portal_refresh_token";

export type SessionState = "signed-in" | "signed-out" | "unknown";

export interface SessionVerdict {
  state: SessionState;
  /** Set-Cookie headers from the backend to relay to the browser. */
  setCookies: string[];
  /** Why (for tests and logs): no-cookie | me-ok | refreshed | rejected | unreachable. */
  reason: "no-cookie" | "me-ok" | "refreshed" | "rejected" | "unreachable";
}

export interface CheckOptions {
  backendUrl?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

export const SESSION_CHECK_TIMEOUT_MS = 1500;

function backendBase(explicit?: string): string {
  const raw =
    explicit ||
    (typeof process !== "undefined" ? process.env.BACKEND_URL : undefined) ||
    "http://localhost:8080";
  return raw.replace(/\/$/, "");
}

/**
 * All Set-Cookie values of a response. Headers.getSetCookie keeps them
 * apart; the fallback splits a joined header only at a comma that starts a
 * new `name=` pair (an `expires=Wed, 23 Sep …` comma does not).
 */
export function setCookiesOf(res: Response): string[] {
  const h = res.headers as Headers & { getSetCookie?: () => string[] };
  if (typeof h.getSetCookie === "function") return h.getSetCookie();
  const joined = res.headers.get("set-cookie");
  if (!joined) return [];
  return joined.split(/,(?=\s*[^;,=\s]+=[^;,]*)/).map((s) => s.trim());
}

type Outcome = { kind: "ok" | "rejected" | "unreachable"; res?: Response };

async function ask(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  fetchImpl: typeof fetch,
): Promise<Outcome> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(url, { ...init, signal: controller.signal, cache: "no-store", redirect: "manual" });
    if (res.ok) return { kind: "ok", res };
    if (res.status === 401 || res.status === 403) return { kind: "rejected", res };
    return { kind: "unreachable", res };
  } catch {
    return { kind: "unreachable" };
  } finally {
    clearTimeout(timer);
  }
}

function cookieHeader(access?: string, refresh?: string): string {
  const parts: string[] = [];
  if (access) parts.push(`${ACCESS_COOKIE}=${access}`);
  if (refresh) parts.push(`${REFRESH_COOKIE}=${refresh}`);
  return parts.join("; ");
}

export async function checkSession(
  access: string | undefined,
  refresh: string | undefined,
  opts: CheckOptions = {},
): Promise<SessionVerdict> {
  if (!access && !refresh) return { state: "signed-out", setCookies: [], reason: "no-cookie" };
  const base = backendBase(opts.backendUrl);
  const timeoutMs = opts.timeoutMs ?? SESSION_CHECK_TIMEOUT_MS;
  const fetchImpl = opts.fetchImpl ?? fetch;

  if (access) {
    const me = await ask(
      `${base}/portal/me`,
      { method: "GET", headers: { cookie: cookieHeader(access) } },
      timeoutMs,
      fetchImpl,
    );
    if (me.kind === "ok") return { state: "signed-in", setCookies: [], reason: "me-ok" };
    if (me.kind === "unreachable") return { state: "unknown", setCookies: [], reason: "unreachable" };
    // rejected: expired, revoked or suspended — the refresh token decides.
  }

  const rf = await ask(
    `${base}/portal/auth/refresh`,
    {
      method: "POST",
      headers: {
        cookie: cookieHeader(access, refresh),
        "content-type": "application/json; charset=utf-8",
      },
    },
    timeoutMs,
    fetchImpl,
  );
  if (rf.kind === "ok") return { state: "signed-in", setCookies: setCookiesOf(rf.res!), reason: "refreshed" };
  if (rf.kind === "rejected") return { state: "signed-out", setCookies: setCookiesOf(rf.res!), reason: "rejected" };
  return { state: "unknown", setCookies: [], reason: "unreachable" };
}

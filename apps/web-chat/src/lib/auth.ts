// Auth for the Portal-integrated Web Chat.
//
// HttpOnly cookie migration: chat no longer reads or stores the token.
// The SRAN Portal (and the gateway) set ``portal_access_token`` /
// ``portal_refresh_token`` as HttpOnly cookies; the browser sends them
// automatically on cross-origin calls once we pass
// ``credentials: "include"``. JS can't read them — no token in URL,
// localStorage, or JS cookies.
//
// Two cross-origin calls go to the gateway (FastAPI) rather than
// chat-edge: refresh and the session-user lookup. The gateway is the
// identity authority; chat-edge only verifies the access cookie.

// Gateway (FastAPI) origin — the identity authority that mints/rotates
// the session cookies and serves /portal/me. Configurable for prod.
const GATEWAY_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_GATEWAY_URL) ||
  "http://localhost:8080";

export const PORTAL_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PORTAL_URL) ||
  "http://localhost:3001";

// Hosted mode (desktop shell): the native host owns the session — it
// injects the cookies, rotates them on its own timer, and treats any
// navigation to the Portal login as its re-auth signal. The page must
// therefore never rotate the refresh token itself: a page-side refresh
// would consume the token the host holds and log the HOST out
// (WEBVIEW2_SPIKE_REPORT.md finding 7).
//
// FIX20 §6.5 — a RUNTIME decision, not a build flag. ONE web-chat build
// serves both the browser and the Electron shell, so NEXT_PUBLIC_HOSTED
// (baked at build time) could never be right for both: set, browser users
// got desktop-only panels that cannot work; unset (the deployed default),
// the desktop app hid five of its screens. The host is detectable instead:
// the Electron preload exposes `window.sranHost` (contextBridge, the E3
// surface) BEFORE any page script runs — Electron's preload contract, and
// the preload obtains its nonce synchronously for exactly that reason — so
// evaluating this once at module load is both correct and early enough for
// the timing-sensitive use (refreshSession / startSilentRefresh must see it
// before the page's first refresh attempt; they do, because this module is
// evaluated before either can run).
//
// Components must NOT read HOSTED_MODE during render (server HTML has no
// window → hydration mismatch); they use hooks/useHostedMode, which hydrates
// with the server value and switches after.
export function detectHostedMode(win: unknown = typeof window === "undefined" ? undefined : window): boolean {
  if (typeof win !== "object" || win === null) return false;
  const host = (win as { sranHost?: unknown }).sranHost;
  if (typeof host !== "object" || host === null) return false;
  const h = host as { postMessage?: unknown; addEventListener?: unknown };
  return typeof h.postMessage === "function" && typeof h.addEventListener === "function";
}

export const HOSTED_MODE: boolean = detectHostedMode();

/**
 * FIX125 M6 — the post-login return target: the page the person is ON
 * (pathname + search), not a hard-coded `/chat`. Bouncing to `/chat`
 * dropped `/chat/<id>`, so after re-login the next send opened a NEW
 * conversation on the default model with default settings.
 *
 * Same-origin only: the path must be a single-slash absolute path, and
 * resolving it against this origin must stay on this origin (rejects
 * `//evil.example`, `/\evil.example`, `https://…`, `javascript:` and the
 * like). Anything else falls back to `${origin}/chat`.
 */
export function portalReturnTarget(
  origin: string,
  pathAndSearch: string | null | undefined,
): string {
  const fallback = `${origin}/chat`;
  if (typeof pathAndSearch !== "string" || pathAndSearch.length === 0) return fallback;
  if (!pathAndSearch.startsWith("/")) return fallback;
  if (pathAndSearch.startsWith("//") || pathAndSearch.startsWith("/\\")) return fallback;
  // Control characters (incl. tab/newline, which URL parsing strips) have no
  // place in a return path.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(pathAndSearch)) return fallback;
  let resolved: URL;
  try {
    resolved = new URL(pathAndSearch, origin);
  } catch {
    return fallback;
  }
  if (resolved.origin !== origin) return fallback;
  return `${resolved.origin}${resolved.pathname}${resolved.search}`;
}

/** The Portal login URL that brings the person back to `loc` afterwards. */
export function portalLoginUrl(loc: Pick<Location, "origin" | "pathname" | "search">): string {
  const ret = encodeURIComponent(portalReturnTarget(loc.origin, `${loc.pathname}${loc.search}`));
  return `${PORTAL_URL}/login?redirect=${ret}`;
}

/**
 * Bounce to the Portal login with the current Web Chat page as the post-login
 * return target.
 *
 * FIX129 B2 — this runs when the session is really over (a rejected refresh).
 * The Portal's middleware treats a PRESENT cookie as "signed in" and sends
 * anyone on /login straight back to the ?redirect= target, so a device that
 * still holds a revoked access cookie (signed out elsewhere) would loop
 * login → chat → login. Expire the stale HttpOnly cookies through the
 * gateway's logout first (its Set-Cookie clears them; JS cannot), bounded to
 * 1.5 s and best effort, then navigate. Hosted mode keeps the synchronous
 * navigation the shell intercepts as its re-auth signal.
 */
export function redirectToPortalLogin(): void {
  if (typeof window === "undefined") return;
  const go = () => {
    window.location.href = portalLoginUrl(window.location);
  };
  if (HOSTED_MODE) {
    go();
    return;
  }
  void clearSessionCookies().then(go, go);
}

/** Expire the session cookies on this device via the gateway (best effort, ≤1.5 s). */
export function clearSessionCookies(): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, 1500);
    let p: Promise<unknown>;
    try {
      p = fetch(`${GATEWAY_URL}/portal/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        keepalive: true,
      });
    } catch {
      p = Promise.resolve();
    }
    p.catch(() => undefined).then(() => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/** The session user as the chat app needs it (derived from /portal/me). */
export interface SessionUser {
  user_id: string;
  tenant_id: string;
  email: string | null;
  role: string;
}

interface PortalMeResponse {
  id: string;
  email?: string | null;
  tenant_id?: string | null;
  tenant_role?: string | null;
}

/**
 * Resolve the current session from the gateway's /portal/me using the
 * HttpOnly cookie. Returns null when unauthenticated (401).
 */
export async function fetchSessionUser(): Promise<SessionUser | null> {
  try {
    const res = await fetch(`${GATEWAY_URL}/portal/me`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) return null;
    const me = (await res.json()) as PortalMeResponse;
    if (!me?.id) return null;
    return {
      user_id: me.id,
      // tenant_id may be null transiently; fall back to the user id so
      // downstream filters still have a stable key.
      tenant_id: me.tenant_id ?? me.id,
      email: me.email ?? null,
      role: me.tenant_role ?? "member",
    };
  } catch {
    return null;
  }
}

/**
 * FIX125 M6 — what a refresh attempt found:
 *   "ok"          the gateway rotated the cookies;
 *   "rejected"    the gateway answered 401/403 — the session is really over;
 *   "unreachable" no answer we can trust (network error, 5xx during a
 *                 redeploy, any other status) — the session may be fine.
 * Only "rejected" may send the person to the login page; "unreachable"
 * keeps the page and lets the next tick/focus/request try again.
 */
export type RefreshOutcome = "ok" | "rejected" | "unreachable";

/**
 * Rotate the session via the gateway refresh endpoint. The refresh token
 * rides the HttpOnly cookie; on success the gateway sets fresh access +
 * refresh cookies.
 */
export async function refreshSession(): Promise<RefreshOutcome> {
  // Hosted mode: refusing here (rather than only in auth-refresh.ts)
  // also covers api.ts's 401→refresh path, so EVERY page-side 401
  // escalates straight to redirectToPortalLogin() — which the shell
  // cancels and answers with a host-side refresh. One holder rotates.
  if (HOSTED_MODE) return "rejected";
  try {
    const res = await fetch(`${GATEWAY_URL}/portal/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
    if (res.ok) return "ok";
    if (res.status === 401 || res.status === 403) return "rejected";
    return "unreachable";
  } catch {
    return "unreachable";
  }
}

const LEGACY_COOKIE = "portal_access_token";

/** Best-effort scrub of any legacy non-HttpOnly token a prior build wrote. */
export function clearLegacyToken(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${LEGACY_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/**
 * Log out: revoke + clear the session cookies on the gateway, then bounce
 * to the Portal login. Fire-and-forget on the network call — we redirect
 * regardless so the UX is immediate.
 */
export async function logoutSession(): Promise<void> {
  // Hosted mode (E3 §2): logout is a host operation. The host revokes the
  // session server-side, clears its store and cookie jar, blanks the page and
  // shows its own signed-out view — the page has nothing left to do. Only if
  // the bridge is unavailable (or refuses) does the browser path below run,
  // which the host still recognises by observation (the E2 fallback).
  if (HOSTED_MODE) {
    try {
      const { bridge } = await import("./bridge");
      const result = await bridge.hostQuery("session.logout", {}, 10_000);
      if (result.ok) return;
    } catch {
      /* fall through to the browser path */
    }
  }
  try {
    await fetch(`${GATEWAY_URL}/portal/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch {
    /* ignore — redirect anyway */
  }
  clearLegacyToken();
  if (typeof window !== "undefined") {
    window.location.href = `${PORTAL_URL}/login`;
  }
}

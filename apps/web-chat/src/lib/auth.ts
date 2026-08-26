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

/** Bounce to the Portal login with Web Chat as the post-login return target. */
export function redirectToPortalLogin(): void {
  if (typeof window === "undefined") return;
  const ret = encodeURIComponent(`${window.location.origin}/chat`);
  window.location.href = `${PORTAL_URL}/login?redirect=${ret}`;
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
 * Rotate the session via the gateway refresh endpoint. The refresh token
 * rides the HttpOnly cookie; on success the gateway sets fresh access +
 * refresh cookies. Returns true on success.
 */
export async function refreshSession(): Promise<boolean> {
  // Hosted mode: refusing here (rather than only in auth-refresh.ts)
  // also covers api.ts's 401→refresh path, so EVERY page-side 401
  // escalates straight to redirectToPortalLogin() — which the shell
  // cancels and answers with a host-side refresh. One holder rotates.
  if (HOSTED_MODE) return false;
  try {
    const res = await fetch(`${GATEWAY_URL}/portal/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
    return res.ok;
  } catch {
    return false;
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

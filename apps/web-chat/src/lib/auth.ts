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

// Token sourcing for Portal-integrated Web Chat.
//
// Phase 1.5: chat-edge no longer mints its own user tokens. The
// SRAN Portal (port 3001) is the identity authority. The portal
// writes `portal_access_token` to localStorage and mirrors it to a cookie:
//
//     portal_access_token=<jwt>; Path=/; Max-Age=3600; SameSite=Lax
//
// Cross-port on the same hostname (localhost:3001 -> localhost:3002)
// shares cookies; localStorage does not. We read from the cookie first,
// then accept a `?token=...` query param as a fallback for cross-domain
// redirect flows.

export const PORTAL_TOKEN_COOKIE = "portal_access_token";

export function getPortalToken(): string | null {
  if (typeof window === "undefined") return null;

  // 1. Cookie - shared across ports on localhost.
  const cookieMatch = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${PORTAL_TOKEN_COOKIE}=`));
  if (cookieMatch) {
    const value = cookieMatch.split("=").slice(1).join("=");
    const token = decodeURIComponent(value);
    if (token && token.length > 10) return token;
  }

  // 2. URL query - for the cross-domain redirect case.
  const params = new URLSearchParams(window.location.search);
  const tokenParam = params.get("token");
  if (tokenParam && tokenParam.length > 10) {
    document.cookie = [
      `${PORTAL_TOKEN_COOKIE}=${encodeURIComponent(tokenParam)}`,
      "Path=/",
      "Max-Age=3600",
      "SameSite=Lax",
    ].join("; ");
    window.history.replaceState({}, "", window.location.pathname);
    return tokenParam;
  }

  return null;
}

export function isAuthenticated(): boolean {
  return getPortalToken() !== null;
}

/** Drop the portal token cookie. Called when chat-edge returns 401. */
export function clearPortalToken(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${PORTAL_TOKEN_COOKIE}=; Path=/; Max-Age=0`;
}

const PORTAL_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PORTAL_URL) ||
  "http://localhost:3001";

/** Bounce to the Portal login with Web Chat as the post-login return target. */
export function redirectToPortalLogin(): void {
  if (typeof window === "undefined") return;
  const ret = encodeURIComponent(`${window.location.origin}/chat`);
  window.location.href = `${PORTAL_URL}/login?redirect=${ret}`;
}

interface JwtClaims {
  sub?: string;
  email?: string;
  tenant_id?: string | null;
  tenant_role?: "owner" | "member";
  type?: string;
  exp?: number;
}

/** Best-effort decode of the JWT payload (no signature check; server verifies). */
export function decodePortalJwt(token: string): JwtClaims | null {
  try {
    const [, payload] = token.split(".");
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json) as JwtClaims;
  } catch {
    return null;
  }
}

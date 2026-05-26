// Token storage.
//
// Tokens live in TWO places:
//   1. localStorage — read by the client-side API fetch wrapper
//   2. a cookie — read by Next.js middleware to gate server-rendered
//      routes before any JS ships to the browser
//
// The mirror is written in one place (setTokens) so they can't drift.

const ACCESS_KEY = "portal_access_token";
const REFRESH_KEY = "portal_refresh_token";
// Full-JWT cookie name. Kept as a best-effort mirror for cross-port
// consumers (Web Chat on :3002 reads it directly if it makes it).
const COOKIE_NAME = "portal_access_token";
// Short marker cookie used by middleware.ts as a "are you signed in?"
// gate. The full JWT can be > 4 KB (long claim sets, long secret
// names, base64 padding), and browsers silently drop oversize
// cookies — leaving the cookie panel completely empty even though
// localStorage was set fine. The marker cookie is tiny (a single
// byte payload), so it always survives. We don't trust it for
// auth — every API call still presents the Bearer JWT from
// localStorage, and FastAPI re-verifies — but it lets the Edge-
// runtime middleware do its UX redirect without parsing the JWT.
const MARKER_COOKIE = "portal_logged_in";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function getToken(): string | null {
  if (!isBrowser()) return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (!isBrowser()) return null;
  return localStorage.getItem(REFRESH_KEY);
}

// Cookie attribute string — keep one source of truth so set/clear
// stay in sync and any future hardening (Secure / Domain) lives here.
// JWTs are already base64url-safe (no characters that require
// percent-encoding), so we write the raw token to avoid any reader
// having to round-trip through decodeURIComponent.
const COOKIE_ATTRS = "Path=/; Max-Age=3600; SameSite=Lax";
const CLEAR_ATTRS = "Path=/; Max-Age=0; SameSite=Lax";

function writeCookies(token: string): void {
  // Marker first — tiny payload, guaranteed to fit even when the
  // full-JWT write below gets silently dropped by the browser.
  document.cookie = `${MARKER_COOKIE}=1; ${COOKIE_ATTRS}`;
  // Best-effort: write the full token cookie for cross-port readers
  // (Web Chat on :3002). If the JWT is over the per-cookie size
  // limit the browser drops this silently — that's fine, the
  // dashboard's "Open Chat" link passes the token through the URL
  // as a fallback (see app/(app)/dashboard/page.tsx).
  document.cookie = `${COOKIE_NAME}=${token}; ${COOKIE_ATTRS}`;
}

export function setTokens(access: string, refresh: string): void {
  if (!isBrowser()) return;
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
  writeCookies(access);
}

export function setAccessToken(access: string): void {
  if (!isBrowser()) return;
  localStorage.setItem(ACCESS_KEY, access);
  writeCookies(access);
}

export function clearTokens(): void {
  if (!isBrowser()) return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  // Match attributes on delete or the browser keeps the original
  // cookie. Both must go on logout.
  document.cookie = `${MARKER_COOKIE}=; ${CLEAR_ATTRS}`;
  document.cookie = `${COOKIE_NAME}=; ${CLEAR_ATTRS}`;
}

export function isAuthed(): boolean {
  return !!getToken();
}

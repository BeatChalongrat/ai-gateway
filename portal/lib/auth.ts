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
const COOKIE_NAME = "portal_access_token";

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

export function setTokens(access: string, refresh: string): void {
  if (!isBrowser()) return;
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
  // Mirror access token to a cookie for middleware. We do NOT store
  // the refresh token in a cookie — it never needs to travel to SSR.
  // Max age matches the backend's portal_access_token_expire_minutes
  // default (60 min). SameSite=Lax is enough since we only need it
  // for first-party nav.
  document.cookie = [
    `${COOKIE_NAME}=${encodeURIComponent(access)}`,
    "Path=/",
    "Max-Age=3600",
    "SameSite=Lax",
  ].join("; ");
}

export function setAccessToken(access: string): void {
  if (!isBrowser()) return;
  localStorage.setItem(ACCESS_KEY, access);
  document.cookie = [
    `${COOKIE_NAME}=${encodeURIComponent(access)}`,
    "Path=/",
    "Max-Age=3600",
    "SameSite=Lax",
  ].join("; ");
}

export function clearTokens(): void {
  if (!isBrowser()) return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  document.cookie = `${COOKIE_NAME}=; Path=/; Max-Age=0`;
}

export function isAuthed(): boolean {
  return !!getToken();
}

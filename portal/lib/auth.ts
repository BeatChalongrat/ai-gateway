// Portal client-side auth state.
//
// HttpOnly cookie migration: the portal no longer stores tokens. The
// backend sets ``portal_access_token`` + ``portal_refresh_token`` as
// HttpOnly cookies on login/refresh, so JS (and any XSS) can't read
// them. Every API call rides those cookies via ``credentials: "include"``
// (see lib/api.ts), and silent refresh keeps them fresh
// (see lib/auth-refresh.ts).
//
// This module now only owns one tiny job: scrubbing the *legacy*
// localStorage / JS-cookie tokens that older builds wrote, so a user
// upgrading mid-session doesn't leave a readable token lying around.

const LEGACY_LOCALSTORAGE_KEYS = [
  "portal_access_token",
  "portal_refresh_token",
];

const LEGACY_COOKIES = ["portal_access_token", "portal_logged_in"];

/** Remove any token a pre-migration build left in localStorage / JS cookies. */
export function clearLegacyTokens(): void {
  if (typeof window === "undefined") return;
  try {
    for (const k of LEGACY_LOCALSTORAGE_KEYS) localStorage.removeItem(k);
  } catch {
    /* private-mode / disabled storage — ignore */
  }
  for (const name of LEGACY_COOKIES) {
    document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
  }
}

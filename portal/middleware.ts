import { NextRequest, NextResponse } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE, checkSession } from "./lib/session-check";

// Edge middleware gate. The backend sets the auth tokens as HttpOnly
// cookies (access 60 min, refresh 90 days); edge middleware runs
// server-side, so it CAN read them.
//
// FIX130 E2 — "a cookie is present" is no longer "signed in". A device that
// still held a REVOKED token (signed out elsewhere, password reset) was
// sent from /login straight back to the ?redirect= target, which bounced
// it back to /login: a loop (FIX129 B2 found it from the Web Chat side).
// The session is now checked for real, by the backend (lib/session-check):
//
//   signed-in   /portal/me accepted the access token, or a refresh
//               succeeded (an idle user whose access token lapsed is NOT
//               bounced; the rotated cookies ride this response).
//   signed-out  the backend REJECTED both (401/403): the stale cookies are
//               cleared on this response, and the login page renders
//               instead of redirecting — no loop.
//   unknown     the backend could not be asked (down, timeout, 5xx). The
//               login page renders (never a redirect, so never a loop);
//               app pages render as before (fail open for the SHELL only —
//               every API call they make is still checked by the backend,
//               which is where the data is). A backend blip therefore
//               neither locks everyone out nor loops anyone.
//
// Pages with no portal cookie at all are decided without a network call,
// exactly as before.

// Routes that should render the same way for everyone — authed users
// still see the landing page, they just get a different nav.
// /forgot-password + /reset-password are here (not AUTH_PATHS) so a
// signed-in user clicking a reset email link still reaches the form —
// and an unauthenticated user isn't bounced to /login before it loads
// (pre-Phase-1 both were unlisted, which made password reset
// unreachable: the email link redirected straight to /login).
// /healthz (FIX134 C1) is the liveness route the Caddy front door probes:
// public, so it is answered without the session check below — a slow or
// down backend must never turn a health probe into a redirect or a timeout.
const PUBLIC_PATHS = [
  "/",
  "/docs",
  "/pricing",
  "/security",
  "/privacy",
  "/terms",
  "/forgot-password",
  "/reset-password",
  "/healthz",
];

// Routes that should *redirect away* if the user is already logged in
// (so signed-in users don't land back on /login or /signup).
// /oauth-complete: mid-OAuth the browser holds only the NextAuth cookie
// (portal cookies come AFTER the cookie-bridge POST), so signedIn is
// false — unlisted, this page was bounced to /login and the OAuth flow
// could never finish. As an AUTH_PATH the unauthenticated load falls
// through to next(); once the bridge sets the portal cookies, a reload
// redirects to the ?redirect= target / dashboard, which is the same
// place the page itself forwards to.
const AUTH_PATHS = ["/login", "/signup", "/invite", "/oauth-complete"];

const WEB_CHAT_ORIGIN = (
  process.env.NEXT_PUBLIC_WEB_CHAT_URL || "http://localhost:3002"
).replace(/\/$/, "");

// Validate a ``?redirect=`` target before honouring it for an
// already-authed user — same allowlist as the login form: same-origin
// relative paths, or the configured Web Chat origin. Anything else is
// dropped (open-redirect / credential-phish guard).
function safeRedirect(raw: string | null, reqOrigin: string): string | null {
  if (!raw) return null;
  if (raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\")) {
    return raw;
  }
  try {
    const u = new URL(raw);
    const webChatOrigin = new URL(WEB_CHAT_ORIGIN).origin;
    if (u.origin === reqOrigin || u.origin === webChatOrigin) {
      return u.toString();
    }
  } catch {
    /* fall through */
  }
  return null;
}

/** Copy the backend's Set-Cookie headers (rotation or clearing) onto ``res``. */
function relayCookies(res: NextResponse, setCookies: string[]): NextResponse {
  for (const c of setCookies) res.headers.append("set-cookie", c);
  return res;
}

/** Expire both portal cookies on this host (backup to the backend's own clear). */
function clearPortalCookies(res: NextResponse): NextResponse {
  for (const name of [ACCESS_COOKIE, REFRESH_COOKIE]) {
    res.cookies.set(name, "", { path: "/", maxAge: 0, httpOnly: true, sameSite: "lax" });
  }
  return res;
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic = PUBLIC_PATHS.some(
    (p) => pathname === p || pathname.startsWith(p + "?")
  );
  const isAuthPage = AUTH_PATHS.some((p) => pathname.startsWith(p));

  // Landing page is always reachable.
  if (isPublic) {
    return NextResponse.next();
  }

  const access = request.cookies.get(ACCESS_COOKIE)?.value || undefined;
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value || undefined;
  const verdict = await checkSession(access, refresh);

  if (verdict.state === "signed-in") {
    // Already-signed-in users bouncing off /login or /signup go to the
    // app — but PRESERVE a valid ``?redirect=`` so the Web Chat handoff
    // completes (the old code stripped it and dumped them on /dashboard,
    // breaking "I clicked Sign in from chat" — audit finding #6.9).
    if (isAuthPage) {
      const target = safeRedirect(
        request.nextUrl.searchParams.get("redirect"),
        request.nextUrl.origin
      );
      if (target) {
        return relayCookies(
          NextResponse.redirect(new URL(target, request.nextUrl.origin)),
          verdict.setCookies
        );
      }
      const url = request.nextUrl.clone();
      url.pathname = "/dashboard";
      url.search = "";
      return relayCookies(NextResponse.redirect(url), verdict.setCookies);
    }
    return relayCookies(NextResponse.next(), verdict.setCookies);
  }

  if (verdict.state === "unknown") {
    // The backend could not be asked: render whatever was asked for — the
    // login page renders (no redirect, so no loop), app pages render and
    // their API calls get the backend's own answer.
    return NextResponse.next();
  }

  // signed-out: no cookie, or the backend rejected the session. Stale
  // cookies are expired on this response (the backend's clear, which
  // carries the configured Domain, plus a host-only clear).
  const hadCookie = !!(access || refresh);
  if (isAuthPage) {
    const res = NextResponse.next();
    return hadCookie ? relayCookies(clearPortalCookies(res), verdict.setCookies) : res;
  }

  // Unauthed users hitting the app shell go to /login, carrying the
  // originally-requested path back so post-login can return them there.
  const url = request.nextUrl.clone();
  const original = pathname + (request.nextUrl.search || "");
  url.pathname = "/login";
  url.search = "";
  url.searchParams.set("redirect", original);
  const res = NextResponse.redirect(url);
  return hadCookie ? relayCookies(clearPortalCookies(res), verdict.setCookies) : res;
}

export const config = {
  // Skip static assets and the /api proxy so rewrites to the backend
  // work without the middleware trying to gate them.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};

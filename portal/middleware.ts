import { NextRequest, NextResponse } from "next/server";

// Edge middleware gate. HttpOnly cookie migration: the backend now sets
// the auth tokens as HttpOnly cookies. Edge middleware runs server-side,
// so it CAN read them (the browser-JS same-origin restriction doesn't
// apply here) — we don't need the old JS ``portal_logged_in`` marker.
//
// We gate on the *refresh* cookie, not the access cookie. The access
// token is short-lived (60 min) and rotates; the refresh cookie lives 90
// days and is the real "is there a session?" signal. Gating on the
// access cookie would bounce an active-but-idle user to /login the moment
// their access token lapsed, even though silent refresh / the API
// 401-retry would have renewed it. We do NOT verify the token here (no
// secret at the edge) — the backend re-verifies on every API call.
const SESSION_COOKIE = "portal_refresh_token";
const ACCESS_COOKIE = "portal_access_token";

// Routes that should render the same way for everyone — authed users
// still see the landing page, they just get a different nav.
// /forgot-password + /reset-password are here (not AUTH_PATHS) so a
// signed-in user clicking a reset email link still reaches the form —
// and an unauthenticated user isn't bounced to /login before it loads
// (pre-Phase-1 both were unlisted, which made password reset
// unreachable: the email link redirected straight to /login).
const PUBLIC_PATHS = [
  "/",
  "/docs",
  "/pricing",
  "/security",
  "/privacy",
  "/terms",
  "/forgot-password",
  "/reset-password",
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

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  // Either cookie counts as "signed in"; the refresh cookie is the
  // durable signal but accept the access cookie too for robustness.
  const signedIn =
    !!request.cookies.get(SESSION_COOKIE)?.value ||
    !!request.cookies.get(ACCESS_COOKIE)?.value;

  const isPublic = PUBLIC_PATHS.some(
    (p) => pathname === p || pathname.startsWith(p + "?")
  );
  const isAuthPage = AUTH_PATHS.some((p) => pathname.startsWith(p));

  // Landing page is always reachable.
  if (isPublic) {
    return NextResponse.next();
  }

  // Already-signed-in users bouncing off /login or /signup go to the
  // app — but PRESERVE a valid ``?redirect=`` so the Web Chat handoff
  // completes (the old code stripped it and dumped them on /dashboard,
  // breaking "I clicked Sign in from chat" — audit finding #6.9).
  if (signedIn && isAuthPage) {
    const target = safeRedirect(
      request.nextUrl.searchParams.get("redirect"),
      request.nextUrl.origin
    );
    const url = request.nextUrl.clone();
    if (target) {
      return NextResponse.redirect(new URL(target, request.nextUrl.origin));
    }
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }

  // Unauthed users hitting the app shell go to /login, carrying the
  // originally-requested path back so post-login can return them there.
  if (!signedIn && !isAuthPage) {
    const url = request.nextUrl.clone();
    const original = pathname + (request.nextUrl.search || "");
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("redirect", original);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Skip static assets and the /api proxy so rewrites to the backend
  // work without the middleware trying to gate them.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};

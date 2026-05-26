import { NextRequest, NextResponse } from "next/server";

// "Are you signed in?" cookie used as the middleware gate. The
// short marker (``portal_logged_in=1``) is set by ``lib/auth.ts``
// alongside localStorage on every successful login/refresh, and
// cleared on logout. We do NOT trust this marker for auth — every
// API call still presents the Bearer JWT from localStorage, which
// FastAPI re-verifies. The marker just lets middleware decide UX
// (redirect vs render) without parsing a JWT.
//
// Why not the full JWT? JWTs are routinely >4 KB once tenancy +
// role + version claims are populated, and browsers silently drop
// oversize cookies — middleware would then see no cookie even
// though login succeeded and bounce the user back to /login in an
// infinite loop. The marker cookie is one byte of payload, so it
// always survives.
const TOKEN_COOKIE = "portal_logged_in";

// Routes that should render the same way for everyone — authed users
// still see the landing page, they just get a different nav.
const PUBLIC_PATHS = ["/", "/docs", "/pricing", "/security", "/privacy", "/terms"];

// Routes that should *redirect away* if the user is already logged in
// (so signed-in users don't land back on /login or /signup).
// /invite/:code is an auth path — the page resolves the code server-
// side and shows a pre-filled signup. Already-logged-in users bounce
// to the dashboard because the backend refuses to let a tenanted
// user redeem into another tenant anyway.
const AUTH_PATHS = ["/login", "/signup", "/invite"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const token = request.cookies.get(TOKEN_COOKIE)?.value;
  // Treat the marker presence (any non-empty value) as "signed in".

  const isPublic = PUBLIC_PATHS.some(
    (p) => pathname === p || pathname.startsWith(p + "?")
  );
  const isAuthPage = AUTH_PATHS.some((p) => pathname.startsWith(p));

  // Landing page is always reachable.
  if (isPublic) {
    return NextResponse.next();
  }

  // Already-logged-in users bouncing off /login or /signup go to the
  // app. The landing page intentionally stays visible to them.
  //
  // We clone() then null out the search so a stale ``?redirect=…``
  // param doesn't survive into the dashboard URL. The Web Chat
  // redirect path was generating
  //   /login?redirect=http://localhost:3002/chat
  // and this bounce would otherwise preserve that as
  //   /dashboard?redirect=http://localhost:3002/chat
  // — confusing because the dashboard doesn't honour ``redirect``.
  if (token && isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }

  // Unauthed users hitting the app shell go to /login.
  if (!token && !isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Skip static assets and the /api proxy so rewrites to the backend
  // work without the middleware trying to gate them.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};

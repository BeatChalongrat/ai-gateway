import { NextRequest, NextResponse } from "next/server";

// The access token is mirrored from localStorage to a cookie on
// login/signup so server-side middleware can gate routes without
// JavaScript. See ``lib/auth.ts`` for the write side.
const TOKEN_COOKIE = "portal_access_token";

// Routes that should render the same way for everyone — authed users
// still see the landing page, they just get a different nav.
const PUBLIC_PATHS = ["/", "/docs", "/pricing", "/security", "/privacy", "/terms"];

// Routes that should *redirect away* if the user is already logged in
// (so signed-in users don't land back on /login or /signup).
const AUTH_PATHS = ["/login", "/signup"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const token = request.cookies.get(TOKEN_COOKIE)?.value;

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
  if (token && isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
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

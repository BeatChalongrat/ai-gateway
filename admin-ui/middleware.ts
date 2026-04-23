import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon") ||
    pathname.endsWith(".svg") ||
    pathname.endsWith(".png") ||
    pathname.endsWith(".ico") ||
    pathname.endsWith(".jpg")
  ) {
    return NextResponse.next();
  }

  // Phase 2A Commit 6: check the real admin_session cookie set by the
  // backend at POST /auth/session/login. We don't verify the HMAC here —
  // that would require shipping the session secret into the edge runtime.
  // Instead: cookie presence gates the UI (soft gate); the backend does
  // the cryptographic check on every API call and returns 401 if invalid.
  const hasSession = request.cookies.get("admin_session")?.value;
  if (!hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

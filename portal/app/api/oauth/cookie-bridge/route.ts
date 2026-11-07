// OAuth cookie-bridge (Phase 11 E3).
//
// Converts the NextAuth-held portal token pair into HttpOnly cookies
// WITHOUT ever exposing the tokens to client-side JS. Previously the
// /oauth-complete page called getSession() (tokens readable by any
// script via /api/auth/session) and POSTed them to the backend
// cookie-exchange itself. Now:
//
//   browser  → POST /api/oauth/cookie-bridge            (no body, no tokens)
//   server   → getToken() decodes the HttpOnly NextAuth JWT cookie
//   server   → POST {BACKEND}/portal/auth/cookie-exchange {tokens}
//   response ← backend's Set-Cookie headers relayed verbatim
//
// The NextAuth ``session`` callback no longer copies the tokens onto
// the client-visible session object — they live only inside the
// encrypted, HttpOnly NextAuth JWT, which this server route is the
// sole consumer of.
//
// This is a route handler (not a rewrite): Next's rewrites are
// afterFiles, so this wins over the catch-all /api/:path* proxy.

import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";

// Same server-reachable base the NextAuth callbacks use (docker service
// DNS in compose; localhost in bare-metal dev).
const BACKEND = process.env.BACKEND_URL || "http://localhost:8080";

type PortalTokenClaims = {
  portal_access_token?: string;
  portal_refresh_token?: string;
};

export async function POST(req: NextRequest) {
  const token = (await getToken({ req })) as PortalTokenClaims | null;
  const access = token?.portal_access_token;
  const refresh = token?.portal_refresh_token;
  if (!access || !refresh) {
    return NextResponse.json(
      {
        error: {
          code: "no_oauth_session",
          message: "No OAuth sign-in session found.",
        },
      },
      { status: 401 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/portal/auth/cookie-exchange`, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ access_token: access, refresh_token: refresh }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "backend_unreachable",
          message: "Could not reach the auth service.",
        },
      },
      { status: 502 },
    );
  }

  const body = await upstream.text();
  const res = new NextResponse(body || "{}", {
    status: upstream.status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
  // Relay every Set-Cookie from the backend (access + refresh, or the
  // clears on failure) onto the browser response.
  const setCookies =
    typeof upstream.headers.getSetCookie === "function"
      ? upstream.headers.getSetCookie()
      : upstream.headers.get("set-cookie")
        ? [upstream.headers.get("set-cookie") as string]
        : [];
  for (const cookie of setCookies) {
    res.headers.append("Set-Cookie", cookie);
  }
  return res;
}

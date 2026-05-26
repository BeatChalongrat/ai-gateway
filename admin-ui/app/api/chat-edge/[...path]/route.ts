// Server-side proxy from admin-ui to chat-edge-api.
//
// chat-edge's admin observability endpoints (added in Phase 2
// Session 6) are gated by a shared bearer secret. The secret must
// NEVER reach the browser, so we route admin-ui's client fetches
// through this Next.js route handler, which:
//
//   1. Validates the admin's existing session cookie via the
//      gateway-api's /auth/session/me endpoint. Anything that's
//      not a current admin session 401s here, before we even
//      touch chat-edge.
//   2. Injects ``Authorization: Bearer <CHAT_EDGE_ADMIN_API_KEY>``
//      from the server-side env so the secret never leaves the
//      node runtime.
//   3. Forwards method, query string, body, and the response
//      verbatim — pure pass-through.

import { NextRequest, NextResponse } from "next/server";

const CHAT_EDGE_URL =
  process.env.CHAT_EDGE_API_URL || "http://localhost:4000/api/v1";
const CHAT_EDGE_ADMIN_KEY = process.env.CHAT_EDGE_ADMIN_API_KEY || "";
const GATEWAY_URL =
  process.env.GATEWAY_API_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://localhost:8080";

async function isAdmin(req: NextRequest): Promise<boolean> {
  // The admin-ui session cookie lives on the gateway-api origin.
  // We need to forward whatever cookies the browser sent to us so
  // gateway-api can validate the session.
  const cookie = req.headers.get("cookie") || "";
  if (!cookie) return false;
  try {
    const res = await fetch(`${GATEWAY_URL}/auth/session/me`, {
      headers: { cookie },
      cache: "no-store",
    });
    if (!res.ok) return false;
    const body = await res.json();
    const role = body?.user?.role;
    return role === "admin" || role === "super_admin";
  } catch {
    return false;
  }
}

async function proxy(req: NextRequest, ctx: { params: { path: string[] } }) {
  if (!CHAT_EDGE_ADMIN_KEY) {
    return NextResponse.json(
      { error: { code: "DISABLED", message: "Admin API not configured" } },
      { status: 503 },
    );
  }
  if (!(await isAdmin(req))) {
    return NextResponse.json(
      { error: { code: "UNAUTHORIZED", message: "admin session required" } },
      { status: 401 },
    );
  }

  const subPath = ctx.params.path.join("/");
  const search = req.nextUrl.search;
  const upstream = `${CHAT_EDGE_URL}/admin/${subPath}${search}`;

  const headers: Record<string, string> = {
    Authorization: `Bearer ${CHAT_EDGE_ADMIN_KEY}`,
  };
  const contentType = req.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;

  let body: BodyInit | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.text();
  }

  const upstreamRes = await fetch(upstream, {
    method: req.method,
    headers,
    body,
    cache: "no-store",
  });

  // 204 / 304 carry no body — pass through cleanly.
  if (upstreamRes.status === 204 || upstreamRes.status === 304) {
    return new NextResponse(null, { status: upstreamRes.status });
  }
  const buf = await upstreamRes.arrayBuffer();
  return new NextResponse(buf, {
    status: upstreamRes.status,
    headers: {
      "Content-Type":
        upstreamRes.headers.get("content-type") || "application/json",
    },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

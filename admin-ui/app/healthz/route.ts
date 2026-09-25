import { NextResponse } from "next/server";

// FIX134 C1 — liveness for the Caddy front door (infra/caddy/Caddyfile
// probes /healthz, before it probed /login: a full page render, which timed
// out whenever the box was busy). Answers from this process alone: no auth,
// no database, no fetch. middleware.ts lets /healthz through without the
// admin_session cookie.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({ ok: true, service: "admin-ui" });
}

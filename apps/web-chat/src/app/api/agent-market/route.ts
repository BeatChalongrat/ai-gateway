// Agent Market feature flag. Served by the Next server itself (same origin),
// read from the server's environment at request time — NOT a NEXT_PUBLIC_
// value baked into the bundle — so the flag flips with a restart, not a
// rebuild. With it off the client never calls chat-edge's /agent-market.
// force-dynamic keeps Next from prerendering a frozen response (same pattern
// as /api/version).

export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ enabled: process.env.AGENT_MARKET_ENABLED === "true" });
}

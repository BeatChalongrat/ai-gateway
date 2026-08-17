// Build-id endpoint for the update-available prompt. Served by the Next
// server itself (same origin — NOT chat-edge, whose base is an absolute
// URL on :4000), so the value always reflects the CURRENTLY DEPLOYED
// build. The service worker never caches /api/* paths, so this can't go
// stale. force-dynamic keeps Next from prerendering a frozen response.

export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ build: process.env.NEXT_PUBLIC_BUILD_ID ?? "dev" });
}

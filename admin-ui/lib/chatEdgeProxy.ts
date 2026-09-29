// Rules of the admin-ui → chat-edge proxy (app/api/chat-edge/[...path]).
//
// Kept apart from the route file so they can be tested (a Next route file
// may export only its HTTP methods).
//
// AM7 — the proxy names the signed-in admin to chat-edge. It asks the
// gateway who the session belongs to (GET /auth/session/me), then sends that
// admin's id, email, name and role in X-Admin-* headers next to the admin
// bearer. chat-edge trusts them only behind the bearer (which only this
// server holds) and enforces the real boundary on every endpoint; the checks
// here are the first belt:
//
//   admin, super_admin  → every chat-edge admin path
//   agent_publisher     → agent-market/* only (the catalog and the problems
//                         queue; chat-edge refuses the rest of that door)
//   anything else       → nothing

export interface AdminIdentity {
  id: string;
  email: string;
  username: string;
  role: string;
}

const FULL_ROLES = new Set(["admin", "super_admin"]);

/** The chat-edge admin paths an admin-ui role may reach through the proxy. */
export function roleMayReach(role: string, subPath: string): boolean {
  if (FULL_ROLES.has(role)) return true;
  if (role === "agent_publisher") {
    return subPath === "agent-market" || subPath.startsWith("agent-market/");
  }
  return false;
}

/** A sub-path the proxy forwards: plain segments only (no "." / ".." / slashes inside one). */
export function safeSubPath(segments: string[]): string | null {
  if (!segments.length) return null;
  for (const s of segments) {
    if (!s || s === "." || s === ".." || /[\\/]/.test(s)) return null;
  }
  // "@" stays as it is: chat-edge's router reads a path with a non-canonical
  // escape (%40) raw, and an e-mail in a path segment would not match.
  return segments.map((s) => encodeURIComponent(s).replace(/%40/g, "@")).join("/");
}

/** The headers that name the admin to chat-edge. */
export function adminHeaders(who: AdminIdentity): Record<string, string> {
  return {
    "X-Admin-User-Id": who.id,
    "X-Admin-Email": who.email,
    // header values are Latin-1: a Thai name travels escaped
    "X-Admin-Name": encodeURIComponent(who.username || ""),
    "X-Admin-Role": who.role,
  };
}

/** Response headers passed back as they came (a download keeps its file name). */
export const PASSED_RESPONSE_HEADERS = ["content-type", "content-disposition", "cache-control"] as const;

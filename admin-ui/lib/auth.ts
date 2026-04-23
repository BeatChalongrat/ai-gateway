// Phase 2A Commit 6: auth client for the admin UI.
//
// Sources of truth moved to the backend session:
//   • GET  /auth/session/me      — returns the current user + role
//   • POST /auth/session/logout  — revokes the session row, clears cookies
//
// The localStorage shim is kept only as a single-request role cache for
// nav rendering — it's evicted on logout and refreshed on login.

export type DemoRole = "viewer" | "project_admin" | "tenant_admin" | "admin";

export interface AdminSession {
  role: DemoRole;
  name: string;
  email: string;
}

const CACHE_KEY = "admin_session_cache";
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

export function getSession(): AdminSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function refreshSession(): Promise<AdminSession | null> {
  try {
    const res = await fetch(`${API_BASE}/auth/session/me`, {
      credentials: "include",
    });
    if (!res.ok) return null;
    const body = await res.json();
    const u = body.user;
    const session: AdminSession = {
      role: (u.role as DemoRole) || "viewer",
      name: u.username || u.email,
      email: u.email,
    };
    localStorage.setItem(CACHE_KEY, JSON.stringify(session));
    return session;
  } catch {
    return null;
  }
}

export async function clearSession(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/session/logout`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    // Non-fatal — the cookie clears locally regardless.
  }
  if (typeof window !== "undefined") {
    localStorage.removeItem(CACHE_KEY);
  }
}

// /audit was removed in the 2026-04-23 UX-cleanup pass — it duplicated
// /requests (gateway request log), so both pages were folded into
// /requests and the /audit route now 308-redirects there. The
// admin-operation log lives at /audit-log, which is admin-only.
export const ROLE_NAV: Record<DemoRole, string[]> = {
  viewer: ["/", "/requests", "/usage", "/alerts/events"],
  project_admin: ["/", "/projects", "/api-keys", "/requests", "/usage", "/alerts/events"],
  tenant_admin: ["/", "/projects", "/api-keys", "/tenants", "/requests", "/usage", "/alerts/events"],
  admin: ["/", "/providers", "/models", "/policies", "/routing", "/dlp-coverage", "/tenants", "/projects", "/api-keys", "/portal-users", "/portal-settings", "/portal-token-packs", "/requests", "/usage", "/audit-log", "/alerts/events", "/ai-review"],
};

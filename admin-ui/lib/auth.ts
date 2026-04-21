export type DemoRole = "viewer" | "project_admin" | "admin";

export interface DemoSession {
  role: DemoRole;
  name: string;
  loggedInAt: number;
}

const SESSION_KEY = "sran_demo_session";

export function getSession(): DemoSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setSession(role: DemoRole): void {
  const names: Record<DemoRole, string> = {
    viewer: "Demo Viewer",
    project_admin: "Project Admin",
    admin: "Super Admin",
  };
  const session: DemoSession = { role, name: names[role], loggedInAt: Date.now() };
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  document.cookie = `sran_demo_role=${role}; path=/; max-age=86400`;
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_KEY);
  document.cookie = "sran_demo_role=; path=/; max-age=0";
}

export const ROLE_NAV: Record<DemoRole, string[]> = {
  viewer: ["/", "/requests", "/usage", "/audit", "/alerts/events"],
  project_admin: ["/", "/projects", "/api-keys", "/requests", "/usage", "/audit", "/alerts/events"],
  admin: ["/", "/providers", "/models", "/policies", "/routing", "/dlp-coverage", "/tenants", "/projects", "/api-keys", "/portal-users", "/portal-settings", "/portal-token-packs", "/requests", "/usage", "/audit", "/alerts/events", "/ai-review"],
};

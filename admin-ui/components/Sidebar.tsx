"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { getSession, refreshSession, clearSession, ROLE_NAV, type AdminSession } from "../lib/auth";

const S = { w: 15, h: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const NAV_SECTIONS: { title: string; items: { label: string; href: string; icon: ReactNode }[] }[] = [
  {
    title: "Overview",
    items: [
      { label: "Dashboard", href: "/", icon: <svg {...S}><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg> },
    ],
  },
  {
    title: "Infrastructure",
    items: [
      { label: "Providers", href: "/providers", icon: <svg {...S}><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg> },
      { label: "Models", href: "/models", icon: <svg {...S}><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg> },
      { label: "Policies", href: "/policies", icon: <svg {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> },
      { label: "Routing", href: "/routing", icon: <svg {...S}><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="12" cy="18" r="3"/><path d="M6 9v3a3 3 0 003 3h6a3 3 0 003-3V9"/></svg> },
      { label: "DLP coverage", href: "/dlp-coverage", icon: <svg {...S}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg> },
      { label: "Search", href: "/search-settings", icon: <svg {...S}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> },
    ],
  },
  {
    title: "Tenancy",
    items: [
      { label: "Tenants", href: "/tenants", icon: <svg {...S}><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg> },
      { label: "Projects", href: "/projects", icon: <svg {...S}><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> },
      { label: "API Keys", href: "/api-keys", icon: <svg {...S}><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg> },
    ],
  },
  {
    title: "Portal",
    items: [
      { label: "Users", href: "/portal-users", icon: <svg {...S}><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg> },
      { label: "Settings", href: "/portal-settings", icon: <svg {...S}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg> },
      { label: "Token Packs", href: "/portal-token-packs", icon: <svg {...S}><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg> },
    ],
  },
  {
    title: "Observability",
    items: [
      { label: "Requests", href: "/requests", icon: <svg {...S}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg> },
      { label: "Usage & Cost", href: "/usage", icon: <svg {...S}><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg> },
      { label: "Admin Actions", href: "/audit-log", icon: <svg {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg> },
      { label: "Alert events", href: "/alerts/events", icon: <svg {...S}><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> },
      { label: "AI review", href: "/ai-review", icon: <svg {...S}><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> },
    ],
  },
  {
    title: "Web Chat",
    items: [
      { label: "Chat Analytics", href: "/chat-analytics", icon: <svg {...S}><path d="M3 3v18h18"/><path d="M7 14l3-3 4 4 5-6"/></svg> },
      { label: "Conversation Logs", href: "/chat-conversations", icon: <svg {...S}><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg> },
      { label: "Chat Users", href: "/chat-users", icon: <svg {...S}><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11l-3-3-3 3"/><path d="M19 8v9"/></svg> },
      { label: "Knowledge Bases", href: "/chat-knowledge", icon: <svg {...S}><path d="M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg> },
    ],
  },
];

const Divider = () => <div style={{ height: "1px", background: "rgba(80,130,220,0.06)", margin: "0.125rem 0.875rem" }} />;

export default function Sidebar({
  mobileOpen = false,
  onClose,
}: {
  // Mobile drawer state, driven by AppShell. On desktop (>=768px) these are
  // inert — the sidebar is an in-flow sticky column regardless of `mobileOpen`.
  mobileOpen?: boolean;
  onClose?: () => void;
} = {}) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSessionState] = useState<AdminSession | null>(null);

  useEffect(() => {
    // First paint: render from the cached role so the UI doesn't flash.
    setSessionState(getSession());
    // Then revalidate against the backend; this also catches session
    // expiry while the tab is open.
    refreshSession().then((s) => setSessionState(s));
  }, []);

  const allowedPaths = session ? ROLE_NAV[session.role] : [];

  async function handleLogout() {
    onClose?.();
    await clearSession();
    router.push("/login");
  }

  const filteredSections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => allowedPaths.includes(item.href)),
  })).filter((section) => section.items.length > 0);

  return (
    <aside className={`sran-sidebar${mobileOpen ? " sran-sidebar-open" : ""}`}>
      {/* Brand */}
      <div className="sran-sidebar-brand">
        <img
          src="/sran-logo.png"
          alt="SRAN"
          style={{ width: "36px", height: "36px", borderRadius: "8px", objectFit: "cover" as const, flexShrink: 0, boxShadow: "0 0 10px rgba(80,130,255,0.3), 0 2px 8px rgba(0,0,0,0.4)" }}
        />
        <div>
          <div className="sran-sidebar-brand-name">SRAN</div>
          <div className="sran-sidebar-brand-sub">AI Gateway</div>
        </div>
        {/* Mobile-only close button — backdrop also closes the drawer, but an
            explicit control is more discoverable. Hidden >=768px via CSS. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close menu"
          className="sran-sidebar-close tap-44"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>

      {/* Nav */}
      <nav className="sran-sidebar-nav">
        {filteredSections.map((section, idx) => (
          <div key={section.title}>
            {idx > 0 && <Divider />}
            <div className="sran-sidebar-section">
              <div className="sran-sidebar-section-title">{section.title}</div>
              {section.items.map((item) => {
                // Exact-match OR child-path match. The plain startsWith
                // form caused /audit-log to light up both "Audit" and
                // "Audit Log" because "/audit" is a prefix of
                // "/audit-log". Requiring a trailing slash on the child
                // case prevents that collision for any href that shares
                // a prefix with another route.
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname === item.href ||
                      pathname.startsWith(item.href + "/");
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onClose}
                    className={`sran-sidebar-link ${isActive ? "sran-sidebar-link-active" : ""}`}
                  >
                    <span className="sran-sidebar-icon">{item.icon}</span>
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="sran-sidebar-footer">
        {session ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", minWidth: 0 }}>
              <div style={{ width: "26px", height: "26px", borderRadius: "5px", background: "linear-gradient(135deg, #1a3570 0%, #2850a8 100%)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.6rem", fontWeight: 700, color: "rgba(200,220,255,0.85)", letterSpacing: "0.02em", flexShrink: 0, border: "1px solid rgba(80,130,240,0.18)" }}>
                {session?.name?.charAt(0)?.toUpperCase() || "U"}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: "0.6875rem", fontWeight: 600, color: "rgba(215,228,250,0.85)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", letterSpacing: "0.005em" }}>
                  {session.name}
                </div>
                <div className="sran-badge-role" style={{ display: "inline-block", marginTop: "1px" }}>
                  {session.role.replace("_", " ").toUpperCase()}
                </div>
              </div>
            </div>
            <button
              onClick={handleLogout}
              title="Sign out"
              style={{ background: "none", border: "1px solid rgba(100,140,220,0.2)", borderRadius: "6px", padding: "5px 7px", cursor: "pointer", color: "rgba(160,190,255,0.6)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, transition: "all 0.15s" }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "rgba(100,140,220,0.5)"; e.currentTarget.style.color = "rgba(200,220,255,0.9)"; e.currentTarget.style.background = "rgba(100,140,220,0.08)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "rgba(100,140,220,0.2)"; e.currentTarget.style.color = "rgba(160,190,255,0.6)"; e.currentTarget.style.background = "none"; }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
            </button>
          </>
        ) : (
          <div className="sran-sidebar-footer-text">SRAN AI Gateway v0.1.0</div>
        )}
      </div>
    </aside>
  );
}

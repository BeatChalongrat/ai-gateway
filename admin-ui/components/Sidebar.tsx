"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { getSession, refreshSession, clearSession, ROLE_NAV, type AdminSession } from "../lib/auth";
import { useI18n, type DictKey } from "../lib/i18n";
import { LanguageToggle } from "./LanguageToggle";

const S = { w: 15, h: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

// NAV_SECTIONS is built once at module scope, outside any React tree, so it
// stores dictionary KEYS and the component resolves them at render time —
// switching language re-labels the nav without rebuilding this array.
const NAV_SECTIONS: { title: DictKey; items: { label: DictKey; href: string; icon: ReactNode }[] }[] = [
  {
    title: "nav.section.overview",
    items: [
      { label: "nav.dashboard", href: "/", icon: <svg {...S}><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg> },
    ],
  },
  {
    title: "nav.section.infrastructure",
    items: [
      { label: "nav.providers", href: "/providers", icon: <svg {...S}><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg> },
      { label: "nav.models", href: "/models", icon: <svg {...S}><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg> },
      { label: "nav.policies", href: "/policies", icon: <svg {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> },
      { label: "nav.dlpDetectors", href: "/dlp-detectors", icon: <svg {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg> },
      { label: "nav.routing", href: "/routing", icon: <svg {...S}><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="12" cy="18" r="3"/><path d="M6 9v3a3 3 0 003 3h6a3 3 0 003-3V9"/></svg> },
      { label: "nav.fallback", href: "/fallback", icon: <svg {...S}><path d="M4 6h6l3 4h7"/><path d="M4 18h6l3-4h7"/><path d="M17 7l3 3-3 3"/><path d="M17 11l3 3-3 3"/></svg> },
      { label: "nav.dlpCoverage", href: "/dlp-coverage", icon: <svg {...S}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg> },
      { label: "nav.search", href: "/search-settings", icon: <svg {...S}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> },
    ],
  },
  {
    title: "nav.section.tenancy",
    items: [
      { label: "nav.tenants", href: "/tenants", icon: <svg {...S}><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg> },
      { label: "nav.projects", href: "/projects", icon: <svg {...S}><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> },
      { label: "nav.apiKeys", href: "/api-keys", icon: <svg {...S}><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg> },
    ],
  },
  {
    title: "nav.section.portal",
    items: [
      { label: "nav.portalUsers", href: "/portal-users", icon: <svg {...S}><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg> },
      { label: "nav.portalSettings", href: "/portal-settings", icon: <svg {...S}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg> },
      { label: "nav.tokenPacks", href: "/portal-token-packs", icon: <svg {...S}><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg> },
    ],
  },
  {
    title: "nav.section.observability",
    items: [
      { label: "nav.requests", href: "/requests", icon: <svg {...S}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg> },
      { label: "nav.usage", href: "/usage", icon: <svg {...S}><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg> },
      { label: "nav.auditLog", href: "/audit-log", icon: <svg {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg> },
      { label: "nav.alertEvents", href: "/alerts/events", icon: <svg {...S}><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> },
      { label: "nav.aiReview", href: "/ai-review", icon: <svg {...S}><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> },
    ],
  },
  {
    title: "nav.section.webChat",
    items: [
      { label: "nav.chatAnalytics", href: "/chat-analytics", icon: <svg {...S}><path d="M3 3v18h18"/><path d="M7 14l3-3 4 4 5-6"/></svg> },
      { label: "nav.chatConversations", href: "/chat-conversations", icon: <svg {...S}><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg> },
      { label: "nav.chatUsers", href: "/chat-users", icon: <svg {...S}><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11l-3-3-3 3"/><path d="M19 8v9"/></svg> },
      { label: "nav.chatKnowledge", href: "/chat-knowledge", icon: <svg {...S}><path d="M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg> },
      { label: "nav.chatRetention", href: "/chat-retention", icon: <svg {...S}><path d="M3 6h18"/><path d="M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg> },
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
  const { t } = useI18n();
  const [session, setSessionState] = useState<AdminSession | null>(null);

  // User avatar — a base64 data URL picked via the hidden file input and
  // persisted client-side only (localStorage; there is no upload API).
  const [avatar, setAvatar] = useState<string | null>(null);
  const avatarInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // First paint: render from the cached role so the UI doesn't flash.
    setSessionState(getSession());
    setAvatar(localStorage.getItem("user-avatar"));
    // Then revalidate against the backend; this also catches session
    // expiry while the tab is open.
    refreshSession().then((s) => setSessionState(s));
  }, []);

  function handleAvatarPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string") return;
      try {
        localStorage.setItem("user-avatar", reader.result);
      } catch {
        // Storage quota exceeded (very large image) — still show it for
        // this session, it just won't survive a reload.
      }
      setAvatar(reader.result);
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  }

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
          aria-label={t("nav.closeMenu")}
          className="sran-sidebar-close tap-44"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>

      {/* Nav */}
      <nav className="sran-sidebar-nav">
        {filteredSections.map((section, idx) => (
          <div key={section.title as string}>
            {idx > 0 && <Divider />}
            <div className="sran-sidebar-section">
              <div className="sran-sidebar-section-title">{t(section.title)}</div>
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
                    <span>{t(item.label)}</span>
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
            <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: 1 }}>
              <div
                className="sran-sidebar-avatar"
                role="button"
                tabIndex={0}
                title={t("app.changeAvatar")}
                onClick={() => avatarInputRef.current?.click()}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); avatarInputRef.current?.click(); } }}
              >
                {avatar ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={avatar} alt={t("app.avatarAlt")} />
                ) : (
                  session?.name?.charAt(0)?.toUpperCase() || "U"
                )}
                <span className="sran-sidebar-avatar-cam" aria-hidden>
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#E2E8F0" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                    <circle cx="12" cy="13" r="4" />
                  </svg>
                </span>
                <input
                  ref={avatarInputRef}
                  type="file"
                  accept="image/*"
                  onChange={handleAvatarPick}
                  style={{ display: "none" }}
                />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="sran-sidebar-username">{session.name}</div>
                <div className="sran-sidebar-email">{session.email || `${session.name}@sran.ai`}</div>
              </div>
            </div>
            <LanguageToggle />
            <button
              onClick={handleLogout}
              title={t("app.logout")}
              style={{ background: "none", border: "none", padding: 5, cursor: "pointer", color: "rgba(140,160,200,0.4)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, transition: "color 0.2s" }}
              onMouseEnter={(e) => { e.currentTarget.style.color = "rgba(200,210,230,0.7)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = "rgba(140,160,200,0.4)"; }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
            </button>
          </>
        ) : (
          <div className="sran-sidebar-footer-text">SRAN AI Gateway v0.1.0</div>
        )}
      </div>
    </aside>
  );
}

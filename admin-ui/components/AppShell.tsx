"use client";

import { useEffect, useState } from "react";
import Sidebar from "./Sidebar";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Lock background scroll while the mobile drawer is open.
  useEffect(() => {
    if (!mobileMenuOpen) return;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileMenuOpen]);

  const closeMenu = () => setMobileMenuOpen(false);

  return (
    <div style={{ display: "flex", minHeight: "100vh", width: "100%" }}>
      {/* Mobile top bar — only below md (768px). Desktop is unchanged. */}
      <header
        className="md:hidden"
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: "3.5rem",
          zIndex: 30,
          background: "var(--bg-sidebar)",
          borderBottom: "1px solid rgba(80,130,220,0.12)",
          display: "flex",
          alignItems: "center",
          gap: "0.75rem",
          padding: "0 1rem",
          color: "#e2e8f0",
        }}
      >
        <button
          type="button"
          onClick={() => setMobileMenuOpen(true)}
          aria-label="Open menu"
          aria-expanded={mobileMenuOpen}
          className="tap-44"
          style={{
            background: "none",
            border: "1px solid rgba(100,140,220,0.2)",
            borderRadius: "8px",
            color: "inherit",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            flexShrink: 0,
          }}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", minWidth: 0 }}>
          <img src="/sran-logo.png" alt="" style={{ width: 26, height: 26, borderRadius: 6, flexShrink: 0 }} />
          <span style={{ fontSize: "0.9rem", fontWeight: 700, letterSpacing: "0.06em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            SRAN AI Gateway
          </span>
        </div>
      </header>

      {/* Backdrop — closes the drawer on tap. Only below md. */}
      {mobileMenuOpen && (
        <div
          className="md:hidden"
          onClick={closeMenu}
          aria-hidden="true"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 40,
            background: "rgba(0,0,0,0.5)",
            backdropFilter: "blur(2px)",
          }}
        />
      )}

      <Sidebar mobileOpen={mobileMenuOpen} onClose={closeMenu} />

      <main
        // pt clears the fixed mobile top bar (3.5rem) on small screens; desktop
        // restores the original 1.75rem 2.25rem 2.5rem padding. overflowX is no
        // longer hidden — pages now scroll their own wide content instead of
        // having it silently clipped.
        className="pt-[4.5rem] px-4 pb-10 md:pt-7 md:px-9 md:pb-10"
        style={{
          flex: 1,
          minWidth: 0,
          overflowY: "auto",
          overflowX: "visible",
          maxHeight: "100vh",
        }}
      >
        <div style={{ maxWidth: "1280px" }}>{children}</div>
      </main>
    </div>
  );
}

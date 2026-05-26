"use client";

import { useSelectedLayoutSegments } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Sidebar } from "@/components/chat/Sidebar";
import { Spinner } from "@/components/ui/Spinner";
import { getPortalToken, redirectToPortalLogin } from "@/lib/auth";
import { useAuthStore } from "@/stores/authStore";

const COLLAPSED_KEY = "sran-webchat-sidebar-collapsed";

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const segments = useSelectedLayoutSegments();
  const activeId = segments?.[0] ?? null;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const checkAuth = useAuthStore((s) => s.checkAuth);

  // Hydrate collapsed state from localStorage AFTER mount so the
  // server render isn't tagged with a client-only state.
  useEffect(() => {
    if (typeof window === "undefined") return;
    setCollapsed(window.localStorage.getItem(COLLAPSED_KEY) === "1");
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      }
      return next;
    });
  }

  // See longer comment below: synchronous read on first render.
  const initialToken = useMemo(() => {
    if (typeof window === "undefined") return null;
    return getPortalToken();
  }, []);
  const [isReady, setIsReady] = useState(!!initialToken);

  useEffect(() => {
    if (initialToken) checkAuth();
  }, [initialToken, checkAuth]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!initialToken) {
      redirectToPortalLogin();
      return;
    }
    setIsReady(true);
  }, [initialToken]);

  if (!isReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg-base text-text-secondary">
        <Spinner size={28} />
      </div>
    );
  }

  // Lock body scroll while the mobile slide-over is open so the
  // viewport behind the sidebar doesn't bounce / rubber-band.
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (!sidebarOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [sidebarOpen]);

  return (
    <div className="flex h-screen overflow-hidden bg-bg-base">
      <Sidebar
        activeId={activeId}
        open={sidebarOpen}
        collapsed={collapsed}
        onClose={() => setSidebarOpen(false)}
        onToggleCollapsed={toggleCollapsed}
      />

      <main className="relative flex h-full flex-1 flex-col overflow-hidden">
        {/* Top bar — hamburger on both desktop (when collapsed) and
            mobile. safe-top adds notch padding under iOS. The bar
            tops out at ~56px on phones so it doesn't crowd the
            visible viewport. */}
        <div className="safe-top flex shrink-0 items-center gap-3 border-b border-border bg-bg-base/80 px-3 py-2.5 backdrop-blur md:px-4 md:py-2">
          {/* Mobile: open overlay */}
          <button
            type="button"
            aria-label="Open sidebar"
            onClick={() => setSidebarOpen(true)}
            className="focus-ring tap-44 rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary md:hidden"
          >
            <svg width={22} height={22} viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M3 6h18M3 12h18M3 18h18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
          {/* Desktop: expand when collapsed */}
          {collapsed ? (
            <button
              type="button"
              aria-label="Expand sidebar"
              onClick={toggleCollapsed}
              className="focus-ring hidden rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary md:inline-flex"
              title="Expand sidebar"
            >
              <svg width={20} height={20} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M3 6h18M3 12h18M3 18h18"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          ) : null}
          <span className="text-sm font-medium tracking-tight md:hidden">
            SRAN Chat
          </span>
          <OfflineIndicator />
        </div>

        {children}
      </main>
    </div>
  );
}

// ── Offline indicator ─────────────────────────────────────────
// Listens to the browser's online/offline events and pins a small
// "Offline" chip in the header. Cheap, no polling, no extra deps.

function OfflineIndicator() {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sync = () => setOnline(navigator.onLine);
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);
  if (online) return null;
  return (
    <span
      role="status"
      className="ml-auto inline-flex items-center gap-1 rounded-full bg-status-warning/15 px-2 py-0.5 text-[10px] font-medium text-status-warning ring-1 ring-status-warning/30"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-status-warning" aria-hidden />
      Offline
    </span>
  );
}

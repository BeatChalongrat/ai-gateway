"use client";

import { useSelectedLayoutSegments } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Sidebar } from "@/components/chat/Sidebar";
import { Spinner } from "@/components/ui/Spinner";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
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

  // Lock body scroll while the mobile slide-over is open.
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (!sidebarOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [sidebarOpen]);

  // Global keyboard shortcuts: Cmd/Ctrl+Shift+S toggles sidebar,
  // Cmd/Ctrl+K focuses the sidebar search, Cmd/Ctrl+Shift+N starts
  // a new chat. Hook reads the auth-derived ready flag so the keys
  // don't fire on the login redirect screen.
  useKeyboardShortcuts({
    onToggleSidebar: () => {
      // On mobile (md breakpoint not met), toggle the overlay;
      // on desktop, toggle the collapsed state.
      if (typeof window !== "undefined" && window.innerWidth < 768) {
        setSidebarOpen((v) => !v);
      } else {
        toggleCollapsed();
      }
    },
  });

  if (!isReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg-base text-text-secondary">
        <Spinner size={28} />
      </div>
    );
  }

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
        {/* Slim top bar — only render on mobile or when the sidebar
            is collapsed on desktop. The desktop header that lives
            inside each conversation page handles the model dropdown
            + actions. */}
        <div className="safe-top relative flex shrink-0 items-center gap-2 px-3 py-2 md:px-4">
          <button
            type="button"
            aria-label="Open sidebar"
            onClick={() => setSidebarOpen(true)}
            className="focus-ring tap-44 rounded-md p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary md:hidden"
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
          {collapsed ? (
            <button
              type="button"
              aria-label="Expand sidebar"
              onClick={toggleCollapsed}
              className="focus-ring tap-44 hidden rounded-md p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary md:inline-flex"
              title="Expand sidebar (⌘⇧S)"
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
          <OfflineIndicator />
        </div>

        {children}
      </main>
    </div>
  );
}

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

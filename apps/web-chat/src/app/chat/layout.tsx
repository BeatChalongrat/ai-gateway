"use client";

import { useSelectedLayoutSegments } from "next/navigation";
import { useEffect, useLayoutEffect, useState } from "react";

// SSR-safe ``useLayoutEffect``. Next.js pre-renders client
// components on the server where ``useLayoutEffect`` warns
// (DOM isn't available). Falling back to ``useEffect`` on the
// server is the React-team-blessed workaround.
const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

import { Sidebar } from "@/components/chat/Sidebar";
import { Spinner } from "@/components/ui/Spinner";
import {
  useKeyboardShortcuts,
  useNewChatHandler,
} from "@/hooks/useKeyboardShortcuts";
import { useMemoryPendingPoll } from "@/hooks/useMemoryPendingCount";
import { getPortalToken, redirectToPortalLogin } from "@/lib/auth";
import { useAuthStore } from "@/stores/authStore";
import { useModelStore } from "@/stores/modelStore";

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
  const newChat = useNewChatHandler();

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

  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const token = getPortalToken();
    if (!token) {
      redirectToPortalLogin();
      return;
    }
    useAuthStore.getState().checkAuth();
    // Seed the model catalog as soon as we know the user is
    // authenticated. The previous Sidebar component owned this
    // call; the redesign dropped the binding, leaving the model
    // picker permanently empty. Fetching here means the catalog
    // is ready whether the user lands on /chat, /chat/[id], or
    // somewhere else inside this layout.
    if (!useModelStore.getState().loaded) {
      void useModelStore.getState().loadModels();
    }
    setIsReady(true);
  }, []);

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

  // Publish the sidebar's horizontal footprint to ``<html>`` so the
  // ToastViewport (mounted at AppProviders, outside this layout) can
  // bias its ``left: calc(50% + var(--toast-x-offset))`` toward the
  // chat content area's visual center instead of the viewport's.
  // The sidebar contributes ``260px`` at desktop sizes when not
  // collapsed (see Sidebar.tsx's ``md:w-[260px]``); at mobile it's
  // an overlay that doesn't take horizontal space, and when
  // ``collapsed`` it animates to width 0.
  //
  // CSS owns the FIRST-PAINT default in globals.css (130px desktop,
  // 0px below 768px) so the toast never appears off-axis before
  // this effect runs. ``useLayoutEffect`` flushes the collapsed-
  // state override BEFORE the browser paints, so toggling the
  // sidebar doesn't flicker either. Cleanup on unmount so
  // navigating away from /chat leaves the CSS default in charge.
  useIsomorphicLayoutEffect(() => {
    const root = document.documentElement;
    const SIDEBAR_DESKTOP_WIDTH = 260;
    const mq = window.matchMedia("(min-width: 768px)");
    const sync = () => {
      const isDesktop = mq.matches;
      const sidebarWidth = isDesktop && !collapsed ? SIDEBAR_DESKTOP_WIDTH : 0;
      // Half the sidebar width — moves the toast center toward the
      // right by the same amount the sidebar pushed the content
      // area off-axis.
      root.style.setProperty("--toast-x-offset", `${sidebarWidth / 2}px`);
    };
    sync();
    mq.addEventListener("change", sync);
    return () => {
      mq.removeEventListener("change", sync);
      root.style.removeProperty("--toast-x-offset");
    };
  }, [collapsed]);

  // S8 — poll the pending-preference count so the sidebar Memory
  // badge + MemoryIndicator dot have something to read. Single
  // mount per chat session via the layout.
  useMemoryPendingPoll();

  // Global keyboard shortcuts: Cmd/Ctrl+Shift+S toggles sidebar,
  // Cmd/Ctrl+K focuses the sidebar search, Cmd/Ctrl+Shift+N starts
  // a new chat.
  useKeyboardShortcuts({
    onToggleSidebar: () => {
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
        {/* Top bar. Three modes:
            • mobile: hamburger to open the slide-over
            • desktop expanded: just the offline pill on the right
            • desktop collapsed: floating hamburger + new-chat rail
              at the top-left (ChatGPT-style, see CollapsedRail) */}
        <div className="safe-top relative flex shrink-0 items-center gap-2 px-3 py-2 md:px-4">
          <button
            type="button"
            aria-label="Open sidebar"
            onClick={() => setSidebarOpen(true)}
            className="focus-ring tap-44 rounded-md p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary md:hidden"
          >
            <HamburgerIcon />
          </button>
          {collapsed ? (
            <CollapsedRail
              onExpand={toggleCollapsed}
              onNewChat={() => void newChat()}
            />
          ) : null}
          <OfflineIndicator />
        </div>

        {children}
      </main>
    </div>
  );
}

// ─── Collapsed-sidebar rail ──────────────────────────────────
// Renders only on desktop (md+) when the sidebar is collapsed.
// Sits inside the same top-bar flex row as the offline pill, so
// the row keeps its safe-top + height invariants. Buttons mirror
// ChatGPT's: a hamburger to reopen the sidebar and a pencil/edit
// icon that spawns or reuses an empty conversation via the same
// dedup-aware handler the sidebar's "+ New chat" uses.

function CollapsedRail({
  onExpand,
  onNewChat,
}: {
  onExpand: () => void;
  onNewChat: () => void;
}) {
  return (
    <div className="hidden items-center gap-1 md:flex">
      <button
        type="button"
        aria-label="Expand sidebar"
        title="Expand sidebar (⌘⇧S)"
        onClick={onExpand}
        className="focus-ring tap-44 rounded-md p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        <HamburgerIcon />
      </button>
      <button
        type="button"
        aria-label="New chat"
        title="New chat (⌘⇧N)"
        onClick={onNewChat}
        className="focus-ring tap-44 rounded-md p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        <NewChatIcon />
      </button>
    </div>
  );
}

function HamburgerIcon() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 6h18M3 12h18M3 18h18"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function NewChatIcon() {
  // Pencil-in-square — the ChatGPT-equivalent "compose new" mark.
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M15.5 3.5a2.12 2.12 0 1 1 3 3L7 18l-4 1 1-4 11.5-11.5Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M14 4l6 6"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
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

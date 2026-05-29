"use client";

import {
  usePathname,
  useRouter,
  useSelectedLayoutSegments,
} from "next/navigation";
import { useEffect, useState } from "react";
import { BookOpen, Brain, MoreHorizontal, Plus } from "lucide-react";

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
import { useSidebarStore } from "@/stores/sidebarStore";

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const segments = useSelectedLayoutSegments();
  const activeId = segments?.[0] ?? null;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // ``collapsed`` lives in a shared Zustand store so other UI
  // (notably the Toast component, which biases its horizontal
  // position by the sidebar width) can read it synchronously
  // during render. The store handles its own localStorage
  // persistence — no useEffect-driven hydration in this layout.
  const collapsed = useSidebarStore((s) => s.collapsed);
  const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed);
  const newChat = useNewChatHandler();

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

  // Toast positioning offset is no longer wired here — the Toast
  // component reads ``collapsed`` from ``useSidebarStore`` directly
  // and computes its own offset synchronously during render. See
  // Toast.tsx for the inline computation. Removing the CSS-variable
  // round-trip eliminates the one-frame flicker that the prior
  // useLayoutEffect-based publication could not avoid on first paint.

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
          {/* Mobile-only right actions (Claude-style): quick new-chat +
              an overflow menu. Hidden on desktop, where the sidebar
              owns these actions. */}
          <MobileTopActions
            onNewChat={() => {
              setSidebarOpen(false);
              void newChat();
            }}
          />
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

// ─── Mobile top-bar actions ──────────────────────────────────
// Right-aligned, mobile-only (md:hidden) group mirroring Claude's
// mobile chrome: a prominent "+" that starts a new chat (reusing the
// same dedup-aware handler the sidebar uses) and a "⋯" overflow menu
// linking to the routes the sidebar footer also exposes (Memory,
// Knowledge). Desktop is untouched — the sidebar owns these there.
//
// Both buttons share a subtle, integrated circle style (Claude-like):
// a faint surface fill + a hairline ring, NOT a bright CTA. Rendered
// with theme tokens rather than literal white/15 + text-white/90: the
// chat app has a reachable light theme (themeStore / [data-theme=
// "light"]) where white-translucent fills + white icons would vanish
// on the white top bar. In dark theme --border-default already IS
// rgba(255,255,255,0.12) (the translucent ring the design calls for);
// in light it's rgba(0,0,0,0.10) — so border-border + bg-bg-surface +
// text-text-primary reproduce the subtle look in BOTH themes.
const SUBTLE_TOPBAR_BTN =
  "focus-ring tap-44 flex h-9 w-9 items-center justify-center rounded-full " +
  "border border-border bg-bg-surface text-text-primary transition-colors " +
  "hover:bg-bg-elevated";

const MORE_MENU_ITEMS: { label: string; Icon: typeof Brain; href: string }[] = [
  { label: "Memory", Icon: Brain, href: "/chat/memory" },
  { label: "Knowledge", Icon: BookOpen, href: "/chat/knowledge" },
];

function MobileTopActions({ onNewChat }: { onNewChat: () => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  // Dismiss the menu on any navigation so it never lingers over a new
  // route after a menu item (or anything else) changes the path.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  return (
    <div className="relative ml-auto flex items-center gap-1 md:hidden">
      <button
        type="button"
        aria-label="New chat"
        title="New chat"
        onClick={onNewChat}
        className={SUBTLE_TOPBAR_BTN}
      >
        <Plus size={18} aria-hidden />
      </button>

      <button
        type="button"
        aria-label="More options"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((v) => !v)}
        className={SUBTLE_TOPBAR_BTN}
      >
        <MoreHorizontal size={18} aria-hidden />
      </button>

      {menuOpen ? (
        <>
          {/* Backdrop — tap anywhere to dismiss. */}
          <div
            className="fixed inset-0 z-40"
            aria-hidden
            onClick={() => setMenuOpen(false)}
          />
          <div
            role="menu"
            aria-label="More options"
            className="absolute right-0 top-12 z-50 w-48 overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-xl"
          >
            {MORE_MENU_ITEMS.map(({ label, Icon, href }) => (
              <button
                key={label}
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  router.push(href);
                }}
                className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
              >
                <Icon size={16} aria-hidden />
                {label}
              </button>
            ))}
          </div>
        </>
      ) : null}
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

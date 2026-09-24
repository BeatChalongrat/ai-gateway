"use client";

import {
  usePathname,
  useRouter,
  useSelectedLayoutSegments,
} from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Brain, MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { useChatDeletionText } from "@/hooks/useDataPolicy";
import { DELETION_NOTICE_KEYS } from "@/lib/dataPolicy";
import { useHostedMode } from "@/hooks/useHostedMode";
import { useKnowledgeAvailable } from "@/hooks/useKnowledgeAvailable";
import { useChatStore } from "@/stores/chatStore";
import { CreditBadge } from "@/components/chat/CreditBadge";
import { ShortcutsDialog } from "@/components/chat/ShortcutsDialog";
import { Sidebar } from "@/components/chat/Sidebar";
import { ModeResetBanner } from "@/components/chat/ModeResetBanner";
import { TurnNotSavedBanner } from "@/components/chat/TurnNotSavedBanner";
import { UpdateBanner } from "@/components/chat/UpdateBanner";
import { Spinner } from "@/components/ui/Spinner";
import { SessionUnreachable } from "@/components/ui/SessionUnreachable";
import {
  useKeyboardShortcuts,
  useNewChatHandler,
} from "@/hooks/useKeyboardShortcuts";
import { useMemoryPendingPoll } from "@/hooks/useMemoryPendingCount";
import { useShortcutHint } from "@/hooks/useShortcutHint";
import { redirectToPortalLogin } from "@/lib/auth";
import { useT } from "@/lib/i18n";
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
  const t = useT();

  // Route-change focus management (WCAG 2.4.3): a client-side
  // navigation leaves focus wherever the click happened (usually a
  // sidebar button), so screen-reader/keyboard users get no signal
  // the page changed. Moving focus to the labelled <main> both
  // repositions the reading cursor and announces the page name.
  // Skipped on first mount — stealing initial page focus is worse.
  const pathname = usePathname();
  const mainRef = useRef<HTMLElement>(null);
  const firstRouteRef = useRef(true);
  useEffect(() => {
    if (firstRouteRef.current) {
      firstRouteRef.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [pathname]);
  const pageLabel = pathname?.startsWith("/chat/knowledge")
    ? t("menu.knowledgeBase")
    : pathname?.startsWith("/chat/projects")
      ? t("menu.projects")
      : pathname?.startsWith("/chat/memory")
        ? t("a11y.memorySettings")
        : pathname?.startsWith("/chat/archive")
          ? t("archive.open")
          : t("a11y.pageChat");

  const [isReady, setIsReady] = useState(false);
  // FIX130 C3.1 — the session check got no trustworthy answer: a retry
  // screen, never the login bounce (which revokes the tokens).
  const [unreachable, setUnreachable] = useState(false);
  const [authAttempt, setAuthAttempt] = useState(0);
  const [authChecking, setAuthChecking] = useState(false);
  const retryAuth = useCallback(() => setAuthAttempt((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    // Cookie-based auth: we can't read the token in JS, so resolve the
    // session via /portal/me (cookie-authenticated). A session the gateway
    // refused → bounce to the Portal login.
    void (async () => {
      setAuthChecking(true);
      await useAuthStore.getState().checkAuth();
      if (!alive) return;
      setAuthChecking(false);
      const { user, status } = useAuthStore.getState();
      if (!user) {
        if (status === "rejected") redirectToPortalLogin();
        else setUnreachable(true);
        return;
      }
      setUnreachable(false);
      // Seed the model catalog now that we know the user is
      // authenticated, so the picker isn't empty wherever they land
      // inside this layout.
      if (!useModelStore.getState().loaded) {
        void useModelStore.getState().loadModels();
      }
      setIsReady(true);
    })();
    return () => {
      alive = false;
    };
  }, [authAttempt]);

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

  // Phase 10 — the cheatsheet is mounted here (layout level) so the
  // global ? / Cmd+/ binding works on every chat route, including ones
  // with no composer.
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // FIX34 Part D — Cmd/Ctrl+Shift+Backspace asks first (a shortcut must never
  // delete silently); Cmd/Ctrl+, opens settings (desktop) or the memory &
  // preferences page (web has no settings screen).
  const [deleteOpen, setDeleteOpen] = useState(false);
  const router = useRouter();
  const hosted = useHostedMode();
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  // FIX132 C — the confirmation states the real period; asked only once
  // the dialog opens.
  const deleteBody = useChatDeletionText(t, DELETION_NOTICE_KEYS.shortcutDialog, deleteOpen);
  async function confirmDelete() {
    const id = useChatStore.getState().activeConversationId;
    setDeleteOpen(false);
    if (!id) return;
    try {
      await deleteConversation(id);
      router.replace("/chat");
    } catch {
      /* the store surfaces the error banner */
    }
  }

  // Global keyboard shortcuts (hooks/useKeyboardShortcuts.ts lists them all;
  // ShortcutsDialog mirrors the list for the user).
  useKeyboardShortcuts({
    onShowShortcuts: () => setShortcutsOpen(true),
    onToggleSidebar: () => {
      if (typeof window !== "undefined" && window.innerWidth < 768) {
        setSidebarOpen((v) => !v);
      } else {
        toggleCollapsed();
      }
    },
    onDeleteConversation: () => setDeleteOpen(true),
    // FIX43 Part B4 — the web has a Settings page now; Ctrl+, goes there on
    // both surfaces (OPEN_DECISIONS #23 closed).
    onOpenSettings: () => router.push("/chat/settings"),
    // FIX84 row 1 — Ctrl+K from anywhere: bring the sidebar on screen (the
    // mobile drawer, or the collapsed rail), then the hook asks it to expand
    // its search row.
    onSearchConversations: () => {
      if (typeof window !== "undefined" && window.innerWidth < 768) {
        setSidebarOpen(true);
      } else if (useSidebarStore.getState().collapsed) {
        toggleCollapsed();
      }
    },
  });

  if (!isReady && unreachable) {
    return <SessionUnreachable onRetry={retryAuth} checking={authChecking} />;
  }
  if (!isReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg-base text-text-secondary">
        <Spinner size={28} />
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-bg-base">
      {/* FIX128 D4 — the first Tab stop: jump straight to the composer. */}
      <a
        href="#composer"
        data-testid="skip-to-composer"
        onClick={(e) => {
          const ta = document.querySelector<HTMLTextAreaElement>('textarea[data-shortcut="composer"]');
          if (!ta) return;
          e.preventDefault();
          ta.focus();
        }}
        className="focus-ring sr-only z-50 rounded-md bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        {t("a11y.skipToComposer")}
      </a>
      <Sidebar
        activeId={activeId}
        open={sidebarOpen}
        collapsed={collapsed}
        onClose={() => setSidebarOpen(false)}
        onToggleCollapsed={toggleCollapsed}
      />

      <main
        ref={mainRef}
        tabIndex={-1}
        aria-label={pageLabel}
        className="relative flex h-full flex-1 flex-col overflow-hidden outline-none"
      >
        {/* Top bar. Three modes:
            • mobile: hamburger to open the slide-over
            • desktop expanded: just the offline pill on the right
            • desktop collapsed: floating hamburger + new-chat rail
              at the top-left (ChatGPT-style, see CollapsedRail) */}
        <div className="safe-top relative flex shrink-0 items-center gap-2 px-3 py-2 md:px-4">
          <button
            type="button"
            aria-label={t("a11y.openSidebar")}
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

        {/* FIX22 §2 — desktop update notice (hosted mode only; renders
            nothing in a plain browser or when nothing is published). */}
        <UpdateBanner />
        {/* FIX25 §1c.3 — "permissions were reset from Never-asks" (desktop, once per launch). */}
        <ModeResetBanner />
        {/* FIX121 C4 — "this turn was not saved to the server" (desktop only). */}
        <TurnNotSavedBanner />

        {children}

        {/* Floating usage toggle (mini ring pill) — bottom-right of
            the chat area, above the composer. Positions itself
            absolutely against this relative <main>; the panel opens
            upward from the pill. */}
        <CreditBadge />
      </main>

      <ShortcutsDialog
        open={shortcutsOpen}
        onClose={() => setShortcutsOpen(false)}
      />
      <Modal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title={t("shortcuts.deleteConfirm.title")}
        description={deleteBody}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeleteOpen(false)} data-testid="delete-cancel">
              {t("common.cancel")}
            </Button>
            <Button variant="danger" onClick={() => void confirmDelete()} data-testid="delete-confirm">
              {t("common.delete")}
            </Button>
          </div>
        }
      >
        <span />
      </Modal>
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
  const t = useT();
  const hint = useShortcutHint();
  return (
    <div className="hidden items-center gap-1 md:flex">
      <button
        type="button"
        aria-label={t("a11y.expandSidebar")}
        title={`${t("a11y.expandSidebar")} (${hint("Mod+Shift+S")})`}
        onClick={onExpand}
        className="focus-ring tap-44 rounded-md p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        <HamburgerIcon />
      </button>
      <button
        type="button"
        aria-label={t("sidebar.newChat")}
        title={`${t("sidebar.newChat")} (${hint("Mod+Shift+N")})`}
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
// Both buttons are ghost icon buttons (Claude-like): no fill, no
// border, just the icon with a faint hover highlight. Theme tokens
// rather than literal text-white/70 + hover:bg-white/10 — the chat app
// has a reachable light theme (themeStore / [data-theme="light"]) where
// a white icon would vanish on the white top bar. text-text-secondary→
// primary + hover:bg-bg-elevated mirrors the hamburger button: looks
// identical in dark, stays visible in light.
const SUBTLE_TOPBAR_BTN =
  "focus-ring tap-44 flex h-9 w-9 items-center justify-center rounded-xl " +
  "text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary";

// FIX130 C2 — labels through the dictionary (they were English on a Thai
// screen); the knowledge entry only when a knowledge base can work.
const MORE_MENU_ITEMS: { labelKey: "menu.memory" | "menu.knowledgeBase"; Icon: typeof Brain; href: string; needsKB?: boolean }[] = [
  { labelKey: "menu.memory", Icon: Brain, href: "/chat/memory" },
  { labelKey: "menu.knowledgeBase", Icon: BookOpen, href: "/chat/knowledge", needsKB: true },
];

function MobileTopActions({ onNewChat }: { onNewChat: () => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const t = useT();
  const [menuOpen, setMenuOpen] = useState(false);
  const kbAvailable = useKnowledgeAvailable();

  // Dismiss the menu on any navigation so it never lingers over a new
  // route after a menu item (or anything else) changes the path.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  return (
    <div className="relative ml-auto flex items-center gap-1 md:hidden">
      <button
        type="button"
        aria-label={t("sidebar.newChat")}
        title="New chat"
        onClick={onNewChat}
        className={SUBTLE_TOPBAR_BTN}
      >
        <ChatBubblePlusIcon />
      </button>

      <button
        type="button"
        aria-label={t("a11y.moreOptions")}
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
            aria-label={t("a11y.moreOptions")}
            className="absolute right-0 top-12 z-50 w-48 overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-xl"
          >
            {MORE_MENU_ITEMS.filter((item) => !item.needsKB || kbAvailable).map(({ labelKey, Icon, href }) => (
              <button
                key={href}
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  router.push(href);
                }}
                className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
              >
                <Icon size={16} aria-hidden />
                {t(labelKey)}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

function ChatBubblePlusIcon() {
  // Chat-bubble-with-plus — the "compose a new conversation" mark.
  // currentColor so it inherits the button's text-secondary→primary.
  return (
    <svg width={40} height={40} viewBox="0 0 36 36" fill="none" aria-hidden>
      <path
        d="M10 12C10 10.3 11.3 9 13 9H23C24.7 9 26 10.3 26 12V19C26 20.7 24.7 22 23 22H20L16 26V22H13C11.3 22 10 20.7 10 19V12Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <line x1="18" y1="13" x2="18" y2="18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <line x1="15.5" y1="15.5" x2="20.5" y2="15.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
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

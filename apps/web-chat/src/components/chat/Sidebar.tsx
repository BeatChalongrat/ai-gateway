"use client";

// Sidebar — ChatGPT/Claude-style minimal:
//
//   • Header: "SRAN Chat" wordmark + collapse button (desktop)
//   • Top actions: + New chat, Search chats (both subtle outlined)
//   • Conversation list grouped by time, plain-text items
//   • Footer: ONE user row that opens a popup menu (theme toggle,
//     KB, voice settings, custom instructions, logout). Mirrors
//     ChatGPT's footer — a single tap target, no icon-grid.
//
// Below 80 rows we render plain divs; above that we virtualize via
// @tanstack/react-virtual. Server-side search is debounced 300ms.

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";
import { useNewChatHandler } from "@/hooks/useKeyboardShortcuts";
import { clearPortalToken } from "@/lib/auth";
import { useI18nStore, useT } from "@/lib/i18n";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useThemeStore } from "@/stores/themeStore";
import { ConversationListSkeleton } from "@/components/ui/Skeleton";
import { Brain, BrainCircuit } from "lucide-react";

import { ConversationItem } from "./ConversationItem";
import { CustomInstructionsModal } from "./CustomInstructionsModal";
import { VoiceSettingsModal } from "./VoiceSettingsModal";

const PORTAL_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PORTAL_URL) ||
  "http://localhost:3001";

const VIRTUALIZE_THRESHOLD = 80;
const LOAD_MORE_THRESHOLD_PX = 200;

interface Props {
  activeId: string | null;
  open: boolean;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapsed: () => void;
}

type Row =
  | { kind: "header"; label: string; key: string }
  | { kind: "conv"; conv: Conversation; key: string };

export function Sidebar({
  activeId,
  open,
  collapsed,
  onClose,
  onToggleCollapsed,
}: Props) {
  const router = useRouter();
  const { user } = useAuth();
  const conversations = useChatStore((s) => s.conversations);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const loadMoreConversations = useChatStore((s) => s.loadMoreConversations);
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const hasMoreConversations = useChatStore((s) => s.hasMoreConversations);
  const loadingMoreConversations = useChatStore((s) => s.loadingMoreConversations);
  const searchResults = useChatStore((s) => s.conversationsSearchResults);
  const searchActive = useChatStore((s) => s.conversationsSearchActive);
  const searchLoading = useChatStore((s) => s.conversationsSearchLoading);
  const runSearch = useChatStore((s) => s.searchConversations);
  const clearSearch = useChatStore((s) => s.clearConversationsSearch);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const messagesByConv = useChatStore((s) => s.messages);
  const newChat = useNewChatHandler();

  const [search, setSearch] = useState("");
  const [searchExpanded, setSearchExpanded] = useState(false);
  const [instructionsOpen, setInstructionsOpen] = useState(false);
  const [voiceSettingsOpen, setVoiceSettingsOpen] = useState(false);
  const debouncedSearch = useDebounce(search, 300);
  const t = useT();

  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
  }, [conversationsLoaded, loadConversations]);

  useEffect(() => {
    const q = debouncedSearch.trim();
    if (q) {
      void runSearch(q);
    } else {
      clearSearch();
    }
  }, [debouncedSearch, runSearch, clearSearch]);

  const rows = useMemo<Row[]>(() => {
    if (searchActive) {
      return searchResults.map((c) => ({
        kind: "conv" as const,
        conv: c,
        key: c.id,
      }));
    }
    // Hide conversations with no messages — they're either legacy
    // empties from before the lazy-create rollout or transient rows
    // that never received a first send. The active conversation
    // gets a pass: during the first send its server-side
    // message_count is still 0 until the assistant turn persists,
    // and its local optimistic messages prove the user is mid-send.
    const filtered = conversations.filter((c) => {
      if (c.message_count > 0) return true;
      if (c.id !== activeConversationId) return false;
      return (messagesByConv[c.id]?.length ?? 0) > 0;
    });
    const sorted = filtered.sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );
    const groups = groupByTime(sorted);
    const out: Row[] = [];
    for (const g of groups) {
      if (g.items.length === 0) continue;
      out.push({ kind: "header", label: g.label, key: `h-${g.label}` });
      for (const c of g.items) {
        out.push({ kind: "conv", conv: c, key: c.id });
      }
    }
    return out;
  }, [searchActive, searchResults, conversations, activeConversationId, messagesByConv]);

  function handleNew() {
    newChat();
    onClose();
  }

  function handleTemporaryChat() {
    // Set the store flag the next createConversation() consumes;
    // navigation to /chat lazy-creates on the first send, so the
    // PATCH to memory_enabled=false runs at create-time before the
    // first message turn.
    useChatStore.getState().setNextConversationTemporary(true);
    newChat();
    onClose();
  }

  function handleOpenMemory() {
    router.push("/chat/memory");
    onClose();
  }

  function handleLogout() {
    clearPortalToken();
    window.location.href = `${PORTAL_URL}/login`;
  }

  const isHidden = collapsed;
  const userDisplay =
    user?.email || (user?.user_id ? user.user_id.slice(0, 8) : "Guest");
  const userInitial = (userDisplay || "?").charAt(0).toUpperCase();

  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={`fixed inset-0 z-30 bg-black/50 backdrop-blur-sm transition-opacity duration-200 md:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      <aside
        className={`safe-top safe-bottom fixed left-0 top-0 z-40 flex h-full w-[85vw] max-w-[320px] flex-col border-r border-border-subtle bg-bg-sidebar transition-all duration-200 ease-out md:static md:w-[260px] md:max-w-none ${
          open ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0 ${
          isHidden
            ? "md:pointer-events-none md:w-0 md:-translate-x-full md:overflow-hidden md:opacity-0"
            : ""
        }`}
      >
        {/* Header — SRAN wordmark + collapse toggle. We replaced the
            cat-bubble PNG with text because the bitmap mark read as
            chrome rather than a brand at the sidebar's 260px width.
            ``.font-brand`` is the Outfit-family alias defined in
            globals.css; falls back to the system sans-serif so a
            font-load failure still ships a readable label. */}
        <div
          className="flex items-center justify-between gap-2 px-3"
          style={{ height: 48 }}
        >
          <span
            aria-label="SRAN Chat"
            className="font-brand text-xl tracking-tight text-text-primary"
          >
            {/* ChatGPT-style weight split: bold "SRAN" + lighter
                "Chat" gives the wordmark a brand/product hierarchy
                without changing typeface or size. */}
            <span className="font-bold">SRAN</span>
            <span className="font-normal">Chat</span>
          </span>
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label="Collapse sidebar (⌘⇧S)"
            title="Collapse sidebar (⌘⇧S)"
            className="focus-ring tap-44 hidden rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary md:inline-flex"
          >
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M15 6l-6 6 6 6"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>

        {/* New chat + search row */}
        <div className="flex flex-col gap-1 px-2 pt-1">
          <button
            type="button"
            onClick={handleNew}
            className="focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg border border-border-subtle bg-transparent px-3 py-2 text-sm text-text-primary transition-colors hover:bg-bg-elevated"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M12 5v14M5 12h14"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
            <span>{t("sidebar.newChat")}</span>
            <span className="ml-auto hidden text-[10px] text-text-muted md:inline">
              ⌘⇧N
            </span>
          </button>

          {searchExpanded || search ? (
            <div className="relative">
              <svg
                width={14}
                height={14}
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted"
              >
                <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
                <path
                  d="M21 21l-4.3-4.3"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
              <input
                type="text"
                value={search}
                data-shortcut="conv-search"
                autoFocus
                onChange={(e) => setSearch(e.target.value)}
                onBlur={() => {
                  if (!search) setSearchExpanded(false);
                }}
                placeholder={`${t("sidebar.searchChats")}…`}
                className="w-full rounded-lg border border-border-subtle bg-transparent py-1.5 pl-8 pr-2 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none"
              />
              {search ? (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => {
                    setSearch("");
                    setSearchExpanded(false);
                  }}
                  className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
                >
                  <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
                    <path
                      d="M6 6l12 12M18 6l-12 12"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
              ) : null}
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setSearchExpanded(true)}
              className="focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
            >
              <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
                <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
                <path d="M21 21l-4.3-4.3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <span>{t("sidebar.searchChats")}</span>
              <span className="ml-auto hidden text-[10px] text-text-muted md:inline">
                ⌘K
              </span>
            </button>
          )}
        </div>

        {/* Conversation list */}
        <ConversationsArea
          rows={rows}
          conversationsLoaded={conversationsLoaded}
          searchActive={searchActive}
          searchLoading={searchLoading}
          hasMore={hasMoreConversations && !searchActive}
          loadingMore={loadingMoreConversations}
          onLoadMore={() => void loadMoreConversations()}
          activeId={activeId}
          onSelect={(id) => {
            router.push(`/chat/${id}`);
            onClose();
          }}
          onRename={(id, t) => renameConversation(id, t)}
          onDelete={async (id) => {
            await deleteConversation(id);
            if (id === activeId) router.replace("/chat");
          }}
        />

        {/* Footer — single user row that opens the popup menu. */}
        <UserMenu
          userDisplay={userDisplay}
          userInitial={userInitial}
          onKnowledgeBase={() => router.push("/chat/knowledge")}
          onVoiceSettings={() => setVoiceSettingsOpen(true)}
          onCustomInstructions={() => setInstructionsOpen(true)}
          onMemory={handleOpenMemory}
          onTemporaryChat={handleTemporaryChat}
          onLogout={handleLogout}
        />
      </aside>
      <CustomInstructionsModal
        open={instructionsOpen}
        onClose={() => setInstructionsOpen(false)}
      />
      <VoiceSettingsModal
        open={voiceSettingsOpen}
        onClose={() => setVoiceSettingsOpen(false)}
      />
    </>
  );
}

// UserMenu — ChatGPT-style sidebar footer. One row, one tap target;
// every secondary action (theme, KB, voice, custom instructions,
// logout) lives inside the popup that opens ABOVE the row.
//
// The popup uses position:absolute against the same wrapper so it
// inherits the sidebar width. Outside-click + Escape both close it.
function UserMenu({
  userDisplay,
  userInitial,
  onKnowledgeBase,
  onVoiceSettings,
  onCustomInstructions,
  onMemory,
  onTemporaryChat,
  onLogout,
}: {
  userDisplay: string;
  userInitial: string;
  onKnowledgeBase: () => void;
  onVoiceSettings: () => void;
  onCustomInstructions: () => void;
  onMemory: () => void;
  onTemporaryChat: () => void;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggle);
  const isDark = theme === "dark";
  const locale = useI18nStore((s) => s.locale);
  const toggleLocale = useI18nStore((s) => s.toggle);
  const t = useT();

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", off);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative border-t border-border-subtle">
      {open ? (
        <div
          role="menu"
          aria-label="Account menu"
          className="popover-appear absolute inset-x-2 bottom-full z-40 mb-1 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-2xl"
        >
          <MenuItem
            onClick={toggleTheme}
            icon={<ThemeIcon isDark={isDark} />}
            label={isDark ? t("menu.theme.dark") : t("menu.theme.light")}
            trailing={<ThemePill isDark={isDark} />}
            closeOnClick={false}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={toggleLocale}
            icon={<GlobeIcon />}
            label={t("menu.language")}
            trailing={<LocalePill locale={locale} />}
            closeOnClick={false}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={onKnowledgeBase}
            icon={<BookIcon />}
            label={t("menu.knowledgeBase")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={onVoiceSettings}
            icon={<MicIcon />}
            label={t("menu.voiceSettings")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={onCustomInstructions}
            icon={<GearIcon />}
            label={t("menu.customInstructions")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={onMemory}
            icon={<Brain width={14} height={14} aria-hidden />}
            label="Memory"
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={onTemporaryChat}
            icon={<BrainCircuit width={14} height={14} aria-hidden />}
            label="Temporary chat"
            setOpen={setOpen}
          />
          <div className="border-t border-border/60" />
          <MenuItem
            onClick={onLogout}
            icon={<LogoutIcon />}
            label={t("menu.logout")}
            danger
            setOpen={setOpen}
          />
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 pt-2 text-left transition-colors hover:bg-bg-elevated"
        // ``safe-bottom`` alone resolves to 0 on desktop, leaving the
        // user row flush against the viewport edge. Stack a real
        // 1rem gap on top of the safe-area inset so the row breathes
        // on every device — desktop AND iOS home-indicator.
        style={{
          minHeight: 48,
          paddingBottom: "calc(1rem + var(--sab, 0px))",
        }}
      >
        <span
          aria-hidden
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white"
          style={{ background: "var(--bg-elevated, #3a3a3a)" }}
        >
          {userInitial}
        </span>
        <span
          className="min-w-0 flex-1 truncate text-[13px] font-medium text-text-primary"
          title={userDisplay}
        >
          {userDisplay}
        </span>
        <span aria-hidden className="shrink-0 text-text-muted">
          <DotsIcon />
        </span>
      </button>
    </div>
  );
}

function MenuItem({
  onClick,
  icon,
  label,
  trailing,
  danger,
  closeOnClick = true,
  setOpen,
}: {
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  trailing?: React.ReactNode;
  danger?: boolean;
  closeOnClick?: boolean;
  setOpen: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={() => {
        onClick();
        if (closeOnClick) setOpen(false);
      }}
      className={`flex w-full items-center gap-2.5 px-3 text-left text-[13px] transition-colors hover:bg-bg-elevated ${
        danger
          ? "text-status-danger hover:text-status-danger"
          : "text-text-primary"
      }`}
      style={{ height: 40 }}
    >
      <span
        aria-hidden
        className={`inline-flex h-4 w-4 shrink-0 items-center justify-center ${
          danger ? "text-status-danger" : "text-text-secondary"
        }`}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {trailing ? (
        <span aria-hidden className="shrink-0 text-text-muted">
          {trailing}
        </span>
      ) : null}
    </button>
  );
}

// ── Icons ────────────────────────────────────────────────────

function DotsIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <circle cx="5" cy="12" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="19" cy="12" r="1.6" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M9 6l6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ThemeIcon({ isDark }: { isDark: boolean }) {
  return isDark ? (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ) : (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ThemePill({ isDark }: { isDark: boolean }) {
  // Read-only pill that mirrors the current theme so the user sees
  // their state at a glance. Clicking the row toggles; the pill
  // animates the knob across.
  return (
    <span
      aria-hidden
      className="inline-flex h-4 w-7 items-center rounded-full bg-border-subtle"
    >
      <span
        className="inline-block h-3 w-3 transform rounded-full bg-text-secondary transition-transform"
        style={{ transform: isDark ? "translateX(14px)" : "translateX(2px)" }}
      />
    </span>
  );
}

function GlobeIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M3 12h18M12 3a13 13 0 0 1 0 18M12 3a13 13 0 0 0 0 18"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

// LocalePill — read-only chip showing the active locale code so the
// user sees their state in the menu row at a glance. Clicking the
// row toggles via the store; this is just the display.
function LocalePill({ locale }: { locale: "en" | "th" }) {
  return (
    <span
      aria-hidden
      className="inline-flex h-5 items-center rounded-full border border-border bg-bg-base px-2 text-[10px] font-semibold uppercase tracking-wider text-text-secondary"
    >
      {locale}
    </span>
  );
}

function BookIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MicIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="9" y="3" width="6" height="11" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M5 11a7 7 0 0 0 14 0M12 18v3"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06A2 2 0 0 1 4.21 16.97l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9 1.65 1.65 0 0 0 4.27 7.18l-.06-.06A2 2 0 1 1 7.03 4.29l.06.06A1.65 1.65 0 0 0 8.91 4.6 1.65 1.65 0 0 0 9.91 3.09V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M16 17l5-5-5-5M21 12H9M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ─── Conversations area (virtualized + lazy-load) ─────────────

interface AreaProps {
  rows: Row[];
  conversationsLoaded: boolean;
  searchActive: boolean;
  searchLoading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  activeId: string | null;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
}

function ConversationsArea({
  rows,
  conversationsLoaded,
  searchActive,
  searchLoading,
  hasMore,
  loadingMore,
  onLoadMore,
  activeId,
  onSelect,
  onRename,
  onDelete,
}: AreaProps) {
  const ref = useRef<HTMLDivElement>(null);
  const useVirtual = rows.length >= VIRTUALIZE_THRESHOLD;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => (useVirtual ? ref.current : null),
    estimateSize: (i) => (rows[i]?.kind === "header" ? 24 : 36),
    overscan: 8,
    measureElement(el) {
      return el?.getBoundingClientRect().height ?? 0;
    },
  });

  function onScroll(e: React.UIEvent<HTMLDivElement>) {
    if (!hasMore || loadingMore) return;
    const el = e.currentTarget;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distance < LOAD_MORE_THRESHOLD_PX) {
      onLoadMore();
    }
  }

  if (!conversationsLoaded) {
    return (
      <nav className="scrollbar-thin mt-3 flex-1 overflow-y-auto px-2 pb-2">
        <ConversationListSkeleton rows={5} />
      </nav>
    );
  }

  if (rows.length === 0) {
    return (
      <nav className="scrollbar-thin mt-3 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-3 py-4 text-xs text-text-muted">
          {searchActive
            ? searchLoading
              ? "Searching…"
              : "No conversations found"
            : "No conversations yet"}
        </div>
      </nav>
    );
  }

  return (
    <nav
      ref={ref}
      onScroll={onScroll}
      className="scrollbar-thin mt-3 flex-1 overflow-y-auto px-2 pb-2"
    >
      {useVirtual ? (
        <div
          style={{
            position: "relative",
            height: virtualizer.getTotalSize(),
            width: "100%",
          }}
        >
          {virtualizer.getVirtualItems().map((vi) => {
            const row = rows[vi.index];
            return (
              <div
                key={row.key}
                data-index={vi.index}
                ref={virtualizer.measureElement}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${vi.start}px)`,
                }}
              >
                <SidebarRow
                  row={row}
                  active={row.kind === "conv" ? row.conv.id === activeId : false}
                  onSelect={onSelect}
                  onRename={onRename}
                  onDelete={onDelete}
                />
              </div>
            );
          })}
        </div>
      ) : (
        <div className="flex flex-col">
          {rows.map((row) => (
            <SidebarRow
              key={row.key}
              row={row}
              active={row.kind === "conv" ? row.conv.id === activeId : false}
              onSelect={onSelect}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
      {hasMore ? (
        <div className="flex justify-center py-2 text-[10px] text-text-muted">
          {loadingMore ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
              Loading more…
            </span>
          ) : (
            <button
              type="button"
              onClick={onLoadMore}
              className="focus-ring rounded px-2 py-1 hover:text-text-primary"
            >
              Load more
            </button>
          )}
        </div>
      ) : null}
    </nav>
  );
}

function SidebarRow({
  row,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  row: Row;
  active: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
}) {
  const t = useT();
  if (row.kind === "header") {
    return (
      <div className="px-3 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {/* ``row.label`` is a translation key like ``sidebar.group.today``;
            t() looks it up against the active locale dictionary. */}
        {t(row.label as Parameters<typeof t>[0])}
      </div>
    );
  }
  return (
    <ConversationItem
      conv={row.conv}
      active={active}
      onSelect={() => onSelect(row.conv.id)}
      onRename={(t) => onRename(row.conv.id, t)}
      onDelete={() => onDelete(row.conv.id)}
    />
  );
}

interface Group {
  label: string;
  items: Conversation[];
}

function groupByTime(items: Conversation[]): Group[] {
  const now = new Date();
  const startOfDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = startOfDay(now);
  const yesterday = today - 24 * 60 * 60 * 1000;
  const sevenDays = today - 7 * 24 * 60 * 60 * 1000;
  const thirtyDays = today - 30 * 24 * 60 * 60 * 1000;

  // ``label`` holds a translation-key, not literal copy. The
  // sidebar's row renderer calls ``t()`` on it just before painting
  // so the same Row objects render in either locale without
  // re-grouping when the user flips languages.
  const groups: Group[] = [
    { label: "sidebar.group.today", items: [] },
    { label: "sidebar.group.yesterday", items: [] },
    { label: "sidebar.group.last7", items: [] },
    { label: "sidebar.group.last30", items: [] },
    { label: "sidebar.group.older", items: [] },
  ];

  for (const c of items) {
    const t = new Date(c.updated_at).getTime();
    if (t >= today) groups[0].items.push(c);
    else if (t >= yesterday) groups[1].items.push(c);
    else if (t >= sevenDays) groups[2].items.push(c);
    else if (t >= thirtyDays) groups[3].items.push(c);
    else groups[4].items.push(c);
  }
  return groups;
}

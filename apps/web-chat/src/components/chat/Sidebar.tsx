"use client";

// Sidebar — ChatGPT/Claude-style minimal:
//
//   • Header: "SRAN Chat" wordmark + collapse button (desktop)
//   • Top actions: + New chat, Search chats (both subtle outlined)
//   • Conversation list grouped by time, plain-text items
//   • Footer: ONE user row that opens a popup menu (theme, language,
//     KB, voice settings, Memory, logout). Mirrors ChatGPT's footer
//     — a single tap target, no icon-grid.
//
// Below 80 rows we render plain divs; above that we virtualize via
// @tanstack/react-virtual. Server-side search is debounced 300ms.

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";
import { useNewChatHandler } from "@/hooks/useKeyboardShortcuts";
import { useHostedMode } from "@/hooks/useHostedMode";
import { useKnowledgeAvailable } from "@/hooks/useKnowledgeAvailable";
import { useShortcutHint } from "@/hooks/useShortcutHint";
import { logoutSession } from "@/lib/auth";
import { CONV_SEARCH_EVENT } from "@/lib/composerShortcuts";
import { requestMessageJump } from "@/lib/messageJump";
import { NewProjectDialog } from "./NewProjectDialog";
import { useI18nStore, useT } from "@/lib/i18n";
import type {
  Conversation,
  ConversationSearchHit,
  Project,
} from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useThemeStore } from "@/stores/themeStore";
import { ConversationListSkeleton } from "@/components/ui/Skeleton";
import {
  Archive, Brain, BrainCircuit, ChevronRight, Clock, FolderKanban, Pencil, PenLine, Plug, Plus,
  Settings as SettingsIcon, Trash2, Wrench,
} from "lucide-react";

import { useMemoryPendingStore } from "@/hooks/useMemoryPendingCount";
import { AgentRailSection } from "@/features/agent-market/AgentRailSection";

import { ConversationItem } from "./ConversationItem";
import { VoiceSettingsModal } from "./VoiceSettingsModal";

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
  | { kind: "project"; project: Project; collapsed: boolean; key: string }
  | { kind: "conv"; conv: Conversation; key: string }
  // Full-text content match (message bodies) — search mode only.
  | { kind: "hit"; hit: ConversationSearchHit; key: string };

export function Sidebar({
  activeId,
  open,
  collapsed,
  onClose,
  onToggleCollapsed,
}: Props) {
  const router = useRouter();
  const { user } = useAuth();
  const kbAvailable = useKnowledgeAvailable(); // FIX130 C2
  const conversations = useChatStore((s) => s.conversations);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const loadMoreConversations = useChatStore((s) => s.loadMoreConversations);
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const conversationsLoadFailed = useChatStore((s) => s.conversationsLoadFailed);
  const hasMoreConversations = useChatStore((s) => s.hasMoreConversations);
  const loadingMoreConversations = useChatStore((s) => s.loadingMoreConversations);
  const searchResults = useChatStore((s) => s.conversationsSearchResults);
  const searchHits = useChatStore((s) => s.conversationsSearchHits);
  const searchActive = useChatStore((s) => s.conversationsSearchActive);
  const searchLoading = useChatStore((s) => s.conversationsSearchLoading);
  const runSearch = useChatStore((s) => s.searchConversations);
  const clearSearch = useChatStore((s) => s.clearConversationsSearch);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const togglePinConversation = useChatStore((s) => s.togglePinConversation);
  const createProject = useChatStore((s) => s.createProject);
  const updateProject = useChatStore((s) => s.updateProject);
  const deleteProject = useChatStore((s) => s.deleteProject);
  const projects = useChatStore((s) => s.projects);
  const projectsLoaded = useChatStore((s) => s.projectsLoaded);
  const loadProjects = useChatStore((s) => s.loadProjects);
  const moveConversationToProject = useChatStore((s) => s.moveConversationToProject);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const messagesByConv = useChatStore((s) => s.messages);
  const setError = useChatStore((s) => s.setError);
  const setConversationArchived = useChatStore((s) => s.setConversationArchived);
  const newChat = useNewChatHandler();

  const [search, setSearch] = useState("");
  const [searchExpanded, setSearchExpanded] = useState(false);
  const [voiceSettingsOpen, setVoiceSettingsOpen] = useState(false);
  // Per-project collapse state, persisted so a project the user closed
  // stays closed across reloads. Keyed by project id → true when
  // collapsed. Falls back to the pre-Phase-9 folder key on first load:
  // migration 031 gave each project its folder's id, so the old collapse
  // map still addresses the right rows and the layout survives the merge.
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>(
    () => {
      if (typeof window === "undefined") return {};
      try {
        const current = localStorage.getItem("sidebar.projects.collapsed");
        if (current) return JSON.parse(current);
        return JSON.parse(localStorage.getItem("sidebar.folders.collapsed") || "{}");
      } catch {
        return {};
      }
    },
  );
  const debouncedSearch = useDebounce(search, 300);
  const t = useT();
  const hint = useShortcutHint();

  // FIX84 row 1 — Cmd/Ctrl+K arrives here when the search input is not
  // mounted yet (it only exists while the row is expanded). Expanding the
  // row mounts the autofocused input, which is what the shortcut wants.
  useEffect(() => {
    const onSearchShortcut = () => setSearchExpanded(true);
    window.addEventListener(CONV_SEARCH_EVENT, onSearchShortcut);
    return () => window.removeEventListener(CONV_SEARCH_EVENT, onSearchShortcut);
  }, []);

  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
  }, [conversationsLoaded, loadConversations]);

  useEffect(() => {
    if (!projectsLoaded) void loadProjects();
  }, [projectsLoaded, loadProjects]);

  function toggleProjectCollapsed(id: string) {
    setCollapsedProjects((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      try {
        localStorage.setItem("sidebar.projects.collapsed", JSON.stringify(next));
      } catch {
        /* ignore quota / privacy-mode failures */
      }
      return next;
    });
  }

  // A project created from the sidebar is a plain grouping container: no
  // instructions, no knowledge base — exactly what a folder was. The user
  // can add instructions later from the Projects page.
  // FIX132 F29 — it is named first (NewProjectDialog), not created silently
  // as "โปรเจกต์ใหม่"; then it opens (or, from a chat's menu, takes the chat).
  const [newProject, setNewProject] = useState<{ moveConvId?: string } | null>(null);

  function handleCreateProject() {
    setNewProject({});
  }

  function handleCreateProjectForMove(convId: string) {
    setNewProject({ moveConvId: convId });
  }

  async function createNamedProject(name: string): Promise<void> {
    const moveConvId = newProject?.moveConvId;
    let p: Project;
    try {
      p = await createProject(name, "");
    } catch (err) {
      setError(t("projects.toast.createFailed"));
      throw err;
    }
    setNewProject(null);
    // Expand the new project so the user sees it appear.
    setCollapsedProjects((prev) => ({ ...prev, [p.id]: false }));
    if (moveConvId) {
      await moveConversationToProject(moveConvId, p.id);
      return;
    }
    router.push(`/chat/projects?project=${p.id}`);
    onClose();
  }

  async function handleDeleteProject(id: string) {
    try {
      await deleteProject(id);
    } catch {
      setError(t("projects.toast.deleteFailed"));
    }
  }

  async function handleRenameProject(id: string, name: string) {
    try {
      await updateProject(id, { name });
    } catch {
      setError(t("projects.toast.saveFailed"));
    }
  }

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
      const out: Row[] = searchResults.map((c) => ({
        kind: "conv" as const,
        conv: c,
        key: c.id,
      }));
      // Content matches (message bodies via the S6 full-text engine)
      // render under their own section, below the title matches.
      if (searchHits.length > 0) {
        out.push({
          kind: "header",
          label: "sidebar.searchMessages",
          key: "h-search-messages",
        });
        for (const h of searchHits) {
          out.push({ kind: "hit", hit: h, key: `hit-${h.conversation_id}` });
        }
      }
      return out;
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
    const byRecency = (a: Conversation, b: Conversation) =>
      new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();

    const out: Row[] = [];

    // Projects come FIRST, above PINNED + the time buckets. Each project is
    // a collapsible section; only its own conversations (project_id ===
    // project.id) render under it, and a conversation inside a project does
    // NOT also appear in the PINNED / time buckets below. This is the
    // Phase-9 merge: folders used to occupy this slot (migration 022) and
    // are now projects (migration 031) — a project with no instructions
    // behaves exactly like the old folder.
    const byProject = new Map<string, Conversation[]>();
    for (const c of filtered) {
      if (!c.project_id) continue;
      const list = byProject.get(c.project_id) ?? [];
      list.push(c);
      byProject.set(c.project_id, list);
    }
    for (const project of projects) {
      const collapsed = collapsedProjects[project.id] ?? false;
      out.push({ kind: "project", project, collapsed, key: `project-${project.id}` });
      if (collapsed) continue;
      const inProject = (byProject.get(project.id) ?? []).sort(byRecency);
      for (const c of inProject) {
        out.push({ kind: "conv", conv: c, key: c.id });
      }
    }

    // Only UNASSIGNED conversations flow through PINNED + the time buckets.
    const unfiled = filtered.filter((c) => !c.project_id);
    // Pinned conversations get their own "PINNED" section, independent of
    // the time buckets, sorted by updated_at within the section.
    const pinned = unfiled.filter((c) => c.is_pinned).sort(byRecency);
    const unpinned = unfiled.filter((c) => !c.is_pinned).sort(byRecency);

    if (pinned.length > 0) {
      out.push({
        kind: "header",
        label: "sidebar.group.pinned",
        key: "h-pinned",
      });
      for (const c of pinned) {
        out.push({ kind: "conv", conv: c, key: c.id });
      }
    }
    const groups = groupByTime(unpinned);
    for (const g of groups) {
      if (g.items.length === 0) continue;
      out.push({ kind: "header", label: g.label, key: `h-${g.label}` });
      for (const c of g.items) {
        out.push({ kind: "conv", conv: c, key: c.id });
      }
    }
    return out;
  }, [searchActive, searchResults, searchHits, conversations, activeConversationId, messagesByConv, projects, collapsedProjects]);

  function handleNew() {
    newChat();
    onClose();
  }

  // A temporary chat starts the same way an ordinary one does — the row
  // is created lazily on the first send — so the flag is set here and
  // consumed by that create, which PATCHes memory_enabled=false before
  // the first message goes anywhere. Same contract the memory page's
  // launcher uses; this one exists because the sidebar is where people
  // actually start chats.
  function handleNewTemporary() {
    useChatStore.getState().setNextConversationTemporary(true);
    newChat();
    onClose();
  }

  function handleOpenMemory() {
    router.push("/chat/memory");
    onClose();
  }

  function handleLogout() {
    // Revoke + clear the HttpOnly session cookies on the gateway, then
    // bounce to the Portal login.
    void logoutSession();
  }

  const isHidden = collapsed;
  // FIX132 F16 — a sidebar that is off screen is `inert` (out of the Tab
  // order and the accessibility tree): the closed drawer below md, the
  // collapsed sidebar from md up. NIGHT3 j6: at 360 px, 12 of the first 23
  // Tab stops were on the closed drawer (x = -298).
  const isDesktop = useMediaQuery("(min-width: 768px)");
  const offScreen = isDesktop ? isHidden : !open;
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
        // React 18 renders `inert` only as a string ("" = present); the
        // types say boolean, hence the cast.
        {...(offScreen ? ({ inert: "" } as unknown as { inert: boolean }) : {})}
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
            aria-label={t("a11y.collapseSidebar")}
            title={`${t("a11y.collapseSidebar")} (${hint("Mod+Shift+S")})`}
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
              {hint("Mod+Shift+N")}
            </span>
          </button>

          <button
            type="button"
            onClick={handleNewTemporary}
            title={t("sidebar.newTemporaryChat.tooltip")}
            className="focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            <BrainCircuit width={16} height={16} aria-hidden />
            <span>{t("sidebar.newTemporaryChat")}</span>
          </button>

          <button
            type="button"
            onClick={handleCreateProject}
            className="focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            <span className="relative flex h-4 w-4 items-center justify-center" aria-hidden>
              <FolderKanban width={16} height={16} />
              <Plus
                width={9}
                height={9}
                strokeWidth={3}
                className="absolute -bottom-0.5 -right-1 rounded-full bg-bg-sidebar"
              />
            </span>
            <span>{t("project.new")}</span>
          </button>
          <AgentRailSection onNavigate={onClose} />

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
                  aria-label={t("a11y.clearSearch")}
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
                {hint("Mod+K")}
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
          onSelect={(id, messageId) => {
            // FIX132 F12 — a content hit opens the chat AT the message.
            if (messageId) requestMessageJump(id, messageId);
            router.push(messageId ? `/chat/${id}#msg-${messageId}` : `/chat/${id}`);
            onClose();
          }}
          onRename={(id, t) => renameConversation(id, t)}
          onDelete={async (id) => {
            await deleteConversation(id);
            if (id === activeId) router.replace("/chat");
          }}
          onTogglePin={(id) => togglePinConversation(id)}
          onToggleArchive={async (id) => {
            const ok = await setConversationArchived(id, true);
            // Archiving the OPEN chat would leave the user staring at a
            // thread that is no longer in the list — send them home.
            // FIX84 row 6: only when it actually archived; on failure the
            // row is back in the list and the banner says so.
            if (ok && id === activeId) router.replace("/chat");
          }}
          projects={projects}
          onMoveToProject={(id, projectId) => moveConversationToProject(id, projectId)}
          onCreateProjectForMove={(id) => handleCreateProjectForMove(id)}
          onToggleProject={toggleProjectCollapsed}
          onRenameProject={(id, name) => handleRenameProject(id, name)}
          onDeleteProject={(id) => handleDeleteProject(id)}
        />

        {/* Footer — single user row that opens the popup menu. */}
        <UserMenu
          userDisplay={userDisplay}
          userInitial={userInitial}
          onKnowledgeBase={kbAvailable ? () => router.push("/chat/knowledge") : undefined}
          onProjects={() => router.push("/chat/projects")}
          onArchive={() => router.push("/chat/archive")}
          onVoiceSettings={() => setVoiceSettingsOpen(true)}
          onMemory={handleOpenMemory}
          onLogout={handleLogout}
        />
      </aside>
      <VoiceSettingsModal
        open={voiceSettingsOpen}
        onClose={() => setVoiceSettingsOpen(false)}
      />
      <NewProjectDialog open={newProject !== null} onClose={() => setNewProject(null)} onCreate={createNamedProject} />
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
  onProjects,
  onArchive,
  onVoiceSettings,
  onMemory,
  onLogout,
}: {
  userDisplay: string;
  userInitial: string;
  /** FIX130 C2 — absent when a knowledge base cannot work (no menu entry). */
  onKnowledgeBase?: () => void;
  onProjects: () => void;
  onArchive: () => void;
  onVoiceSettings: () => void;
  onMemory: () => void;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const hosted = useHostedMode();
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggle);
  const isDark = theme === "dark";
  const locale = useI18nStore((s) => s.locale);
  const toggleLocale = useI18nStore((s) => s.toggle);
  const t = useT();

  // WAI-ARIA menu pattern: focus moves INTO the menu on open, ArrowUp/
  // ArrowDown cycle through items, Home/End jump, Escape closes and
  // returns focus to the trigger. Refs (not state) so the keyboard
  // handlers read the live DOM order.
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuItems = () =>
    Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );

  useEffect(() => {
    if (!open) return;
    // Initial focus: first menu item (menu ARIA contract).
    menuItems()[0]?.focus();
    const off = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", off);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function onMenuKeyDown(e: React.KeyboardEvent) {
    const list = menuItems();
    if (list.length === 0) return;
    const idx = list.indexOf(document.activeElement as HTMLElement);
    const focusAt = (i: number) =>
      list[(i + list.length) % list.length].focus();
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        focusAt(idx + 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusAt(idx - 1);
        break;
      case "Home":
        e.preventDefault();
        list[0].focus();
        break;
      case "End":
        e.preventDefault();
        list[list.length - 1].focus();
        break;
    }
  }

  return (
    <div ref={wrapRef} className="relative border-t border-border-subtle">
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label={t("a11y.accountMenu")}
          onKeyDown={onMenuKeyDown}
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
          {/* FIX130 C2 — only when a knowledge base can work. */}
          {onKnowledgeBase ? (
            <MenuItem
              onClick={onKnowledgeBase}
              icon={<BookIcon />}
              label={t("menu.knowledgeBase")}
              trailing={<ArrowIcon />}
              setOpen={setOpen}
            />
          ) : null}
          <MenuItem
            onClick={onProjects}
            icon={<FolderKanban width={14} height={14} aria-hidden />}
            label={t("menu.projects")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          <MenuItem
            onClick={onArchive}
            icon={<Archive width={14} height={14} aria-hidden />}
            label={t("archive.open")}
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
            onClick={onMemory}
            icon={<Brain width={14} height={14} aria-hidden />}
            label={t("menu.memory")}
            trailing={<MemoryTrailing />}
            setOpen={setOpen}
          />
          {/* FIX34 Part E — Skills live in the sidebar menu for EVERY surface
              (claude.ai: Customize › Skills), not on the landing page. The web
              page is chat-edge-backed (FIX32 4.3); the desktop one is host-backed. */}
          <MenuItem
            onClick={() => router.push("/chat/skills")}
            icon={<Wrench width={14} height={14} aria-hidden />}
            label={t("menu.skills")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          {/* FIX43 Part B — gated per SECTION, not per page. Scheduled tasks
              and Settings have a web half (cloud tasks are server-side; the
              web Settings page is appearance + account), so they show on
              both surfaces and each page hides its device-only sections
              itself. Connectors and Writing style keep ALL their state in the
              desktop host over the bridge (docs/FIX43_REPORT.md §B2), so they
              stay desktop-only — decided at RUNTIME (FIX20 §6.5): hosted is
              true exactly when the Electron preload's window.sranHost is
              present, so ONE build serves both surfaces. */}
          <div className="border-t border-border" />
          <MenuItem
            onClick={() => router.push("/chat/tasks")}
            icon={<Clock width={14} height={14} aria-hidden />}
            label={t("menu.scheduledTasks")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          {hosted ? (
            <>
              <MenuItem
                onClick={() => router.push("/chat/connectors")}
                icon={<Plug width={14} height={14} aria-hidden />}
                label={t("menu.connectors")}
                trailing={<ArrowIcon />}
                setOpen={setOpen}
              />
              <MenuItem
                onClick={() => router.push("/chat/writing-style")}
                icon={<PenLine width={14} height={14} aria-hidden />}
                label={t("menu.writingStyle")}
                trailing={<ArrowIcon />}
                setOpen={setOpen}
              />
            </>
          ) : null}
          <MenuItem
            onClick={() => router.push("/chat/settings")}
            icon={<SettingsIcon width={14} height={14} aria-hidden />}
            label={t("menu.settings")}
            trailing={<ArrowIcon />}
            setOpen={setOpen}
          />
          <div className="border-t border-border" />
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
        ref={triggerRef}
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

// S8 — trailing slot for the Memory menu item: a blue pill with
// the pending-preference count + the existing arrow caret.
// Renders only the arrow when count is 0 so the menu row stays
// quiet for users who don't have any suggestions.
function MemoryTrailing() {
  const count = useMemoryPendingStore((s) => s.count);
  if (count <= 0) {
    return <ArrowIcon />;
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-flex min-w-[18px] items-center justify-center rounded-full bg-blue-600 px-1.5 py-0.5 text-[10px] font-semibold text-white"
        aria-label={`${count} pending preference${count === 1 ? "" : "s"}`}
      >
        {count > 99 ? "99+" : count}
      </span>
      <ArrowIcon />
    </span>
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

/** Conversation id a row navigates to, or null for non-navigable rows.
 *  Lets conv AND content-hit rows share the active-highlight logic. */
function rowActiveId(row: Row): string | null {
  if (row.kind === "conv") return row.conv.id;
  if (row.kind === "hit") return row.hit.conversation_id;
  return null;
}

interface AreaProps {
  rows: Row[];
  conversationsLoaded: boolean;
  searchActive: boolean;
  searchLoading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  activeId: string | null;
  onSelect: (id: string, messageId?: string) => void;
  onRename: (id: string, title: string) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
  onTogglePin: (id: string) => Promise<void> | void;
  onToggleArchive: (id: string) => Promise<void> | void;
  projects: Project[];
  onMoveToProject: (id: string, projectId: string | null) => Promise<void> | void;
  onCreateProjectForMove: (id: string) => Promise<void> | void;
  onToggleProject: (id: string) => void;
  onRenameProject: (id: string, name: string) => Promise<void> | void;
  onDeleteProject: (id: string) => Promise<void> | void;
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
  onTogglePin,
  onToggleArchive,
  projects,
  onMoveToProject,
  onCreateProjectForMove,
  onToggleProject,
  onRenameProject,
  onDeleteProject,
}: AreaProps) {
  const t = useT();
  // FIX86 Part C — "could not load" is not "you have none".
  const conversationsLoadFailed = useChatStore((s) => s.conversationsLoadFailed);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const ref = useRef<HTMLDivElement>(null);
  const useVirtual = rows.length >= VIRTUALIZE_THRESHOLD;
  const projectCtx: ProjectCtx = {
    projects,
    onMoveToProject,
    onCreateProjectForMove,
    onToggleProject,
    onRenameProject,
    onDeleteProject,
  };

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

  if (rows.length === 0 && conversationsLoadFailed && !searchActive) {
    // FIX86 Part C — "could not load" must not read as "you have none".
    return (
      <nav className="scrollbar-thin mt-3 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-3 py-4 text-xs text-text-muted" role="status" data-testid="sidebar-load-failed">
          <div>{t("sidebar.loadFailed")}</div>
          <button
            type="button"
            onClick={() => void loadConversations()}
            className="focus-ring mt-2 rounded-full border border-border px-2.5 py-1 text-xs font-medium text-text-primary transition-colors hover:border-border-strong"
          >
            {t("sidebar.retry")}
          </button>
        </div>
      </nav>
    );
  }

  if (rows.length === 0) {
    return (
      <nav className="scrollbar-thin mt-3 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-3 py-4 text-xs text-text-muted">
          {searchActive
            ? searchLoading
              ? t("sidebar.searching")
              : t("sidebar.noResults")
            : t("sidebar.empty")}
        </div>
      </nav>
    );
  }

  return (
    <nav
      ref={ref}
      onScroll={onScroll}
      onKeyDown={moveBetweenChats}
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
                  active={rowActiveId(row) === activeId && activeId !== null}
                  onSelect={onSelect}
                  onRename={onRename}
                  onDelete={onDelete}
                  onTogglePin={onTogglePin}
                  onToggleArchive={onToggleArchive}
                  projectCtx={projectCtx}
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
              active={rowActiveId(row) === activeId && activeId !== null}
              onSelect={onSelect}
              onRename={onRename}
              onDelete={onDelete}
              onTogglePin={onTogglePin}
                  onToggleArchive={onToggleArchive}
              projectCtx={projectCtx}
            />
          ))}
        </div>
      )}
      {hasMore ? (
        <div className="flex justify-center py-2 text-[10px] text-text-muted">
          {loadingMore ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
              {t("sidebar.loadingMore")}
            </span>
          ) : (
            <button
              type="button"
              onClick={onLoadMore}
              className="focus-ring rounded px-2 py-1 hover:text-text-primary"
            >
              {t("sidebar.loadMore")}
            </button>
          )}
        </div>
      ) : null}
    </nav>
  );
}

// ProjectCtx bundles the project data + handlers so SidebarRow gets one prop
// instead of six. Built once in ConversationsArea.
interface ProjectCtx {
  projects: Project[];
  onMoveToProject: (id: string, projectId: string | null) => Promise<void> | void;
  onCreateProjectForMove: (id: string) => Promise<void> | void;
  onToggleProject: (id: string) => void;
  onRenameProject: (id: string, name: string) => Promise<void> | void;
  onDeleteProject: (id: string) => Promise<void> | void;
}

function SidebarRow({
  row,
  active,
  onSelect,
  onRename,
  onDelete,
  onTogglePin,
  onToggleArchive,
  projectCtx,
}: {
  row: Row;
  active: boolean;
  onSelect: (id: string, messageId?: string) => void;
  onRename: (id: string, title: string) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
  onTogglePin: (id: string) => Promise<void> | void;
  onToggleArchive: (id: string) => Promise<void> | void;
  projectCtx: ProjectCtx;
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
  if (row.kind === "project") {
    return (
      <ProjectHeader
        project={row.project}
        collapsed={row.collapsed}
        onToggle={() => projectCtx.onToggleProject(row.project.id)}
        onRename={(name) => projectCtx.onRenameProject(row.project.id, name)}
        onDelete={() => projectCtx.onDeleteProject(row.project.id)}
      />
    );
  }
  if (row.kind === "hit") {
    // Content match — click navigates to the conversation. Kept as a
    // plain button (no rename/delete menu): the same conversation may
    // also appear above as a title match with the full menu.
    return (
      <button
        type="button"
        onClick={() => onSelect(row.hit.conversation_id, row.hit.message_id)}
        className={`focus-ring flex w-full flex-col items-start gap-0.5 rounded-lg px-3 py-1.5 text-left text-[13px] transition-colors ${
          active
            ? "bg-bg-elevated text-text-primary"
            : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
        }`}
      >
        <span className="w-full truncate">{row.hit.conversation_title}</span>
        <span className="line-clamp-2 w-full text-[11px] leading-snug text-text-muted">
          {row.hit.snippet}
        </span>
      </button>
    );
  }
  return (
    <ConversationItem
      conv={row.conv}
      active={active}
      onSelect={() => onSelect(row.conv.id)}
      onRename={(title) => onRename(row.conv.id, title)}
      onDelete={() => onDelete(row.conv.id)}
      onTogglePin={() => onTogglePin(row.conv.id)}
      onToggleArchive={() => onToggleArchive(row.conv.id)}
      projects={projectCtx.projects}
      onMoveToProject={(projectId) => projectCtx.onMoveToProject(row.conv.id, projectId)}
      onCreateProjectForMove={() => projectCtx.onCreateProjectForMove(row.conv.id)}
    />
  );
}

// ProjectHeader — collapsible project section header with a ··· menu
// (inline rename + delete-with-confirm). Mirrors ConversationItem's menu
// idiom so the two read consistently. Renders identically whether or not
// the project carries instructions / a knowledge base: an empty project
// is just a grouping container (the Phase-9 replacement for a folder),
// and the Projects page is where the extra context is edited.
function ProjectHeader({
  project,
  collapsed,
  onToggle,
  onRename,
  onDelete,
}: {
  project: Project;
  collapsed: boolean;
  onToggle: () => void;
  onRename: (name: string) => Promise<void> | void;
  onDelete: () => Promise<void> | void;
}) {
  const t = useT();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [draft, setDraft] = useState(project.name);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (renaming) {
      setDraft(project.name);
      inputRef.current?.select();
    }
  }, [renaming, project.name]);

  useEffect(() => {
    if (!menuOpen) return;
    const off = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [menuOpen]);

  return (
    <div className="group relative flex flex-col">
      <div className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] text-text-secondary transition-colors hover:bg-bg-elevated">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={!collapsed}
          className="focus-ring flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          <ChevronRight
            width={14}
            height={14}
            aria-hidden
            className={`shrink-0 text-text-muted transition-transform ${collapsed ? "" : "rotate-90"}`}
          />
          <FolderKanban width={14} height={14} aria-hidden className="shrink-0" />
          {renaming ? (
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onBlur={() => {
                const n = draft.trim();
                if (n && n !== project.name) void onRename(n);
                setRenaming(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                else if (e.key === "Escape") {
                  setDraft(project.name);
                  setRenaming(false);
                }
              }}
              className="w-full rounded bg-bg-base px-1.5 py-0.5 text-sm text-text-primary outline-none focus:ring-1 focus:ring-border-strong"
            />
          ) : (
            <>
              <span className="truncate font-medium">{project.name}</span>
              <span className="shrink-0 text-text-muted">
                ({project.conversation_count})
              </span>
            </>
          )}
        </button>
        <button
          type="button"
          aria-label={t("a11y.projectActions")}
          onClick={() => setMenuOpen((v) => !v)}
          className={`focus-ring tap-44 rounded p-1 text-text-muted transition-opacity hover:bg-bg-secondary hover:text-text-primary md:opacity-0 md:group-hover:opacity-100 ${
            menuOpen ? "md:opacity-100" : ""
          }`}
        >
          <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden>
            <circle cx="3" cy="8" r="1.5" fill="currentColor" />
            <circle cx="8" cy="8" r="1.5" fill="currentColor" />
            <circle cx="13" cy="8" r="1.5" fill="currentColor" />
          </svg>
        </button>
        {menuOpen ? (
          <div
            ref={menuRef}
            className="popover-appear absolute right-1 top-9 z-10 w-36 overflow-hidden rounded-md border border-border bg-bg-surface text-xs shadow-xl"
          >
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                setRenaming(true);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
            >
              <Pencil width={14} height={14} aria-hidden />
              {t("project.rename")}
            </button>
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                setConfirmingDelete(true);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-status-danger hover:bg-status-danger/15"
            >
              <Trash2 width={14} height={14} aria-hidden />
              {t("project.delete")}
            </button>
          </div>
        ) : null}
      </div>
      {confirmingDelete ? (
        <div className="border-t border-border px-3 py-2 text-[11px]">
          <p className="text-text-secondary">{t("project.deleteConfirm")}</p>
          <div className="mt-1.5 flex gap-2">
            <button
              type="button"
              onClick={() => {
                setConfirmingDelete(false);
                void onDelete();
              }}
              className="focus-ring rounded bg-status-danger-solid px-2 py-1 font-medium text-white hover:bg-status-danger-solid/90"
            >
              {t("project.delete")}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className="focus-ring rounded border border-border px-2 py-1 text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
            >
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
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

/** FIX128 D4 — keyboard-only navigation of the chat list: ArrowDown/ArrowUp
 *  move focus to the next/previous chat row, Home/End to the first/last.
 *  Enter/Space still open it (ConversationItem). A key pressed inside a row's
 *  rename box or menu is left alone. */
export function moveBetweenChats(e: React.KeyboardEvent<HTMLElement>): void {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
  const target = e.target as HTMLElement;
  if (target.closest("input, textarea, [role='menu']")) return;
  const rows = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[data-conversation-id]"));
  if (rows.length === 0) return;
  const at = rows.findIndex((r) => r === target || r.contains(target));
  let next = at;
  if (e.key === "ArrowDown") next = at < 0 ? 0 : Math.min(rows.length - 1, at + 1);
  else if (e.key === "ArrowUp") next = at < 0 ? 0 : Math.max(0, at - 1);
  else if (e.key === "Home") next = 0;
  else next = rows.length - 1;
  if (next === at) return;
  e.preventDefault();
  rows[next].focus();
  rows[next].scrollIntoView({ block: "nearest" });
}

/** FIX132 F16 — a media query as state (false until mounted; jsdom-safe). */
function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const sync = () => setMatches(mql.matches);
    sync();
    mql.addEventListener?.("change", sync);
    return () => mql.removeEventListener?.("change", sync);
  }, [query]);
  return matches;
}

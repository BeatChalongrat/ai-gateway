"use client";

// Sidebar — paginated conversation list with optional server-side
// search and a virtualized scroller. Renders:
//
//   • a header w/ logo + collapse toggle (desktop only)
//   • a "New chat" button
//   • a debounced search input
//   • the conversation list:
//        - search-active path: flat list of search results
//        - normal path: time-grouped headers + paginated rows,
//          with infinite scroll loading the next page near the
//          bottom of the scroll container.
//
// Below ~80 items we skip the virtualizer (the overhead is wasted
// for short lists). Above that, @tanstack/react-virtual measures
// each row so variable-height items (inline rename / delete
// confirm) don't break the layout.

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";
import { clearPortalToken } from "@/lib/auth";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";
import { ConversationListSkeleton } from "@/components/ui/Skeleton";

import { ConversationItem } from "./ConversationItem";
import { CustomInstructionsModal } from "./CustomInstructionsModal";

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
  const createConversation = useChatStore((s) => s.createConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const loadModels = useModelStore((s) => s.loadModels);
  const modelsLoaded = useModelStore((s) => s.loaded);

  const [search, setSearch] = useState("");
  const [instructionsOpen, setInstructionsOpen] = useState(false);
  // 300ms per spec (debounced server-search round-trip).
  const debouncedSearch = useDebounce(search, 300);

  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
    if (!modelsLoaded) void loadModels();
  }, [conversationsLoaded, loadConversations, modelsLoaded, loadModels]);

  // Server-search trigger. Fires whenever the debounced value
  // changes; the store no-ops on an empty input.
  useEffect(() => {
    const q = debouncedSearch.trim();
    if (q) {
      void runSearch(q);
    } else {
      clearSearch();
    }
  }, [debouncedSearch, runSearch, clearSearch]);

  // Build the flat rows array. Time-grouped when not searching;
  // a flat list when searching.
  const rows = useMemo<Row[]>(() => {
    if (searchActive) {
      return searchResults.map((c) => ({
        kind: "conv" as const,
        conv: c,
        key: c.id,
      }));
    }
    const sorted = [...conversations].sort(
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
  }, [searchActive, searchResults, conversations]);

  async function handleNew() {
    if (!selectedModelId) return;
    const c = await createConversation(selectedModelId);
    router.push(`/chat/${c.id}`);
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
      <div
        className={`fixed inset-0 z-30 bg-black/50 backdrop-blur-sm transition-opacity duration-200 md:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      <aside
        className={`safe-top safe-bottom fixed left-0 top-0 z-40 flex h-full w-[85vw] max-w-[320px] flex-col border-r border-border bg-bg-sidebar transition-all duration-200 ease-out md:static md:w-[280px] md:max-w-none ${
          open ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0 ${
          isHidden
            ? "md:pointer-events-none md:w-0 md:-translate-x-full md:overflow-hidden md:opacity-0"
            : ""
        }`}
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-4">
          <div className="flex items-center gap-2">
            <div className="h-7 w-7 rounded-md bg-gradient-to-br from-accent to-accent-subtle" />
            <span className="font-semibold tracking-tight">SRAN Chat</span>
          </div>
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            className="focus-ring hidden rounded-md p-1.5 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary md:inline-flex"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
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

        <div className="px-3 pt-3">
          <button
            type="button"
            onClick={handleNew}
            disabled={!selectedModelId}
            className="focus-ring flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg-secondary px-3 py-2.5 text-sm font-medium text-text-primary transition-all hover:bg-bg-elevated hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M12 5v14M5 12h14"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
            New chat
          </button>
        </div>

        <div className="px-3 pt-2">
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
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search conversations"
              className="w-full rounded-md border border-border bg-bg-base/50 py-1.5 pl-8 pr-7 text-xs text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
            />
            {search ? (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => setSearch("")}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
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
        </div>

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

        <div className="flex items-center gap-3 border-t border-border px-4 py-3">
          <span
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sky-700 to-indigo-800 text-xs font-semibold text-white"
            aria-hidden
          >
            {userInitial}
          </span>
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-xs font-medium text-text-primary"
              style={{ maxWidth: 120 }}
              title={userDisplay}
            >
              {userDisplay}
            </div>
            <div className="truncate text-[11px] text-text-muted">
              {user?.role ?? "user"}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setInstructionsOpen(true)}
            aria-label="Custom instructions"
            title="Custom instructions"
            className="focus-ring rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
              <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
              <path
                d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06A2 2 0 0 1 4.21 16.97l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9 1.65 1.65 0 0 0 4.27 7.18l-.06-.06A2 2 0 1 1 7.03 4.29l.06.06A1.65 1.65 0 0 0 8.91 4.6 1.65 1.65 0 0 0 9.91 3.09V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <button
            type="button"
            onClick={handleLogout}
            aria-label="Logout"
            className="focus-ring rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
            title="Logout"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M16 17l5-5-5-5M21 12H9M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </aside>
      <CustomInstructionsModal
        open={instructionsOpen}
        onClose={() => setInstructionsOpen(false)}
      />
    </>
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
  // Below the virtualization threshold, render plain divs — the
  // virtualizer's overhead isn't worth it for short lists.
  const useVirtual = rows.length >= VIRTUALIZE_THRESHOLD;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => (useVirtual ? ref.current : null),
    estimateSize: (i) => (rows[i]?.kind === "header" ? 28 : 64),
    overscan: 8,
    measureElement(el) {
      return el?.getBoundingClientRect().height ?? 0;
    },
  });

  // Infinite scroll: bump the next page when the user lands within
  // ~200px of the bottom of the scroll container.
  function onScroll(e: React.UIEvent<HTMLDivElement>) {
    if (!hasMore || loadingMore) return;
    const el = e.currentTarget;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distance < LOAD_MORE_THRESHOLD_PX) {
      onLoadMore();
    }
  }

  // Loading skeleton: only on the FIRST load. Subsequent
  // pagination + search use inline spinners.
  if (!conversationsLoaded) {
    return (
      <nav className="scrollbar-thin mt-2 flex-1 overflow-y-auto px-2 pb-2">
        <ConversationListSkeleton rows={4} />
      </nav>
    );
  }

  if (rows.length === 0) {
    return (
      <nav className="scrollbar-thin mt-2 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-2 py-4 text-xs text-text-muted">
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
      className="scrollbar-thin mt-2 flex-1 overflow-y-auto px-2 pb-2"
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
                  active={
                    row.kind === "conv" ? row.conv.id === activeId : false
                  }
                  onSelect={onSelect}
                  onRename={onRename}
                  onDelete={onDelete}
                />
              </div>
            );
          })}
        </div>
      ) : (
        <div className="flex flex-col gap-0.5">
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
  if (row.kind === "header") {
    return (
      <div className="px-3 pb-1 pt-1 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {row.label}
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

// ─── Time grouping ───────────────────────────────────────────
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

  const groups: Group[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 Days", items: [] },
    { label: "Previous 30 Days", items: [] },
    { label: "Older", items: [] },
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

"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";
import { clearPortalToken } from "@/lib/auth";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";
import { ConversationListSkeleton } from "@/components/ui/Skeleton";

import { ConversationItem } from "./ConversationItem";

const PORTAL_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PORTAL_URL) ||
  "http://localhost:3001";

interface Props {
  activeId: string | null;
  open: boolean;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapsed: () => void;
}

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
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const createConversation = useChatStore((s) => s.createConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const loadModels = useModelStore((s) => s.loadModels);
  const modelsLoaded = useModelStore((s) => s.loaded);

  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 200);

  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
    if (!modelsLoaded) void loadModels();
  }, [conversationsLoaded, loadConversations, modelsLoaded, loadModels]);

  const sorted = useMemo(
    () =>
      [...conversations].sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      ),
    [conversations],
  );

  const filtered = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((c) => (c.title || "").toLowerCase().includes(q));
  }, [sorted, debouncedSearch]);

  const grouped = useMemo(() => groupByTime(filtered), [filtered]);

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

  const isHidden = collapsed; // desktop collapsed state
  const userDisplay =
    user?.email || (user?.user_id ? user.user_id.slice(0, 8) : "Guest");
  const userInitial = (userDisplay || "?").charAt(0).toUpperCase();

  return (
    <>
      {/* Mobile overlay */}
      <div
        className={`fixed inset-0 z-30 bg-black/50 backdrop-blur-sm transition-opacity duration-200 md:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      <aside
        className={`fixed left-0 top-0 z-40 flex h-full w-[280px] flex-col border-r border-border bg-bg-sidebar transition-all duration-200 ease-out md:static ${
          open ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0 ${
          isHidden
            ? "md:pointer-events-none md:w-0 md:-translate-x-full md:overflow-hidden md:opacity-0"
            : ""
        }`}
      >
        {/* Header */}
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

        {/* New chat */}
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

        {/* Search */}
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
              <circle
                cx="11"
                cy="11"
                r="7"
                stroke="currentColor"
                strokeWidth="2"
              />
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

        {/* Conversation list */}
        <nav className="scrollbar-thin mt-2 flex-1 overflow-y-auto px-2 pb-2">
          {!conversationsLoaded ? (
            <ConversationListSkeleton rows={4} />
          ) : conversations.length === 0 ? (
            <div className="px-2 py-4 text-xs text-text-muted">
              No conversations yet
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-2 py-4 text-xs text-text-muted">
              No conversations found
            </div>
          ) : debouncedSearch.trim() ? (
            <div className="flex flex-col gap-0.5">
              {filtered.map((c) => (
                <ConversationItem
                  key={c.id}
                  conv={c}
                  active={c.id === activeId}
                  onSelect={() => {
                    router.push(`/chat/${c.id}`);
                    onClose();
                  }}
                  onRename={(t) => renameConversation(c.id, t)}
                  onDelete={async () => {
                    await deleteConversation(c.id);
                    if (c.id === activeId) router.replace("/chat");
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {grouped.map(({ label, items }) =>
                items.length === 0 ? null : (
                  <div key={label}>
                    <div className="px-3 pb-1 pt-1 text-[10px] font-medium uppercase tracking-wider text-text-muted">
                      {label}
                    </div>
                    <div className="flex flex-col gap-0.5">
                      {items.map((c) => (
                        <ConversationItem
                          key={c.id}
                          conv={c}
                          active={c.id === activeId}
                          onSelect={() => {
                            router.push(`/chat/${c.id}`);
                            onClose();
                          }}
                          onRename={(t) => renameConversation(c.id, t)}
                          onDelete={async () => {
                            await deleteConversation(c.id);
                            if (c.id === activeId) router.replace("/chat");
                          }}
                        />
                      ))}
                    </div>
                  </div>
                ),
              )}
            </div>
          )}
        </nav>

        {/* Footer — user + logout */}
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
    </>
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

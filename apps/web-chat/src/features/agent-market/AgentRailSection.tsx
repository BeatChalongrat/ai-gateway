"use client";

// The Agent Market entry in the app sidebar.
//
//   * "Agent Market" — the way into the catalog; it stays where it is.
//   * "Agent ของฉัน (n)" (AM3) — a collapsible section remembered per user:
//     pinned agents first, then the most recently used, about five, and
//     "ดูทั้งหมด" for the rest. A run waiting for an answer is never hidden:
//     the section header carries the count even when collapsed, and a
//     collapsed section still lists the agents that wait.
//   * With text in the sidebar's search box (Ctrl+K), matching agents —
//     installed first, then catalog agents not installed yet.
//   * AM10: each installed agent opens to its own chats (ChatRows.tsx) —
//     the agent on screen opens by itself the first time; what a person opened
//     or closed is remembered in this browser, and going to another agent
//     changes neither (AM24c).
//   * AM24c: nothing moves under the pointer. The order is settled once and
//     held (useHeldOrder): a click moves only the highlight, and a poll, the
//     window's focus or a reload refreshes what the rows say, not where they
//     are. Pin, unpin, install and uninstall move rows — the person asked.
//
// Flag off = absent: renders null until the availability check says the
// feature is available, and never throws — the sidebar mounts this on every
// chat page, including in tests where fetch is missing or fails.

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Pin, PinOff, Store } from "lucide-react";

import { amApi } from "./api";
import { ChatRows, currentChatKey } from "./ChatRows";
import { agentShortName } from "./format";
import { useAmLocale, useAmT } from "./i18n";
import { railOrder, searchAgents } from "./rail";
import type { CatalogAgent, RailInstall } from "./types";
import { AgentTile, cls } from "./ui";
import { useAgentMarket } from "./useAgentMarket";
import { togglePin, useHeldOrder, useRail } from "./useRail";

export function AgentRailSection({ onNavigate, search = "" }: { onNavigate?: () => void; search?: string }) {
  const { state } = useAgentMarket();
  const t = useAmT();
  const pathname = usePathname() ?? "";
  const available = state === "available";
  const rail = useRail(available);
  const listId = useId();
  const [catalog, setCatalog] = useState<CatalogAgent[] | null>(null);
  const query = search.trim();

  // The catalog is read only once someone searches.
  useEffect(() => {
    if (!available || !query || catalog) return;
    let alive = true;
    amApi.catalog().then(
      (r) => alive && setCatalog(r?.agents ?? []),
      () => alive && setCatalog([]),
    );
    return () => {
      alive = false;
    };
  }, [available, query, catalog]);

  const hits = useMemo(() => searchAgents(query, rail.installs, catalog ?? []), [query, rail.installs, catalog]);
  const urlQuery = useSearchString();
  const activeId = activeInstallId(pathname);
  const held = useHeldOrder(rail.installs, rail.loaded, activeId);
  const [chatsOpen, setChatsOpen] = useChatsOpen();
  // an agent with no recorded state opens the first time the person goes into it, and is recorded so
  const activeInstalled = !!activeId && rail.installs.some((r) => r.id === activeId);
  useEffect(() => {
    if (activeInstalled && activeId && chatsOpen[activeId] === undefined) setChatsOpen(activeId, true);
  }, [activeInstalled, activeId, chatsOpen, setChatsOpen]);

  if (!available) return null;

  // The market, an agent's page and its install wizard; a workspace or a run
  // belongs to the installed agent below.
  const marketActive =
    pathname === "/chat/agents" ||
    (pathname.startsWith("/chat/agents/") &&
      !pathname.startsWith("/chat/agents/i/") &&
      !pathname.startsWith("/chat/agents/runs/") &&
      !pathname.startsWith("/chat/agents/mine"));

  const collapsed = rail.prefs.rail_collapsed === true;
  const byId = new Map(rail.installs.map((r) => [r.id, r]));
  const shown = held.hold
    ? held.hold.order.slice(0, held.hold.listed).flatMap((id) => byId.get(id) ?? [])
    : railOrder(rail.installs).shown;
  const rest = Math.max(0, rail.installs.length - shown.length);
  const visible = collapsed ? shown.filter((r) => r.waiting > 0) : shown;
  const waiting = rail.waitingTotal;
  const isOpen = (id: string) => chatsOpen[id] ?? id === activeId;

  const pin = (row: RailInstall) => {
    const was = !!row.pinned_at;
    const now = was ? null : new Date().toISOString();
    rail.patch(row.id, { pinned_at: now });
    held.pinned(row.id, now);
    togglePin(row.id, was).then(
      (at) => rail.patch(row.id, { pinned_at: at }),
      () => {
        rail.patch(row.id, { pinned_at: row.pinned_at });
        held.pinned(row.id, row.pinned_at);
      },
    );
  };

  return (
    <nav aria-label={t("rail.aria")} className="flex flex-col gap-0.5" data-testid="agent-rail-section">
      <Link
        href="/chat/agents"
        onClick={onNavigate}
        aria-current={marketActive ? "page" : undefined}
        className={cls(
          "focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-bg-elevated hover:text-text-primary",
          marketActive ? "bg-bg-elevated text-text-primary" : "text-text-secondary",
        )}
      >
        <Store width={16} height={16} aria-hidden />
        <span className="min-w-0 flex-1 truncate">{t("rail.market")}</span>
        {rail.sharedUnopened > 0 ? (
          <span className="rounded-full bg-bg-elevated px-1.5 text-[10px] tabular-nums text-text-secondary" title={t("rail.sharedNew")}>
            {rail.sharedUnopened}
            <span className="sr-only">{` ${t("rail.sharedNew")}`}</span>
          </span>
        ) : null}
      </Link>

      {query ? (
        <SearchHits query={query} installed={hits.installed} catalog={hits.catalog} loading={!catalog} onNavigate={onNavigate} />
      ) : rail.installs.length ? (
        <div data-testid="agent-rail-mine">
          <button
            type="button"
            aria-expanded={!collapsed}
            aria-controls={listId}
            onClick={() => rail.setPrefs({ rail_collapsed: !collapsed })}
            className="focus-ring flex w-full items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-medium tracking-wide text-text-muted hover:bg-bg-elevated hover:text-text-primary"
            data-testid="agent-rail-toggle"
          >
            <ChevronDown width={13} height={13} aria-hidden className={cls("shrink-0 transition-transform", collapsed ? "-rotate-90" : "")} />
            <span className="min-w-0 flex-1 truncate text-left">{t("rail.mine", { n: String(rail.installs.length) })}</span>
            {waiting > 0 ? (
              <span
                className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-status-warning px-1.5 text-[10px] font-semibold tabular-nums text-bg-base"
                data-testid="agent-rail-waiting-total"
                title={t("rail.waitingTotal", { n: String(waiting) })}
              >
                {waiting}
                <span className="sr-only">{` ${t("rail.waitingTotal", { n: String(waiting) })}`}</span>
              </span>
            ) : null}
          </button>
          <ul id={listId} className="flex flex-col gap-0.5">
            {visible.map((i) => (
              <RailRow
                key={i.id}
                row={i}
                pathname={pathname}
                search={urlQuery}
                opened={isOpen(i.id)}
                onToggle={() => setChatsOpen(i.id, !isOpen(i.id))}
                onNavigate={onNavigate}
                onPin={() => pin(i)}
              />
            ))}
          </ul>
          {!collapsed && rest > 0 ? (
            <Link
              href="/chat/agents/mine"
              onClick={onNavigate}
              className="focus-ring flex w-full items-center rounded-lg py-1.5 pl-5 pr-3 text-xs text-text-muted hover:bg-bg-elevated hover:text-text-primary"
              data-testid="agent-rail-all"
            >
              {t("rail.all", { n: String(rail.installs.length) })}
            </Link>
          ) : null}
        </div>
      ) : null}
    </nav>
  );
}

/** The URL's query, where next/navigation gives one (tests mount the sidebar without it). */
function useSearchString(): string | null {
  let hook: typeof useSearchParams | null = null;
  try {
    hook = typeof useSearchParams === "function" ? useSearchParams : null;
  } catch {
    hook = null;
  }
  // the hook is the module's, so this call is made or not made the same way on every render
  const params = hook ? hook() : null;
  return params ? params.toString() : null;
}

const AGENT_PATH = "/chat/agents/i/";

/** The installed agent on screen: its workspace, a chat of it, or a page under it. */
function activeInstallId(pathname: string): string | null {
  return pathname.startsWith(AGENT_PATH) ? pathname.slice(AGENT_PATH.length).split("/")[0] || null : null;
}

const CHATS_KEY = "am.rail.chats";

/**
 * Which agents show their chats in the sidebar — this browser's, like a folder left open. One stored value, one
 * meaning, for every row: true open, false closed; nothing recorded is closed, except on the agent on screen.
 * (Until AM24c the stored set meant "open" for every row but the one on screen, where it meant "closed", so going
 * to another agent flipped two rows at once. That set, am.rail.opened, is not read: what it held cannot be told
 * apart.)
 */
function useChatsOpen(): [Record<string, boolean>, (id: string, open: boolean) => void] {
  const [state, setState] = useState<Record<string, boolean>>(() => {
    try {
      const raw = typeof window !== "undefined" ? window.localStorage.getItem(CHATS_KEY) : null;
      const kept = raw ? (JSON.parse(raw) as unknown) : null;
      if (!kept || typeof kept !== "object" || Array.isArray(kept)) return {};
      return Object.fromEntries(Object.entries(kept).filter((e): e is [string, boolean] => typeof e[1] === "boolean"));
    } catch {
      return {};
    }
  });
  const set = useCallback((id: string, open: boolean) => {
    setState((cur) => {
      if (cur[id] === open) return cur;
      const next = { ...cur, [id]: open };
      try {
        window.localStorage.setItem(CHATS_KEY, JSON.stringify(next));
      } catch {
        /* a browser without storage forgets on reload */
      }
      return next;
    });
  }, []);
  return [state, set];
}

function RailRow({
  row,
  pathname,
  search,
  opened,
  onToggle,
  onNavigate,
  onPin,
}: {
  row: RailInstall;
  pathname: string;
  search: string | null;
  /** this row shows its chats — the same meaning for every row, the one on screen too */
  opened: boolean;
  onToggle: () => void;
  onNavigate?: () => void;
  onPin: () => void;
}) {
  const t = useAmT();
  const href = `/chat/agents/i/${row.id}`;
  const active = pathname === href || pathname.startsWith(`${href}/`);
  const pinned = !!row.pinned_at;
  const showChats = opened;
  const chatsId = useId();
  return (
    <>
    <li className="group relative flex items-center" data-testid="agent-rail-row" data-chats-open={showChats || undefined}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={showChats}
        aria-controls={chatsId}
        aria-label={showChats ? t("chat.collapse", { name: row.title }) : t("chat.expand", { name: row.title })}
        className="focus-ring absolute left-0.5 z-10 inline-flex h-7 w-5 items-center justify-center rounded-md text-text-muted hover:text-text-primary"
        data-testid="agent-rail-chats-toggle"
      >
        {showChats ? <ChevronDown width={12} height={12} aria-hidden /> : <ChevronRight width={12} height={12} aria-hidden />}
      </button>
      <Link
        href={href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        data-testid="agent-rail-install"
        className={cls(
          "focus-ring tap-44 flex min-w-0 flex-1 items-center gap-2.5 rounded-lg py-1.5 pl-5 pr-9 text-sm transition-colors hover:bg-bg-elevated hover:text-text-primary",
          active ? "bg-bg-elevated text-text-primary" : "text-text-secondary",
        )}
      >
        <AgentTile icon={row.icon} look={row} size={20} quiet={row.disabled} />
        <span className="min-w-0 flex-1 truncate">{row.title}</span>
        {row.disabled ? <span className="shrink-0 text-[10px] text-text-muted">{t("rail.disabled")}</span> : null}
        {row.waiting > 0 ? (
          <span
            className="inline-flex min-w-[1.25rem] shrink-0 items-center justify-center rounded-full bg-status-warning px-1.5 text-[10px] font-semibold tabular-nums text-bg-base"
            title={t("rail.attention")}
            data-testid="agent-rail-waiting"
          >
            {row.waiting}
            <span className="sr-only">{` ${t("rail.attention")}`}</span>
          </span>
        ) : null}
      </Link>
      <button
        type="button"
        onClick={onPin}
        aria-label={pinned ? t("rail.unpin", { name: row.title }) : t("rail.pin", { name: row.title })}
        aria-pressed={pinned}
        className={cls(
          "focus-ring absolute right-1 inline-flex h-7 w-7 items-center justify-center rounded-md text-text-muted transition-opacity hover:bg-bg-surface hover:text-text-primary focus:opacity-100 group-hover:opacity-100",
          pinned ? "opacity-100" : "opacity-0",
        )}
        data-testid="agent-rail-pin"
      >
        {pinned ? <PinOff width={13} height={13} aria-hidden /> : <Pin width={13} height={13} aria-hidden />}
      </button>
    </li>
    {showChats ? (
      <li id={chatsId}>
        <ChatRows installId={row.id} agentName={row.title} active={active} currentKey={currentChatKey(search)} onNavigate={onNavigate} />
      </li>
    ) : null}
    </>
  );
}

function SearchHits({
  query,
  installed,
  catalog,
  loading,
  onNavigate,
}: {
  query: string;
  installed: RailInstall[];
  catalog: CatalogAgent[];
  loading: boolean;
  onNavigate?: () => void;
}) {
  const t = useAmT();
  const locale = useAmLocale();
  if (!installed.length && !catalog.length) {
    return loading ? null : (
      <p className="px-3 py-1 text-[11px] text-text-muted" data-testid="agent-search-none">
        {t("rail.searchNone", { q: query })}
      </p>
    );
  }
  return (
    <div data-testid="agent-search-hits" aria-label={t("rail.searchAria")} role="group">
      <div className="px-3 py-1 text-[11px] font-medium tracking-wide text-text-muted">{t("rail.searchTitle")}</div>
      <ul className="flex flex-col gap-0.5">
        {installed.map((i) => (
          <li key={i.id}>
            <Link
              href={`/chat/agents/i/${i.id}`}
              onClick={onNavigate}
              className="focus-ring flex w-full items-center gap-2.5 rounded-lg py-1.5 pl-5 pr-3 text-sm text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
              data-testid="agent-search-installed"
            >
              <AgentTile icon={i.icon} look={i} size={20} />
              <span className="min-w-0 flex-1 truncate">{i.title}</span>
              {i.waiting > 0 ? (
                <span className="rounded-full bg-status-warning px-1.5 text-[10px] font-semibold tabular-nums text-bg-base">{i.waiting}</span>
              ) : null}
            </Link>
          </li>
        ))}
        {catalog.map((c) => (
          <li key={c.slug}>
            <Link
              href={`/chat/agents/${encodeURIComponent(c.slug)}`}
              onClick={onNavigate}
              className="focus-ring flex w-full items-center gap-2.5 rounded-lg py-1.5 pl-5 pr-3 text-sm text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
              data-testid="agent-search-catalog"
            >
              <AgentTile icon={c.manifest?.icon} look={c.manifest} size={20} />
              <span className="min-w-0 flex-1 truncate">{agentShortName(c.manifest, locale, c.name)}</span>
              <span className="shrink-0 text-[10px] text-text-muted">{t("rail.searchDetails")}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

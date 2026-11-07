// AM3 — the left rail's order and its search. Pure functions so the rules a
// person relies on (pinned first, then recently used, about five, and a run
// waiting for an answer never hidden) are tested without a DOM.

import type { CatalogAgent, RailInstall } from "./types";

/** About five agents in the rail; the rest behind "ดูทั้งหมด". */
export const RAIL_CAP = 5;

function when(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

/**
 * Pinned first (in the order they were pinned), then the most recently used.
 * Never alphabetical — nobody looks for an agent by its first letter. An
 * agent with a run waiting for an answer is always shown, even past the cap.
 */
export function railOrder(rows: RailInstall[], cap = RAIL_CAP): { shown: RailInstall[]; rest: number } {
  const pinned = rows.filter((r) => r.pinned_at).sort((a, b) => when(a.pinned_at) - when(b.pinned_at));
  const others = rows.filter((r) => !r.pinned_at).sort((a, b) => when(b.last_used_at) - when(a.last_used_at));
  const ordered = [...pinned, ...others];
  const shown = ordered.slice(0, cap);
  for (const r of ordered.slice(cap)) if (r.waiting > 0) shown.push(r);
  return { shown, rest: Math.max(0, rows.length - shown.length) };
}

export function totalWaiting(rows: RailInstall[]): number {
  return rows.reduce((n, r) => n + (r.waiting > 0 ? r.waiting : 0), 0);
}

function norm(s: string | undefined | null): string {
  return (s ?? "").toLocaleLowerCase("th").replace(/\s+/g, " ").trim();
}

/** Ctrl+K: installed agents that match first, then catalog agents not installed. */
export function searchAgents(
  q: string,
  installs: RailInstall[],
  catalog: CatalogAgent[],
): { installed: RailInstall[]; catalog: CatalogAgent[] } {
  const needle = norm(q);
  if (!needle) return { installed: [], catalog: [] };
  const hit = (...parts: (string | undefined | null)[]) => parts.some((p) => norm(p).includes(needle));
  const bySlug = new Map(catalog.map((c) => [c.slug, c]));
  const installed = installs.filter((i) => {
    const m = bySlug.get(i.slug)?.manifest;
    return hit(i.title, i.slug, m?.short_name, m?.name, bySlug.get(i.slug)?.name);
  });
  const mine = new Set(installs.map((i) => i.slug));
  const others = catalog.filter(
    (c) => !mine.has(c.slug) && c.status !== "unpublished" && hit(c.name, c.slug, c.manifest?.short_name, c.manifest?.summary),
  );
  return { installed: railOrder(installed, installed.length).shown, catalog: others.slice(0, 5) };
}

// ── AM24c: the order is settled once, and held ─────────────────────────────
// Recency is a good rule for arriving at the sidebar and a bad one for reacting to a click. Opening an agent writes
// its last_opened_at; the next read — the 30-second poll, or the window regaining focus — then moved that row to the
// top, under the person's pointer, while nobody touched anything. So railOrder decides once, at the page's first
// read, and later reads keep that order: titles, counts and badges stay fresh, positions do not change. Only what
// the person asks for moves a row — a pin or unpin moves that row, an install is listed after the others, an
// uninstall takes its row away.

/** The rail's order for the page: every install, in the order kept; the first `listed` are in the sidebar. */
export interface RailHold {
  order: string[];
  listed: number;
}

/** The row joins the listed ones, after them — no row above it moves. */
function listAfter(h: RailHold, id: string): RailHold {
  const at = h.order.indexOf(id);
  if (at >= 0 && at < h.listed) return h;
  const order = h.order.filter((x) => x !== id);
  order.splice(h.listed, 0, id);
  return { order, listed: h.listed + 1 };
}

/**
 * The order after a read. The page's first read (no hold, or none of its agents installed any more) takes
 * railOrder's. Every later read keeps the hold, less what was uninstalled; an install the page has not seen,
 * an agent with a run waiting for an answer and the agent on screen are listed if they are not yet — after the
 * others.
 */
export function settleRail(rows: RailInstall[], hold: RailHold | null, activeId: string | null = null, cap = RAIL_CAP): RailHold {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const all = railOrder(rows, rows.length).shown.map((r) => r.id);
  let h: RailHold;
  if (!hold || !hold.order.some((id) => byId.has(id))) {
    const first = railOrder(rows, cap).shown.map((r) => r.id);
    h = { order: [...first, ...all.filter((id) => !first.includes(id))], listed: first.length };
  } else {
    const order: string[] = [];
    let listed = 0;
    hold.order.forEach((id, i) => {
      if (!byId.has(id)) return;
      order.push(id);
      if (i < hold.listed) listed += 1;
    });
    h = { order, listed };
    const seen = new Set(hold.order);
    for (const id of all) if (!seen.has(id) || byId.get(id)!.waiting > 0) h = listAfter(h, id);
  }
  if (activeId && byId.has(activeId)) h = listAfter(h, activeId);
  return h;
}

/**
 * A pin or unpin the person made: pinned agents first, in the order they were pinned (railOrder's rule); the
 * others keep their order, so an agent just unpinned comes first below the pins. A pinned agent not listed yet is.
 */
export function placePins(h: RailHold, rows: RailInstall[], id: string): RailHold {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const pinned = (x: string) => !!byId.get(x)?.pinned_at;
  const x = pinned(id) ? listAfter(h, id) : h;
  const listed = x.order.slice(0, x.listed);
  const pins = listed.filter(pinned).sort((a, b) => when(byId.get(a)!.pinned_at) - when(byId.get(b)!.pinned_at));
  return { order: [...pins, ...listed.filter((y) => !pinned(y)), ...x.order.slice(x.listed)], listed: x.listed };
}

export function sameHold(a: RailHold | null, b: RailHold | null): boolean {
  return !!a && !!b && a.listed === b.listed && a.order.length === b.order.length && a.order.every((id, i) => b.order[i] === id);
}

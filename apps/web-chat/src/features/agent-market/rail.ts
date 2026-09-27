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

/** The agents a collapsed section still shows: only those waiting for an answer. */
export function waitingOnly(rows: RailInstall[]): RailInstall[] {
  return railOrder(rows, rows.length).shown.filter((r) => r.waiting > 0);
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

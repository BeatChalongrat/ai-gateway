// Agent workspace thread — pure merge/order rules.
//
// The server streams every change of a thread item as the SAME id with a
// higher rev (a card being answered, a progress card ticking). The client
// keeps one copy per id: the highest rev wins, a lower or equal rev is stale.
//
// Order is created_at, then rev. One refinement: rows written in one
// transaction share created_at (Postgres now() is the transaction's start),
// and the composer's own message is always the first row of its transaction
// while its rev is bumped last (it is linked to the run after the run's
// progress card exists). So on a created_at tie the person's message comes
// first — otherwise the progress card would sit above the message that
// started it.

import type { ThreadItem } from "./types";

const USER_KINDS = new Set(["user_text", "user_files"]);

/** created_at as a sortable number with microsecond resolution
 *  (Date.parse keeps milliseconds only; Go sends up to nanoseconds). */
export function timeKey(iso: string): number {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return 0;
  const frac = /\.(\d+)/.exec(iso)?.[1] ?? "";
  const micros = Number((frac + "000000").slice(3, 6));
  return ms * 1000 + micros;
}

export function compareItems(a: ThreadItem, b: ThreadItem): number {
  const t = timeKey(a.created_at) - timeKey(b.created_at);
  if (t !== 0) return t;
  const ua = USER_KINDS.has(a.kind) ? 0 : 1;
  const ub = USER_KINDS.has(b.kind) ? 0 : 1;
  if (ua !== ub) return ua - ub;
  return a.rev - b.rev;
}

export function sortItems(items: ThreadItem[]): ThreadItem[] {
  return [...items].sort(compareItems);
}

/** Merge `incoming` into `current` by id, keeping the highest rev.
 *  Returns `current` itself when nothing changed (cheap React bail-out). */
export function mergeItems(current: ThreadItem[], incoming: ThreadItem[]): ThreadItem[] {
  if (incoming.length === 0) return current;
  const byId = new Map(current.map((i) => [i.id, i] as const));
  let changed = false;
  for (const it of incoming) {
    if (!it || !it.id) continue;
    const prev = byId.get(it.id);
    if (!prev || it.rev > prev.rev) {
      byId.set(it.id, it);
      changed = true;
    }
  }
  return changed ? sortItems([...byId.values()]) : current;
}

/** The resume point for the event stream: the highest rev seen. */
export function maxRev(items: ThreadItem[], floor = 0): number {
  let m = floor;
  for (const i of items) if (i.rev > m) m = i.rev;
  return m;
}

export function isUserItem(item: ThreadItem): boolean {
  return USER_KINDS.has(item.kind);
}

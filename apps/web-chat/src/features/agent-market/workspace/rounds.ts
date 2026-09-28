// AM4 — rounds in the workspace thread (pure; tested in rounds.test.ts).
//
// A round is one run of the agent (a progress card of kind "run" or
// "self_test"). A finished round is never cleared: the thread closes it with a
// divider — "รอบ กันยายน 2569 · เสร็จ", its files, and "เริ่มรอบใหม่" right
// there. Older rounds fold into that divider (open on demand), so the latest
// one reads without scrolling past every month before it. A self-test folds
// as soon as it has finished: it is the sample's round, not the person's.

import type { IntentPayload, NoticePayload, OutputArtifact, OutputsPayload, ProgressPayload, ThreadItem } from "../types";

export interface RoundInfo {
  runId: string;
  kind: "run" | "self_test" | string;
  status: string;
  period: string;
  artifacts: OutputArtifact[];
  /** the item after which the divider sits: the files card, or the progress card of a round that ended without one */
  endItemId: string | null;
}

export type Segment =
  | { type: "item"; item: ThreadItem }
  | { type: "folded"; round: RoundInfo; items: ThreadItem[] }
  | { type: "divider"; round: RoundInfo; latest: boolean; started: boolean };

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export function roundsOf(items: ThreadItem[]): Map<string, RoundInfo> {
  const rounds = new Map<string, RoundInfo>();
  for (const it of items) {
    if (it.kind !== "progress") continue;
    const p = (it.payload ?? {}) as ProgressPayload;
    const kind = p.kind ?? "run";
    if (!p.run_id || (kind !== "run" && kind !== "self_test")) continue;
    rounds.set(p.run_id, { runId: p.run_id, kind, status: p.status, period: "", artifacts: [], endItemId: it.id });
  }
  for (const it of items) {
    if (it.kind !== "outputs") continue;
    const p = (it.payload ?? {}) as OutputsPayload;
    const r = p.run_id ? rounds.get(p.run_id) : undefined;
    if (!r) continue;
    r.period = p.period_label || p.summary?.period_label || r.period;
    // a later files card (a re-render, a sign-off) replaces the files it re-issued
    const byFormat = new Map(r.artifacts.map((a) => [a.format, a] as const));
    for (const a of p.artifacts ?? []) byFormat.set(a.format, a);
    r.artifacts = [...byFormat.values()];
    r.endItemId = it.id;
  }
  return rounds;
}

/** The round an item belongs to, by position: from a round's progress card up
 *  to the next round's first item. Items before the first round (the greeting)
 *  belong to none. */
function positions(items: ThreadItem[], rounds: Map<string, RoundInfo>): (string | null)[] {
  // a round starts at its first item carrying its run id (the person's message
  // linked to it, else its progress card)
  const firstIndex = new Map<string, number>();
  items.forEach((it, i) => {
    const rid = it.run_id ?? (it.payload as { run_id?: string } | null)?.run_id;
    if (rid && rounds.has(rid) && !firstIndex.has(rid)) firstIndex.set(rid, i);
  });
  const starts = [...firstIndex.entries()].sort((a, b) => a[1] - b[1]);
  const out: (string | null)[] = new Array(items.length).fill(null);
  starts.forEach(([rid, start], k) => {
    const end = k + 1 < starts.length ? starts[k + 1]![1] : items.length;
    for (let i = start; i < end; i++) out[i] = rid;
  });
  return out;
}

/** The thread as segments: items, folded older rounds, and dividers. */
export function segments(items: ThreadItem[]): Segment[] {
  const rounds = roundsOf(items);
  const owner = positions(items, rounds);
  const order = [...new Set(owner.filter((x): x is string => !!x))];
  const lastRun = [...order].reverse().find((rid) => rounds.get(rid)?.kind === "run") ?? null;
  const folded = new Set(
    order.filter((rid) => {
      const r = rounds.get(rid)!;
      if (!TERMINAL.has(r.status)) return false;
      return r.kind === "self_test" || rid !== lastRun;
    }),
  );
  // "เริ่มรอบใหม่" already pressed after the latest round ended
  let started = false;
  if (lastRun) {
    const endAt = items.findIndex((it) => it.id === rounds.get(lastRun)!.endItemId);
    started = items.some(
      (it, i) => i > endAt && it.kind === "notice" && !!(it.payload as NoticePayload | null)?.round_start,
    );
  }

  const out: Segment[] = [];
  const bucket = new Map<string, ThreadItem[]>();
  items.forEach((it, i) => {
    const rid = owner[i];
    if (rid && folded.has(rid)) {
      let b = bucket.get(rid);
      if (!b) {
        b = [];
        bucket.set(rid, b);
        out.push({ type: "folded", round: rounds.get(rid)!, items: b });
      }
      b.push(it);
      return;
    }
    out.push({ type: "item", item: it });
    const r = rid ? rounds.get(rid) : undefined;
    if (r && r.kind === "run" && TERMINAL.has(r.status) && r.endItemId === it.id) {
      out.push({ type: "divider", round: r, latest: rid === lastRun, started: rid === lastRun && started });
    }
  });
  return out;
}

/** The workspace has not run the agent on a person's own files yet. */
export function hasOwnRound(items: ThreadItem[]): boolean {
  return [...roundsOf(items).values()].some((r) => r.kind === "run");
}

/** The latest round of kind run with its status (for the header's "ที่มาของตัวเลข"). */
export function latestRound(items: ThreadItem[]): RoundInfo | null {
  let last: RoundInfo | null = null;
  const rounds = roundsOf(items);
  for (const it of items) {
    if (it.kind !== "progress") continue;
    const r = rounds.get((it.payload as ProgressPayload)?.run_id ?? "");
    if (r && r.kind === "run") last = r;
  }
  return last;
}

/** An open round-boundary chip in the thread. */
export function openIntent(items: ThreadItem[]): ThreadItem | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]!;
    if (it.kind === "intent" && (it.payload as IntentPayload | null)?.status === "open") return it;
  }
  return null;
}

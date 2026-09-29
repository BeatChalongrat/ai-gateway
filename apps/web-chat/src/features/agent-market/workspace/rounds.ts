// AM4 — rounds in the workspace thread (pure; tested in rounds.test.ts).
//
// A round is one run of the agent (a progress card of kind "run" or
// "self_test"). A finished round is never cleared: the thread closes it with a
// divider — "รอบ กันยายน 2569 · เสร็จ", its files, and "เริ่มรอบใหม่" right
// there. Older rounds fold into that divider (open on demand), so the latest
// one reads without scrolling past every month before it. A self-test folds
// as soon as it has finished: it is the sample's round, not the person's.

import { outcomeOf } from "../outcome";
import type { IntentPayload, NoticePayload, OutputArtifact, OutputsPayload, ProgressPayload, ThreadItem } from "../types";

export interface RoundInfo {
  runId: string;
  kind: "run" | "self_test" | string;
  status: string;
  /** AM6: what the round came to (the status, from a server that sends no outcome) and its reason's code */
  outcome: string;
  reason: string;
  period: string;
  artifacts: OutputArtifact[];
  /** AM5: how many documents the round's newest files card still lists as left out */
  notIncluded: number;
  /** the item after which the divider sits: the round's last item (files card, closing words, answers about it) */
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
    rounds.set(p.run_id, {
      runId: p.run_id,
      kind,
      status: p.status,
      outcome: outcomeOf(p),
      reason: p.reason ?? "",
      period: "",
      artifacts: [],
      notIncluded: 0,
      endItemId: it.id,
    });
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
    // a files card made from a computed table carries the round's list; a re-render or a sign-off does not
    if (p.summary && Array.isArray(p.summary.not_included)) r.notIncluded = p.summary.not_included.length;
  }
  // the divider closes a round after its last item: the files card, the
  // runner's closing words, answers to questions about it (all carry its id)
  // — until "เริ่มรอบใหม่" closes it for good
  const begun = new Set<string>();
  const closed = new Set<string>();
  for (const it of items) {
    const rid = it.run_id ?? (it.payload as { run_id?: string } | null)?.run_id;
    const r = rid ? rounds.get(rid) : undefined;
    if (r && !closed.has(r.runId)) r.endItemId = it.id;
    if (it.kind === "progress" && r) begun.add(r.runId);
    if (it.kind === "notice" && (it.payload as NoticePayload | null)?.round_start) {
      for (const b of begun) if (TERMINAL.has(rounds.get(b)!.status)) closed.add(b);
    }
  }
  return rounds;
}

/** The round each item belongs to. An item carrying a round's run id is that
 *  round's, wherever it sits (a self-test that started after the person's
 *  round must not take the person's card and files with it when it folds).
 *  An item without one (the greeting, a notice, a typed question) belongs to
 *  the person's round begun most recently before it — a self-test's only when
 *  no round of the person's has begun yet. Items before any round belong to none. */
function positions(items: ThreadItem[], rounds: Map<string, RoundInfo>): (string | null)[] {
  const out: (string | null)[] = new Array(items.length).fill(null);
  const begun = new Set<string>();
  let currentRun: string | null = null;
  let currentSelfTest: string | null = null;
  items.forEach((it, i) => {
    const rid = it.run_id ?? (it.payload as { run_id?: string } | null)?.run_id;
    const r = rid ? rounds.get(rid) : undefined;
    if (r) {
      out[i] = r.runId;
      if (!begun.has(r.runId)) {
        begun.add(r.runId);
        if (r.kind === "run") currentRun = r.runId;
        else currentSelfTest = r.runId;
      }
      return;
    }
    out[i] = currentRun ?? currentSelfTest;
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
  // "เริ่มรอบใหม่" already pressed after the latest round ended — or an open
  // chip is asking that very question: the divider does not offer it twice
  let started = openIntent(items) !== null;
  if (lastRun) {
    const endAt = items.findIndex((it) => it.id === rounds.get(lastRun)!.endItemId);
    started ||= items.some(
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

/** AM6: the agent is at work in this thread — a run of any kind is queued,
 *  running or waiting for an answer (the composer's suggestions step aside). */
export function roundUnderway(items: ThreadItem[]): boolean {
  return items.some((it) => {
    if (it.kind !== "progress") return false;
    const p = (it.payload ?? {}) as ProgressPayload;
    return !!p.run_id && !TERMINAL.has(p.status);
  });
}

/** AM6: the person's latest round has ended and left files: there are
 *  figures to ask about and another format to ask for. */
export function hasFinishedRound(items: ThreadItem[]): boolean {
  const last = latestRound(items);
  return !!last && TERMINAL.has(last.status) && last.artifacts.length > 0;
}

/** An open round-boundary chip in the thread. */
export function openIntent(items: ThreadItem[]): ThreadItem | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]!;
    if (it.kind === "intent" && (it.payload as IntentPayload | null)?.status === "open") return it;
  }
  return null;
}

/** AM5: the files cards a newer one of the same round has replaced (their
 *  "เอกสารที่ยังไม่ได้รวม" list is history; the newest card's is the round's). */
export function supersededOutputs(items: ThreadItem[]): Set<string> {
  const newest = new Map<string, string>();
  for (const it of items) {
    if (it.kind !== "outputs") continue;
    const p = (it.payload ?? {}) as OutputsPayload;
    if (p.run_id && p.summary && Array.isArray(p.summary.not_included)) newest.set(p.run_id, it.id);
  }
  const out = new Set<string>();
  for (const it of items) {
    if (it.kind !== "outputs") continue;
    const p = (it.payload ?? {}) as OutputsPayload;
    if (p.run_id && newest.has(p.run_id) && newest.get(p.run_id) !== it.id) out.add(it.id);
  }
  return out;
}

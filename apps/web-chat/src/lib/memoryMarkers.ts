// FIX130 A3b — the memory marker on an assistant answer:
//
//   · "บันทึกความจำแล้ว" / "เสนอความจำใหม่ — รอยืนยัน" — a save_memory call
//     during this turn stored a memory (active) or queued a suggestion.
//
// FIX132 A — the "ใช้ความจำ N รายการ" (memory USED) marker is gone: the page
// no longer badges answers for using memory, so nothing here reads the
// `memory_used` frame or the `saved_memory` context sources any more.
//
// It has two sources that must mean the same thing: the live SSE frame
// (`memory_saved`, from chat-edge or — the same frame over the bridge — from
// the desktop host) when the page watched the turn, and the persisted
// context_sources rows (`memory_saved`) after a reload. Same shape as
// lossNoticeFromSources (lib/compaction.ts): the live field wins, the row is
// the fallback. Pure, so both paths are pinned by one test file
// (memoryMarkers.test.ts).

import type { ContextSource, Message } from "./types";

export type MemorySavedStatus = "active" | "pending_confirmation";

export interface MemorySavedItem {
  id: string;
  label: string;
  status: MemorySavedStatus;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function isSavedStatus(v: unknown): v is MemorySavedStatus {
  return v === "active" || v === "pending_confirmation";
}

/** Folds one live `memory_saved` frame into what the row already has. A turn
 *  can save more than once; the same memory arriving again (a replayed
 *  ledger frame) updates in place rather than doubling. Returns `prev`
 *  unchanged when the frame carries nothing usable. */
export function mergeMemorySaved(
  prev: MemorySavedItem[] | null | undefined,
  data: unknown,
): MemorySavedItem[] | null {
  const raw = isRecord(data) && Array.isArray(data["items"]) ? data["items"] : [];
  const incoming: MemorySavedItem[] = [];
  for (const it of raw) {
    if (isRecord(it) && typeof it["id"] === "string" && typeof it["label"] === "string" && isSavedStatus(it["status"])) {
      incoming.push({ id: it["id"], label: it["label"], status: it["status"] });
    }
  }
  if (incoming.length === 0) return prev ?? null;
  const out = [...(prev ?? [])];
  for (const it of incoming) {
    const at = out.findIndex((x) => x.id === it.id);
    if (at >= 0) out[at] = it;
    else out.push(it);
  }
  return out;
}

/** After a reload: the memory_saved rows, with the status chat-edge wrote. */
export function memorySavedFromSources(sources: ContextSource[] | null | undefined): MemorySavedItem[] | null {
  const items: MemorySavedItem[] = [];
  for (const s of sources ?? []) {
    if (s.source_type !== "memory_saved") continue;
    const status = s.metadata?.["status"];
    if (!isSavedStatus(status)) continue;
    const id = s.source_id ?? s.id;
    if (items.some((x) => x.id === id)) continue;
    items.push({ id, label: s.display_label, status });
  }
  return items.length > 0 ? items : null;
}

/** The saved-marker for a row: the live frames if the page saw them, else the row's sources. */
export function messageMemorySaved(m: Pick<Message, "memory_saved" | "context_sources">): MemorySavedItem[] | null {
  return m.memory_saved ?? memorySavedFromSources(m.context_sources);
}

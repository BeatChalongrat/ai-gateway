// FIX33 — the compaction UI's numbers, the SAME on web and desktop
// (apps/desktop/src/main/conversation-state.ts holds the same values; the
// desktop test byte-checks this file so the two can't drift).
//
// Spec addendum #1: no consumer chat app shows a permanent context meter.
// Ours is HIDDEN until the conversation has used CONTEXT_METER_SHOW_RATIO of
// its budget; from there a quiet line + "summarize and continue" appear.

import type { ContextInfo, ContextSource } from "./types";

/** Meter + compact button appear from here. */
export const CONTEXT_METER_SHOW_RATIO = 0.7;
/** Auto-compact trigger (chat-edge CONTEXT_COMPACT_TRIGGER_RATIO) — informational for the meter copy. */
export const COMPACT_TRIGGER_RATIO = 0.85;

/**
 * FIX102 — older messages are being left out of what is sent (or were never
 * loaded). Once that is routine a fill gauge is permanently full and says
 * nothing, so the meter states what is sent instead of a percent.
 */
export function isTrimming(info: ContextInfo): boolean {
  return info.compacted === true || (info.omitted_older ?? 0) > 0;
}

/** Whether the header should show anything at all for this info. */
export function shouldShowMeter(info: ContextInfo | null | undefined): boolean {
  if (!info) return false;
  if (isTrimming(info)) return true;
  const show = typeof info.meter_show_ratio === "number" && info.meter_show_ratio > 0 ? info.meter_show_ratio : CONTEXT_METER_SHOW_RATIO;
  if (typeof info.show_meter === "boolean") return info.show_meter;
  return info.used_ratio >= show;
}

/** Whole-number percent of what will be sent, never above 100 (a desktop build older than FIX102 still sends the loaded ratio). */
export function meterPercent(info: ContextInfo): number {
  return Math.min(100, Math.max(0, Math.round(info.used_ratio * 100)));
}

/**
 * FIX102 Part C — the persisted compacted_history source records whether a
 * summary stood in for the messages a reply left out. The live
 * compaction_notice is gone after a reload; this is the same fact from the
 * database, so the line under the reply survives it.
 */
export function lossNoticeFromSources(sources: ContextSource[] | null | undefined): { dropped: number; summary: "failed" | "partial" } | null {
  for (const s of sources ?? []) {
    if (s.source_type !== "compacted_history") continue;
    const summary = s.metadata?.["summary"];
    const missing = s.metadata?.["missing"];
    if ((summary === "failed" || summary === "partial") && typeof missing === "number" && missing > 0) {
      return { dropped: missing, summary };
    }
  }
  return null;
}

/** Conversation messages the assistant sees this turn — without the injected summary pair — and how many there are. */
export function messagesSeen(info: ContextInfo): { sent: number; total: number } {
  const sentRaw = info.messages_would_send ?? info.messages_sent ?? info.messages_total;
  const sent = Math.max(0, sentRaw - (info.state_injected ? 2 : 0));
  return { sent, total: info.messages_total + (info.omitted_older ?? 0) };
}

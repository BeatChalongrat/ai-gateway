// FIX33 — the compaction UI's numbers, the SAME on web and desktop
// (apps/desktop/src/main/conversation-state.ts holds the same values; the
// desktop test byte-checks this file so the two can't drift).
//
// Spec addendum #1: no consumer chat app shows a permanent context meter.
// Ours is HIDDEN until the conversation has used CONTEXT_METER_SHOW_RATIO of
// its budget; from there a quiet line + "summarize and continue" appear.

import type { ContextInfo } from "./types";

/** Meter + compact button appear from here. */
export const CONTEXT_METER_SHOW_RATIO = 0.7;
/** Auto-compact trigger (chat-edge CONTEXT_COMPACT_TRIGGER_RATIO) — informational for the meter copy. */
export const COMPACT_TRIGGER_RATIO = 0.85;

/** Whether the header should show anything at all for this info. */
export function shouldShowMeter(info: ContextInfo | null | undefined): boolean {
  if (!info) return false;
  const show = typeof info.meter_show_ratio === "number" && info.meter_show_ratio > 0 ? info.meter_show_ratio : CONTEXT_METER_SHOW_RATIO;
  if (typeof info.show_meter === "boolean") return info.show_meter;
  return info.used_ratio >= show;
}

/** Whole-number percent, capped at 100 for the copy (the ratio itself may exceed 1 before a trim). */
export function meterPercent(info: ContextInfo): number {
  return Math.min(100, Math.max(0, Math.round(info.used_ratio * 100)));
}

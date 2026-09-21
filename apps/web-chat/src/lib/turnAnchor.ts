// P3 — send-time turn anchoring (the ChatGPT/Claude pattern, and what
// shadcn/ui ships as MessageScroller): when the user sends, the new turn
// scrolls to sit NEAR THE TOP of the viewport with a small peek of the
// previous message above it, so the reply streams downward into open
// space instead of crawling up from the composer.
//
// Pure math only — ChatThread (web) owns the DOM, and the desktop app
// renders this same web bundle (Electron loads /chat from the web-chat
// origin), so there is ONE implementation to change. A C# port,
// SranDesktop.Application.Services.TurnAnchorMath, still sits in the
// retired WPF tree with its tests; nothing that ships calls it, and it
// is not kept in sync.

/** Sliver of the PREVIOUS message kept visible above the anchored turn.
 *  THE one tuning knob for the anchor feel (shadcn's
 *  scrollPreviousItemPeek default). QA: change this single number —
 *  e.g. to ~1/3 of the viewport height — nothing else needs touching. */
export const ANCHOR_PREVIOUS_PEEK_PX = 64;

/** A local action, consumed once by the thread; loading history is not a send. */
export interface TurnScrollRequest {
  id: string;
  kind: "send" | "regenerate";
  userKey: string;
  replyKey: string;
}

/**
 * Scroll offset that puts the sent message's top `peek` pixels below the
 * viewport top. The peek is a MAXIMUM: when the previous message is
 * shorter than `peek`, the target clamps to that message's own top
 * (show whatever there is, never overscroll past it), and with no
 * previous message at all the turn lands flush at the top.
 */
export function anchorTarget(
  userTop: number,
  prevTop: number | null,
  peek: number,
): number {
  return Math.max(0, Math.max(prevTop ?? userTop, userTop - peek));
}

/**
 * Height of the bottom spacer that makes `target` reachable: the part of
 * the viewport below the existing content when scrolled to `target`.
 * Recomputed as the reply streams in, so it shrinks to zero on its own
 * once real content fills the room. Only valid during a live hold;
 * terminal completion releases it even if the reply never fills the room.
 * Never negative, never cumulative —
 * always derived fresh from the current geometry.
 */
export function spacerNeeded(
  target: number,
  viewportHeight: number,
  contentHeight: number,
): number {
  return Math.max(0, target + viewportHeight - contentHeight);
}

/**
 * Phase handover: once the streamed reply has filled the viewport below
 * the anchor (real content reaches the viewport bottom), the hold ends
 * and the pre-existing stick-to-bottom behaviour takes over. THE one
 * line to change if QA wants a different handover policy.
 */
export function shouldResumeFollow(
  contentHeight: number,
  scrollTop: number,
  viewportHeight: number,
): boolean {
  return contentHeight >= scrollTop + viewportHeight - 1;
}

// ─── FIX122 Part A — the reader's landmark ─────────────────────────────
//
// When the thread is neither following the bottom nor holding a sent turn,
// the person is READING something, and whatever happens above it — a page of
// older messages arriving, a virtualized row turning out taller than its
// estimate — must not move it. The thread remembers one row and how far its
// top is from the viewport's top, and after every layout change puts it back
// (Telegram Web's MessageList does the same with getBoundingClientRect).
// Doing it by hand, not with the browser's overflow-anchor: CSS Scroll
// Anchoring §2.1 selects no anchor at scroll offset 0, and the top edge is
// exactly where older messages are loaded from.

/** A rendered row, in viewport coordinates (0 = the scroller's top edge). */
export interface RowBox {
  key: string;
  top: number;
  bottom: number;
}

export interface ReadingLandmark {
  key: string;
  /** The row's top, relative to the viewport top, when it was remembered. */
  offset: number;
}

/**
 * The row to hold still: the first row whose TOP is inside the viewport.
 *
 * Not the first row that merely reaches into it. A row is pinned by its top,
 * so a row cut by the viewport's top edge that then GROWS (its first real
 * measurement, an image loading) grows downward and pushes everything the
 * person can see, while the pin — off-screen — reports that nothing moved.
 * That was the measured jump after loading older messages: 141–388 px.
 * A row that starts inside the viewport has all of its "above" out of sight.
 * Only when no row starts in view (one message taller than the window) is
 * the cut row the landmark: it is all there is to look at.
 */
export function pickReadingLandmark(rows: readonly RowBox[], viewportHeight: number): ReadingLandmark | null {
  let cut: RowBox | null = null;
  for (const row of rows) {
    if (row.bottom <= 1) continue; // wholly above
    if (row.top >= viewportHeight) break; // wholly below — and so is the rest
    if (row.top >= -1) return { key: row.key, offset: row.top };
    cut ??= row;
  }
  return cut ? { key: cut.key, offset: cut.top } : null;
}

/**
 * The scrollTop that puts the landmark back: it was `saved` px below the
 * viewport top, it is now `current` px below it. Clamped to what the content
 * can reach; null when it has not moved (within a pixel) — no write, no
 * scroll event.
 */
export function landmarkCorrection(
  scrollTop: number,
  saved: number,
  current: number,
  maxScrollTop: number,
): number | null {
  if (Math.abs(current - saved) <= 1) return null;
  return Math.max(0, Math.min(maxScrollTop, scrollTop + current - saved));
}

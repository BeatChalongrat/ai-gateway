// P3 — send-time turn anchoring (the ChatGPT/Claude pattern, and what
// shadcn/ui ships as MessageScroller): when the user sends, the new turn
// scrolls to sit NEAR THE TOP of the viewport with a small peek of the
// previous message above it, so the reply streams downward into open
// space instead of crawling up from the composer.
//
// Pure math only — ChatThread (web) owns the DOM. The desktop twin is
// SranDesktop.Application.Services.TurnAnchorMath; keep the two in sync.

/** Sliver of the PREVIOUS message kept visible above the anchored turn.
 *  THE one tuning knob for the anchor feel (shadcn's
 *  scrollPreviousItemPeek default). QA: change this single number —
 *  e.g. to ~1/3 of the viewport height — nothing else needs touching. */
export const ANCHOR_PREVIOUS_PEEK_PX = 64;

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
 * once real content fills the room. Never negative, never cumulative —
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
  return contentHeight >= scrollTop + viewportHeight - 0.5;
}

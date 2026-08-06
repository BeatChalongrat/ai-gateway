// Phase 7 — per-conversation scroll memory.
//
// Switching to another chat and back used to always land at the
// bottom, losing the user's place in a long thread. We remember the
// last scroll offset per conversation for the lifetime of the tab.
//
// Deliberately IN-MEMORY ONLY (no localStorage/sessionStorage —
// artifact rule, and a stale offset restored days later would point at
// the wrong content anyway). A hard reload starts fresh at the bottom,
// which is the expected behaviour for a chat app.

const scrollByConversation = new Map<string, number>();

/** Bounded so a very long session can't grow the map without limit. */
const MAX_REMEMBERED = 50;

export function rememberScroll(convId: string, top: number): void {
  if (!convId) return;
  // Re-insert so the Map's insertion order doubles as an LRU list.
  scrollByConversation.delete(convId);
  scrollByConversation.set(convId, Math.max(0, Math.round(top)));
  if (scrollByConversation.size > MAX_REMEMBERED) {
    const oldest = scrollByConversation.keys().next().value;
    if (oldest !== undefined) scrollByConversation.delete(oldest);
  }
}

/** Saved offset, or null when this conversation has none (→ caller
 *  keeps the default "start at the bottom" behaviour). */
export function recallScroll(convId: string): number | null {
  if (!convId) return null;
  const v = scrollByConversation.get(convId);
  return v === undefined ? null : v;
}

export function forgetScroll(convId: string): void {
  scrollByConversation.delete(convId);
}

/** Test hook. */
export function clearScrollMemory(): void {
  scrollByConversation.clear();
}

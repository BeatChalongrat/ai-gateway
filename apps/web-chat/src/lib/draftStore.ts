// Per-conversation composer drafts (Batch 1 W1 — port of the desktop
// "Composer draft persistence" behaviour, ChatViewModel.cs Phase 3 C4).
//
// Typed-but-unsent text survives conversation switches and reloads:
// ChatInput debounces (~1s) a save while typing, flushes immediately on
// switch/unmount, deletes on send. Invisible beyond the text reappearing.
//
// Persisted in localStorage: a draft is text the user wrote themselves, so
// restoring it days later is exactly what they expect (this matches
// desktop's SQLite drafts). Note the contrast with scroll position, which
// is deliberately NOT remembered — opening a conversation always lands at
// the newest turn.
// All entries live under ONE key as a JSON map so the LRU cap can see
// every draft at once; failures (quota, disabled storage, corrupt JSON)
// are swallowed — losing a draft save must never break the composer.

const STORAGE_KEY = "sran-webchat-composer-drafts";

/** Bounded so a heavy user can't grow storage without limit. */
const MAX_DRAFTS = 50;

/** Bucket for text typed on the landing page before a conversation id
 *  exists. Migrated onto the real id when the eager-create resolves
 *  (see ChatInput's draft-key effect). */
export const NEW_CHAT_DRAFT_KEY = "__new_chat__";

interface DraftEntry {
  /** The draft text. */
  t: string;
  /** Last-write epoch ms — the LRU eviction key. */
  at: number;
}

type DraftMap = Record<string, DraftEntry>;

function readAll(): DraftMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as DraftMap;
    }
  } catch {
    /* corrupt entry — treat as empty */
  }
  return {};
}

function writeAll(map: DraftMap): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* quota or disabled storage — drop the save, never throw */
  }
}

/** Saved draft text, or null when this conversation has none. */
export function loadDraft(convKey: string): string | null {
  if (!convKey) return null;
  const entry = readAll()[convKey];
  return entry && typeof entry.t === "string" && entry.t.length > 0
    ? entry.t
    : null;
}

/** Persist `text` for the conversation. Empty / whitespace-only text
 *  deletes the entry instead — clearing the composer clears the draft
 *  without callers having to special-case it. */
export function saveDraft(convKey: string, text: string): void {
  if (!convKey) return;
  if (!text.trim()) {
    clearDraft(convKey);
    return;
  }
  const map = readAll();
  map[convKey] = { t: text, at: Date.now() };
  // LRU eviction: drop the oldest entries beyond the cap.
  const keys = Object.keys(map);
  if (keys.length > MAX_DRAFTS) {
    keys
      .sort((a, b) => (map[a].at ?? 0) - (map[b].at ?? 0))
      .slice(0, keys.length - MAX_DRAFTS)
      .forEach((k) => delete map[k]);
  }
  writeAll(map);
}

export function clearDraft(convKey: string): void {
  if (!convKey) return;
  const map = readAll();
  if (!(convKey in map)) return;
  delete map[convKey];
  writeAll(map);
}

/** Move a draft between keys — used when the landing page's
 *  eager-create resolves and the NEW_CHAT bucket becomes a real
 *  conversation id. The destination wins nothing: the caller re-saves
 *  the live composer text right after, this just removes the stale
 *  source entry so the map never holds the same text twice. */
export function migrateDraft(fromKey: string, toKey: string): void {
  if (!fromKey || !toKey || fromKey === toKey) return;
  const map = readAll();
  const entry = map[fromKey];
  if (!entry) return;
  delete map[fromKey];
  map[toKey] = { ...entry, at: Date.now() };
  writeAll(map);
}

/** Test hook. */
export function clearDraftStore(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing */
  }
}

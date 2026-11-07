// FIX122 Part C — the send outbox.
//
// The composer empties the moment the person sends. From then until the
// server has taken the message, this is the only place the text exists —
// so it is written here, on the machine, BEFORE the request leaves (the
// order Matrix clients use for their send queue). Two ways out:
//
//   accepted  the server answered the send with a stream → the entry is
//             dropped; the message is the server's now.
//   failed    the request never got an answer, or got a refusal → the text
//             goes back where the person can send it again: into the
//             composer, and into the conversation's draft so a reload or a
//             remount in between cannot lose it.
//
// One entry per conversation: a conversation has one send in flight at a
// time (chatStore's sendsOpening). Same storage discipline as draftStore:
// one JSON map under one key, every failure swallowed — losing the outbox
// must never break a send.

import { loadDraft, saveDraft } from "./draftStore";

const STORAGE_KEY = "sran-webchat-send-outbox";

/** An entry older than this is not an interrupted send any more; it is
 *  something the person has long since dealt with some other way. */
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface OutboxEntry {
  /** The send's request id (the turn's run id and idempotency key). */
  requestId: string;
  text: string;
  at: number;
}

type OutboxMap = Record<string, OutboxEntry>;

function readAll(): OutboxMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as OutboxMap;
  } catch {
    /* corrupt — treat as empty */
  }
  return {};
}

function writeAll(map: OutboxMap): void {
  if (typeof window === "undefined") return;
  try {
    if (Object.keys(map).length === 0) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* quota or disabled storage */
  }
}

/** Record the text before the request leaves. Text-less sends (files only)
 *  have nothing to give back and are not recorded. */
export function outboxPut(convId: string, requestId: string, text: string): void {
  if (!convId || !text.trim()) return;
  const map = readAll();
  map[convId] = { requestId, text, at: Date.now() };
  writeAll(map);
}

/** The server took the message (or the person withdrew it): forget it.
 *  Only the send that wrote the entry may drop it. */
export function outboxAccepted(convId: string, requestId: string): void {
  const map = readAll();
  if (map[convId]?.requestId !== requestId) return;
  delete map[convId];
  writeAll(map);
}

export function outboxPeek(convId: string): OutboxEntry | null {
  const entry = readAll()[convId];
  if (!entry || typeof entry.text !== "string") return null;
  if (Date.now() - (entry.at ?? 0) > MAX_AGE_MS) return null;
  return entry;
}

/** The send failed: move the text to the conversation's draft and hand it
 *  back. A draft the person has written since is theirs and is not
 *  replaced — then nothing is handed back (the failed text is still in the
 *  thread, in the bubble that says it failed). */
export function outboxGiveBack(convId: string, requestId?: string): string | null {
  const map = readAll();
  const entry = map[convId];
  if (!entry || (requestId !== undefined && entry.requestId !== requestId)) return null;
  delete map[convId];
  writeAll(map);
  if (loadDraft(convId) !== null) return null;
  saveDraft(convId, entry.text);
  return entry.text;
}

/** After a reload: is the entry a send the server never got? `rows` is the
 *  conversation as the server has it. The run id is exact (FIX119 puts it on
 *  the user row); the text comparison covers a server that answered before
 *  run ids were stored on user rows. */
export function outboxIsUndelivered(
  entry: OutboxEntry,
  rows: ReadonlyArray<{ role: string; content?: string | null; run_id?: string }>,
): boolean {
  const text = entry.text.trim();
  return !rows.some(
    (m) => m.role === "user" && (m.run_id === entry.requestId || (m.content ?? "").trim() === text),
  );
}

/** Test hook. */
export function clearOutbox(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing */
  }
}

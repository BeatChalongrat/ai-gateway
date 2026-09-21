"use client";

// Client-side tracking for SERVER-SIDE text extraction.
//
// POST /conversations/{id}/files used to block until the uploaded file
// had been read (OCR on a scanned PDF: minutes), which pinned the
// composer on "Uploading…" and kept the send button disabled the whole
// time. The endpoint now returns as soon as the bytes are stored, with
// `extraction_status: "processing"`, and finishes the read in the
// background.
//
// So the client needs a second signal. There is no per-file GET, but
// GET /conversations/{id}/files already returns extraction_status for
// every row, so we poll that.
//
// Watchers are coalesced per conversation: N files uploaded together
// cost ONE request per interval, not N. Callers register a file and get
// an unsubscribe function back; the poll loop stops on its own once the
// last watcher for a conversation is gone.

import { api } from "@/lib/api";
import type { FileAttachment } from "@/lib/types";

export type ExtractionStatus = FileAttachment["extraction_status"];

/** How often to ask the server for the conversation's file statuses. */
export const EXTRACTION_POLL_INTERVAL_MS = 1_500;

/** Hard ceiling on a single file's watch. Above the backend's own
 *  extraction budget so we stop only when the server has genuinely
 *  given up, and the chip can't spin forever if a status write is
 *  lost. */
export const EXTRACTION_POLL_TIMEOUT_MS = 7 * 60 * 1_000;

/** Consecutive listFiles failures tolerated before a conversation's
 *  watchers are released. Prevents an endless retry loop against a
 *  conversation that has been deleted out from under us. */
const MAX_CONSECUTIVE_ERRORS = 5;

/** True while the server is still reading the file. These are the two
 *  non-terminal values of the DB's extraction_status enum. */
export function isExtracting(status: ExtractionStatus | undefined): boolean {
  return status === "pending" || status === "processing";
}

// Documents the server is expected to read. For these, "unsupported" can
// only mean the reader gave up (older rows used it before "failed" existed);
// for an image it is the normal answer — the model looks at the picture.
const READABLE_DOCUMENT_EXTENSIONS: ReadonlySet<string> = new Set([
  "pdf", "docx", "xlsx", "pptx",
]);

/** FIX126 F3 — the server stored the file but could not read its text (a
 *  corrupt or password-protected PDF): "failed", or "unsupported" on a
 *  PDF/Office file. The model will not see its contents. */
export function isUnreadable(
  status: ExtractionStatus | undefined,
  filename: string,
): boolean {
  if (status === "failed") return true;
  if (status !== "unsupported") return false;
  const i = filename.lastIndexOf(".");
  return i >= 0 && READABLE_DOCUMENT_EXTENSIONS.has(filename.slice(i + 1).toLowerCase());
}

interface Watcher {
  fileId: string;
  expiresAt: number;
  onStatus: (status: ExtractionStatus) => void;
}

interface ConversationPoll {
  watchers: Set<Watcher>;
  timer: ReturnType<typeof setTimeout> | null;
  errors: number;
}

const polls = new Map<string, ConversationPoll>();

function stop(convId: string, poll: ConversationPoll) {
  if (poll.timer !== null) clearTimeout(poll.timer);
  poll.timer = null;
  polls.delete(convId);
}

function schedule(convId: string, poll: ConversationPoll) {
  if (poll.watchers.size === 0) {
    stop(convId, poll);
    return;
  }
  poll.timer = setTimeout(() => void tick(convId, poll), EXTRACTION_POLL_INTERVAL_MS);
}

async function tick(convId: string, poll: ConversationPoll) {
  poll.timer = null;
  if (poll.watchers.size === 0) {
    stop(convId, poll);
    return;
  }

  let files: FileAttachment[];
  try {
    files = (await api.listFiles(convId)).files ?? [];
    poll.errors = 0;
  } catch {
    poll.errors += 1;
    if (poll.errors >= MAX_CONSECUTIVE_ERRORS) {
      // Give up quietly. The chip keeps whatever status it last had;
      // a page reload re-reads the truth from the server.
      stop(convId, poll);
      return;
    }
    schedule(convId, poll);
    return;
  }

  const byId = new Map(files.map((f) => [f.id, f]));
  const now = Date.now();
  for (const w of [...poll.watchers]) {
    const row = byId.get(w.fileId);
    if (!row) {
      // Deleted (or not visible any more) — nothing left to watch.
      poll.watchers.delete(w);
      continue;
    }
    w.onStatus(row.extraction_status);
    if (!isExtracting(row.extraction_status) || now > w.expiresAt) {
      poll.watchers.delete(w);
    }
  }
  schedule(convId, poll);
}

/**
 * Watch one file's extraction until it reaches a terminal status.
 *
 * `onStatus` fires on every poll (not only on change) so the caller can
 * keep its own state in sync without tracking previous values. Returns
 * an unsubscribe function — call it if the chip is removed before
 * extraction finishes.
 *
 * No-op (returns a noop unsubscribe) when the file is already terminal.
 */
export function watchExtraction(
  convId: string,
  fileId: string,
  initialStatus: ExtractionStatus,
  onStatus: (status: ExtractionStatus) => void,
): () => void {
  if (!isExtracting(initialStatus)) return () => {};

  let poll = polls.get(convId);
  if (!poll) {
    poll = { watchers: new Set(), timer: null, errors: 0 };
    polls.set(convId, poll);
  }
  const watcher: Watcher = {
    fileId,
    expiresAt: Date.now() + EXTRACTION_POLL_TIMEOUT_MS,
    onStatus,
  };
  poll.watchers.add(watcher);
  if (poll.timer === null) schedule(convId, poll);

  return () => {
    const current = polls.get(convId);
    if (!current) return;
    current.watchers.delete(watcher);
    if (current.watchers.size === 0) stop(convId, current);
  };
}

/** Test hook — drops every in-flight poll. */
export function __resetExtractionWatchers() {
  for (const [convId, poll] of [...polls]) stop(convId, poll);
}

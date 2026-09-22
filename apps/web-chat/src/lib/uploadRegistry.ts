// FIX127 A1 — what happens to an upload when its composer chip is removed.
//
// Until FIX127 the X on a chip was a pure array filter: the XHR kept
// going, the server kept the row and the blob, and every later turn of the
// conversation read that file into the model's context (R3: ddbb237d — the
// model saw 26 files, one of them a 30 MB upload removed at 9 %). The
// server now refuses to show the model any file that is not attached to a
// sent message (chat-edge FileRepo.ListForModelContext); this module is the
// client half — stop the bytes and take the row back straight away.
//
// A chip has three lives and each needs a different exit:
//   queued / in flight  → abort the XHR (nothing, or a torn body, reaches
//                         the server; a torn multipart body writes no row);
//   body fully sent,    → the server may create the row after our abort,
//   response not seen     and we never learn its id: delete by the
//                         client_id we sent with the body — now and again a
//                         few seconds later, in case the row lands after
//                         the first call;
//   uploaded (id known) → DELETE /files/{id}.
// Every server call is best-effort: even if it is lost, the row is not
// attached to a message, so the model never sees it and the unattached
// sweep removes it after UNATTACHED_FILE_TTL.

import { api } from "./api";

const controllers = new Map<string, AbortController>();
/** pendingId → the conversation the bytes were POSTed against. Set the
 *  moment an upload starts; a chip removed before that has nothing on the
 *  server. */
const netTargets = new Map<string, string>();

/** Late-delete retry: an abort after the body was fully sent races the
 *  server's row insert, so the by-client delete is repeated once. */
export const LATE_DELETE_RETRY_MS = 3000;

/** Registers an upload that is about to start. Returns null when the chip
 *  was already removed (the queued-then-removed case) so the caller skips
 *  the upload entirely. */
export function beginUpload(pendingId: string, netConvId: string): AbortController | null {
  if (discarded.has(pendingId)) {
    discarded.delete(pendingId);
    return null;
  }
  const ctrl = new AbortController();
  controllers.set(pendingId, ctrl);
  netTargets.set(pendingId, netConvId);
  return ctrl;
}

/** The upload finished (either way); the controller is no longer needed.
 *  The network target stays so a later remove can still delete by id. */
export function endUpload(pendingId: string): void {
  controllers.delete(pendingId);
}

/** Chips removed before their upload started. Bounded: an entry is consumed
 *  by beginUpload or cleared by forgetUpload. */
const discarded = new Set<string>();

/** The chip left the composer for good (sent, or bucket cleared): nothing to
 *  delete, just drop the bookkeeping. */
export function forgetUpload(pendingId: string): void {
  controllers.delete(pendingId);
  netTargets.delete(pendingId);
  discarded.delete(pendingId);
}

export interface DiscardTarget {
  id: string;
  serverId?: string;
}

/** The user removed the chip. Aborts the transfer and removes whatever the
 *  server may hold for it. Never throws; never blocks the UI. */
export function discardUpload(
  entry: DiscardTarget,
  schedule: (fn: () => void, ms: number) => void = (fn, ms) => {
    setTimeout(fn, ms);
  },
): void {
  const ctrl = controllers.get(entry.id);
  const netConvId = netTargets.get(entry.id);
  controllers.delete(entry.id);
  netTargets.delete(entry.id);
  if (ctrl) ctrl.abort();
  if (entry.serverId) {
    void api.deleteFile(entry.serverId).catch(() => {});
    return;
  }
  if (!netConvId) {
    // Not started yet: make sure the worker loop skips it.
    discarded.add(entry.id);
    return;
  }
  const byClient = () => {
    void api.deleteFileByClientId(netConvId, entry.id).catch(() => {});
  };
  byClient();
  schedule(byClient, LATE_DELETE_RETRY_MS);
}

/** Test seam. */
export function _resetUploadRegistry(): void {
  controllers.clear();
  netTargets.clear();
  discarded.clear();
}

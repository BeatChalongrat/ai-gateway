// FIX109 Part A — the local chain is a CACHE of the server transcript, and a
// cache that never checks whether its source moved is a bug.
//
// The desktop keeps <userData>/history/<conversation>.json: the full gateway
// message list with tool chains, which the chat-edge transcript does not
// carry (turn-coordinator.ts, "History:"). chat-edge owns the visible
// transcript (chat-edge-client.ts, "server-authoritative transcript — FIX 9");
// the desktop imports its own visible rows there after every turn. Until
// FIX109 the local file, once written, was the model's whole past on this
// machine: a turn taken on the web landed on the server and never in the
// file, so the page showed it and the model never saw it (measured on the
// owner's machine: 69 of 172 live conversations had a server transcript
// ahead of the local chain).
//
// The fix is a marker beside the chain — the server's message_count as of
// the last import this host made — and a comparison on every load:
//
//   server == marker  → fresh: the local chain (tool chains included)
//   server  > marker  → stale: someone else wrote → this turn reads the
//                       server transcript instead (tool chains are lost for
//                       the rows that came from the server; content never is)
//   server  < marker  → the server is BEHIND (an import failed, or the count
//                       moved down): the local chain is the only copy of that
//                       turn, so it is kept
//   no marker         → a file written before FIX109 ("unmarked"): decided by
//                       comparing the server's visible rows with what this
//                       chain would have imported (countVisibleRows), one
//                       page-through, after which the file carries a marker
//   server unknown    → chat-edge could not be read: the local chain (the
//                       server transcript could not be read either)
//
// message_count is chat-edge's logical counter (conversation_repo.go
// IncrementMessageCount): +2 per finished web turn or regenerate, +rows
// actually inserted per import, never decremented. It is not a row count of
// the active branch — it is a "did the server move" signal, which is all the
// comparison needs. A false "stale" costs tool chains for one turn; a false
// "fresh" costs a user their own words, so every unknown resolves towards
// the server transcript except the ones where the server cannot be read.
//
// No 'electron' import: unit-tested in plain Node.

import type { GatewayMessage } from "./gateway-client";

export const HISTORY_FILE_VERSION = 2;

/** What the history file holds. `serverMessageCount` is null for a file
 *  written before FIX109 (a bare array) or when chat-edge was unreachable
 *  at the time of the write. */
export interface LocalChain {
  messages: GatewayMessage[];
  serverMessageCount: number | null;
}

interface HistoryFileV2 {
  v: number;
  server_message_count: number | null;
  messages: GatewayMessage[];
}

/** Parse either file shape: a bare array (pre-FIX109) or the v2 envelope. */
export function parseHistoryFile(raw: unknown): LocalChain | null {
  if (Array.isArray(raw)) return { messages: raw as GatewayMessage[], serverMessageCount: null };
  if (!raw || typeof raw !== "object") return null;
  const f = raw as Partial<HistoryFileV2>;
  if (!Array.isArray(f.messages)) return null;
  const n = f.server_message_count;
  return { messages: f.messages, serverMessageCount: typeof n === "number" && Number.isFinite(n) && n >= 0 ? Math.trunc(n) : null };
}

export function serializeHistoryFile(messages: readonly GatewayMessage[], serverMessageCount: number | null): string {
  const file: HistoryFileV2 = { v: HISTORY_FILE_VERSION, server_message_count: serverMessageCount, messages: messages as GatewayMessage[] };
  return JSON.stringify(file);
}

/** The rows importVisible would have sent for this chain: one user row per
 *  user turn, one assistant row per turn that produced any assistant text
 *  (the turn's text is imported as ONE row, preamble text included). Tool
 *  results and text-less tool-call preambles are never imported. */
export function countVisibleRows(messages: readonly GatewayMessage[]): number {
  let rows = 0;
  let textThisTurn = false;
  for (const m of messages) {
    if (m.role === "user") {
      rows++;
      textThisTurn = false;
    } else if (m.role === "assistant" && typeof m.content === "string" && m.content.trim().length > 0 && !textThisTurn) {
      rows++;
      textThisTurn = true;
    }
  }
  return rows;
}

export type ChainFreshness = "fresh" | "stale" | "unmarked" | "unknown";

/** The decision, from the marker and the server's current count. */
export function chainFreshness(local: LocalChain, serverMessageCount: number | undefined | null): ChainFreshness {
  if (typeof serverMessageCount !== "number" || !Number.isFinite(serverMessageCount)) return "unknown";
  if (local.serverMessageCount === null) return "unmarked";
  return serverMessageCount > local.serverMessageCount ? "stale" : "fresh";
}

/** An unmarked chain against the server's visible rows (the transcript the
 *  host would load instead): the server holding MORE visible rows than this
 *  chain ever imported means rows exist that the chain lacks. Fewer or equal
 *  means the chain is at least as complete as the server. */
export function unmarkedFreshness(local: LocalChain, serverVisibleRows: number): "fresh" | "stale" {
  return serverVisibleRows > countVisibleRows(local.messages) ? "stale" : "fresh";
}

/** The marker to write after this host's own import: the server's count as
 *  read at the start of the turn plus the rows the import actually inserted.
 *  Null when the server's count was never read (chat-edge unreachable) — the
 *  next load then takes the unmarked path. A web turn that lands between the
 *  read and the import is not in the sum, so the next load sees the server
 *  ahead and reads the transcript: the safe direction. */
export function nextMarker(serverMessageCountAtStart: number | undefined | null, imported: number): number | null {
  if (typeof serverMessageCountAtStart !== "number" || !Number.isFinite(serverMessageCountAtStart)) return null;
  return Math.max(0, Math.trunc(serverMessageCountAtStart)) + Math.max(0, Math.trunc(imported));
}

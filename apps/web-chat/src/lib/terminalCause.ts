// FIX118 — how a turn ended, the page's half of one closed set.
//
// docs/contracts/terminal_cause.json is the source of truth; chat-edge
// (Go) and the desktop host (TS) pin themselves to the same file, and
// terminalCause.test.ts pins this one. The page does not decide causes —
// the server (web turns) and the host (desktop turns) do — but it reads
// them back on a row (``terminal_cause`` / ``error_code`` from the list
// wire, migration 043) and it names the one ending it observes itself: a
// stream that closed with no terminal frame at all.

export const TERMINAL_CAUSES = ["completed", "stopped_by_user", "error", "timeout", "disconnected", "eof_without_terminal", "interrupted"] as const;
export type TerminalCause = (typeof TERMINAL_CAUSES)[number];

export function isTerminalCause(s: unknown): s is TerminalCause {
  return typeof s === "string" && (TERMINAL_CAUSES as readonly string[]).includes(s);
}

/** The catalogue code for "the stream ended and nothing said how" — the
 *  same code chat-edge and the desktop host mint for that ending, so all
 *  three surfaces show one sentence (error.stream_interrupted) for it.
 *  Before FIX118 the page used a private code, ``stream_ended_early``,
 *  that no mapper knew and no other surface used. */
export const EOF_WITHOUT_TERMINAL_CODE = "stream_interrupted";
export const EOF_WITHOUT_TERMINAL_CAUSE: TerminalCause = "eof_without_terminal";

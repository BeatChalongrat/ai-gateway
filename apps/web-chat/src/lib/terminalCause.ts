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

// ── FIX119 — why the MODEL stopped ────────────────────────────────────────
//
// Distinct from the terminal cause: a completed turn can carry
// `max_tokens` — the answer is cut off — and the page must say so instead
// of presenting the cut as a finished answer. The gateway maps each
// provider's native value into this set; the page reads it from
// message_complete (live) and from the row (reload). `unknown` means the
// provider said nothing; the page shows nothing for it.
export const STOP_REASONS = ["end_turn", "max_tokens", "tool_use", "stop_sequence", "content_filter", "refusal", "context_window_exceeded", "unknown"] as const;
export type StopReason = (typeof STOP_REASONS)[number];

export function isStopReason(s: unknown): s is StopReason {
  return typeof s === "string" && (STOP_REASONS as readonly string[]).includes(s);
}

/** The stop reasons the page tells the person about, and the dictionary
 *  key for each. end_turn / tool_use / unknown say nothing. */
export const STOP_REASON_NOTICE_KEY = {
  max_tokens: "msg.stopMaxTokens",
  refusal: "msg.stopRefusal",
  content_filter: "msg.stopContentFilter",
  context_window_exceeded: "msg.stopContextWindow",
  stop_sequence: "msg.stopSequence",
} as const;

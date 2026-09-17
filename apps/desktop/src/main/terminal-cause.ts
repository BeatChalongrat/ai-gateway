// FIX118 — how a turn ended, said the same way the server says it.
//
// The desktop host used to mint its own codes at each throw site
// (`network` carrying the raw fetch error text, `host_exception`,
// `no_result`, a lowercase `generation_interrupted`), treat a stream that
// simply closed as a success, and drop the gateway's `retryable` verdict.
// The page maps codes it knows to a localised sentence and shows an
// unknown code's message verbatim — so a Thai user saw "fetch failed".
//
// This module is the host's half of docs/contracts/terminal_cause.json:
//   - the closed set of terminal causes (test/terminal-cause.test.ts pins
//     it to the file; chat-edge and the page pin themselves to the same
//     file), and
//   - the mapping from what the host observes to a code the page and the
//     server both know (backend/app/core/error_catalog.py, plus the two
//     codes the web dictionary declares for the host: stream_stalled and
//     GENERATION_INTERRUPTED). A code nothing here can name is passed
//     through and logged, never silently rewritten.

export const TERMINAL_CAUSES = ["completed", "stopped_by_user", "error", "timeout", "disconnected", "eof_without_terminal", "interrupted"] as const;
export type TerminalCause = (typeof TERMINAL_CAUSES)[number];

export function isTerminalCause(s: unknown): s is TerminalCause {
  return typeof s === "string" && (TERMINAL_CAUSES as readonly string[]).includes(s);
}

/** The catalogue code the host mints when the failure has no gateway code. */
export const TRANSPORT_CODES = {
  eof_without_terminal: { code: "stream_interrupted", retryable: true },
  disconnected: { code: "stream_interrupted", retryable: true },
  timeout: { code: "provider_timeout", retryable: true },
  interrupted: { code: "GENERATION_INTERRUPTED", retryable: true },
  unclassified: { code: "unclassified_error", retryable: false },
} as const;

/** The catalogue's own English sentence for each code the host mints —
 *  what a client that does not know the code shows. */
export const CATALOGUE_MESSAGES: Readonly<Record<string, string>> = {
  stream_interrupted: "The response was interrupted. Please try again.",
  provider_timeout: "The AI provider took too long to respond. Please try again.",
  unclassified_error: "Something went wrong while the reply was being received, and we could not identify what. Write a new message to continue.",
  GENERATION_INTERRUPTED: "This turn was interrupted when the app closed. What had been written is kept; send again to continue.",
};

/** The catalogue sentence for a code the host mints ("" when unknown). */
export function catalogueMessage(code: string): string {
  return CATALOGUE_MESSAGES[code] ?? "";
}

/** One terminal `error` frame's data, as the page reads it. */
export interface HostErrorFrame {
  code: string;
  message: string;
  retryable?: boolean;
  cause?: TerminalCause;
  details?: Record<string, unknown>;
  /** FIX121 — the gateway's per-model attempt log, passed through as the
   *  page reads it off a web error frame (`attempts`). */
  attempts?: unknown[];
}

/** The status + cause + code a turn's assistant row is imported with. */
export interface TurnEnding {
  status: "completed" | "error" | "cancelled";
  cause: TerminalCause;
  errorCode?: string;
  /** FIX121 — rides the imported error row as `error_attempts`. */
  attempts?: unknown[];
}

/** The upstream stream closed without [DONE], an error frame or a finish
 *  reason. Before FIX118 this was synthesised into a success. */
export function eofWithoutTerminal(): HostErrorFrame {
  const t = TRANSPORT_CODES.eof_without_terminal;
  return { code: t.code, message: catalogueMessage(t.code), retryable: t.retryable, cause: "eof_without_terminal" };
}

/** A throw out of the stream read: the idle watchdog (stalled) or the
 *  transport (reset, DNS, TLS, a torn body). The Go/Node error text is
 *  diagnostic and rides `details.transport`; it is never the message. */
export function classifyStreamFailure(err: unknown, opts: { stalled: boolean; idleSeconds?: number }): HostErrorFrame {
  const text = err instanceof Error ? err.message : String(err);
  if (opts.stalled) {
    // FIX80's code, declared by the web dictionary with its own sentence.
    return {
      code: "stream_stalled",
      message: `The gateway connection stalled (no data for ${opts.idleSeconds ?? 60}s).`,
      retryable: true,
      cause: "timeout",
      details: { transport: text },
    };
  }
  const t = TRANSPORT_CODES.disconnected;
  return { code: t.code, message: catalogueMessage(t.code), retryable: t.retryable, cause: "disconnected", details: { transport: text } };
}

/** Host-minted codes that are NOT declared anywhere the page can read,
 *  mapped to the catalogue code that names the same thing. The original
 *  code and sentence are kept under `details` for the ledger and the log. */
export const HOST_CODE_MAP: Record<string, { code: string; cause: TerminalCause; retryable: boolean }> = {
  network: { code: TRANSPORT_CODES.disconnected.code, cause: "disconnected", retryable: true },
  host_exception: { code: TRANSPORT_CODES.unclassified.code, cause: "error", retryable: false },
  no_result: { code: TRANSPORT_CODES.unclassified.code, cause: "error", retryable: false },
  generation_interrupted: { code: TRANSPORT_CODES.interrupted.code, cause: "interrupted", retryable: true },
};

/** Codes the host may put on the wire as they are: every catalogue code
 *  the gateway can send is forwarded untouched, and these host codes are
 *  declared by the web dictionary (apps/web-chat/src/lib/errorMessages.ts). */
export const HOST_DECLARED_CODES: ReadonlySet<string> = new Set(["stream_stalled", "message_too_long", "GENERATION_INTERRUPTED", "insufficient_credits"]);

/** Normalise one error frame before it goes out: a host code with a
 *  catalogue equivalent is rewritten (original kept in details); anything
 *  else passes through. `mapped` says which happened, for the log. */
export function normalizeHostError(frame: HostErrorFrame): { frame: HostErrorFrame; mapped: boolean } {
  const m = HOST_CODE_MAP[frame.code];
  if (!m) return { frame: { ...frame, cause: frame.cause ?? "error" }, mapped: false };
  return {
    mapped: true,
    frame: {
      code: m.code,
      message: catalogueMessage(m.code) || frame.message,
      retryable: frame.retryable ?? m.retryable,
      cause: frame.cause ?? m.cause,
      details: { ...(frame.details ?? {}), host_code: frame.code, host_message: frame.message },
      ...(frame.attempts ? { attempts: frame.attempts } : {}),
    },
  };
}

/** The ending a run's outcome implies for the imported assistant row. */
export function endingForOutcome(outcome: "done" | "cancelled" | "error" | "paused" | "denied", errored: { cause?: TerminalCause; errorCode?: string; attempts?: unknown[] } | null): TurnEnding {
  switch (outcome) {
    case "cancelled":
      return { status: "cancelled", cause: "stopped_by_user" };
    case "error":
      return { status: "error", cause: errored?.cause ?? "error", ...(errored?.errorCode ? { errorCode: errored.errorCode } : {}), ...(errored?.attempts?.length ? { attempts: errored.attempts } : {}) };
    default:
      return { status: "completed", cause: "completed" };
  }
}

// ── FIX119 — why the MODEL stopped ────────────────────────────────────────
//
// Distinct from the terminal cause: a run that completed can carry
// stop_reason max_tokens — the answer is cut off, and the page says so.
// The gateway maps each provider family's native value into this closed
// set and sends it as `stop_reason` on the final chunk; the host only
// carries it. An older gateway sends only the OpenAI-shaped finish_reason,
// for which STOP_REASON_FROM_FINISH is the contract's `openai` row.
export const STOP_REASONS = ["end_turn", "max_tokens", "tool_use", "stop_sequence", "content_filter", "refusal", "context_window_exceeded", "unknown"] as const;
export type StopReason = (typeof STOP_REASONS)[number];

export function isStopReason(s: unknown): s is StopReason {
  return typeof s === "string" && (STOP_REASONS as readonly string[]).includes(s);
}

export const STOP_REASON_FROM_FINISH: Readonly<Record<string, StopReason>> = {
  stop: "end_turn",
  length: "max_tokens",
  tool_calls: "tool_use",
  function_call: "tool_use",
  content_filter: "content_filter",
};

/** The closed-set reason for a chunk: the gateway's own when it sent one,
 *  else the OpenAI row of the table, else `unknown`. `null`/"" → "" (nothing
 *  was said; the caller records nothing). */
export function stopReasonFor(stopReason: unknown, finishReason: string | null | undefined): StopReason | "" {
  if (isStopReason(stopReason)) return stopReason;
  if (!finishReason) return "";
  return STOP_REASON_FROM_FINISH[finishReason] ?? "unknown";
}

// FIX32 §1.2 — token-budgeted context for the DESKTOP turn path.
//
// Before this round the desktop had nothing: chain.ts appended forever and
// buildMessages loaded the whole local history verbatim, so a long
// conversation grew until the provider refused it (measured in the FIX32
// report — web-chat has had chat-edge's ContextCompiler since S4; the desktop
// had 22 lines). This is that compiler's shape, ported, not reinvented:
// the same token heuristic (ASCII/4, Thai-CJK ×2/3), the same budget rules
// (context-budget.ts: the catalogue's window with chat-edge's headroom and
// reserves, pinned against the Go source since FIX104), trim-when-over —
// with the deliberate divergences the report lists:
//
//   1. THE FIRST EXCHANGE SURVIVES — WHILE IT IS SMALL. chat-edge trimmed
//      oldest-first, so the user's turn-1 standing instruction was the first
//      thing to die; we keep the head (the first user message and its reply)
//      plus the newest tail, and drop the middle. FIX105: the head is pinned
//      only while it costs at most HEAD_PIN_MAX_SHARE of the budget and
//      leaves room for the newest message. An 18,600-character first reply
//      pinned for good made a conversation unrecoverable; an oversized head
//      is now ordinary history, summarised and dropped first. chat-edge
//      ported the head rule and applies the same cap.
//   2. TOOL CHAINS ARE ATOMIC. The desktop chain carries assistant(tool_calls)
//      + tool results (chat-edge's stored history has no tool rows). A unit is
//      either one plain message or one whole tool group — never an orphan tool
//      result or a call whose result was dropped (FIX23: a malformed role
//      sequence is a provider 4xx).
//   3. NO SYNCHRONOUS SUMMARY EXTRACTION. chat-edge folds soon-to-be-dropped
//      turns into its <conversation_state> row first; the desktop has no state
//      store and a client-side extra model call per long turn is not this
//      round's decision. The model is TOLD what was dropped (a context note in
//      the system message), and the USER is told too (the compaction_notice
//      event → the page's marker) — silently forgetting is the failure mode
//      people find maddening.
//
// No 'electron' import: unit-tested in plain Node.

import { type BudgetBasis, HEAD_PIN_MAX_SHARE, MESSAGES_FLOOR, PER_TOOL_TOKENS, outputReserveForRequest, workingLimitFor } from "./context-budget";
import type { SummaryStatus } from "./context-manager";
import { CONTEXT_NOTE_TEMPLATE } from "./context-note-prompt";
import type { GatewayMessage } from "./gateway-client";

// FIX104: the per-model window map that stood here (`auto` and five gpt ids
// at 120,000, everything else 100,000) is gone. The window is the catalogue's
// (BudgetBasis, resolved by the turn coordinator); the rules are chat-edge's
// (context-budget.ts).
const PER_MESSAGE_OVERHEAD = 4;
/** The context note joins the system message when compaction happens. */
export const CONTEXT_NOTE_RESERVE = 160;
const IMAGE_BLOCK_TOKENS = 500;

/** chat-edge's EstimateTokens, verbatim semantics (token_estimator.go):
 *  ASCII ≈ len/4; Thai/CJK ≈ ceil(2·len/3). */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let ascii = 0;
  let cjk = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0;
    if (
      (c >= 0x0e00 && c <= 0x0e7f) || // Thai
      (c >= 0x4e00 && c <= 0x9fff) || // CJK
      (c >= 0x3040 && c <= 0x30ff) || // kana
      (c >= 0xac00 && c <= 0xd7af) // hangul
    ) {
      cjk++;
    } else {
      ascii++;
    }
  }
  const t = Math.floor(ascii / 4) + Math.floor((cjk * 2 + 2) / 3);
  return t < 1 ? 1 : t;
}

function messageTokens(m: GatewayMessage): number {
  let t = PER_MESSAGE_OVERHEAD;
  const c = m.content as unknown;
  if (typeof c === "string") t += estimateTokens(c);
  else if (Array.isArray(c)) {
    for (const block of c as { type?: string; text?: string }[]) {
      if (block?.type === "text" && typeof block.text === "string") t += estimateTokens(block.text);
      else t += IMAGE_BLOCK_TOKENS;
    }
  }
  if (m.tool_calls) t += estimateTokens(JSON.stringify(m.tool_calls));
  return t;
}

/** One unit = a plain message, or an assistant(tool_calls) plus every tool
 *  result that answers it — kept or dropped together. */
interface Unit {
  messages: GatewayMessage[];
  tokens: number;
}

function toUnits(history: readonly GatewayMessage[]): Unit[] {
  const units: Unit[] = [];
  let i = 0;
  while (i < history.length) {
    const m = history[i]!;
    if (m.role === "assistant" && m.tool_calls && m.tool_calls.length > 0) {
      const group: GatewayMessage[] = [m];
      let j = i + 1;
      while (j < history.length && history[j]!.role === "tool") {
        group.push(history[j]!);
        j++;
      }
      units.push({ messages: group, tokens: group.reduce((s, g) => s + messageTokens(g), 0) });
      i = j;
    } else {
      units.push({ messages: [m], tokens: messageTokens(m) });
      i++;
    }
  }
  return units;
}

export interface CompiledChain {
  /** system (with the context note when compacted) [+ state preamble] + kept history. */
  messages: GatewayMessage[];
  compacted: boolean;
  /** True when `messages` is a NEW array (compacted and/or preamble injected);
   *  false means the very same array came back — callers persist the chain
   *  from the ORIGINAL when altered. */
  altered: boolean;
  /** messages dropped (message count, not units). */
  dropped: number;
  totalMessages: number;
  sentMessages: number;
  /** tokens of the history actually SENT (the conversation's own messages). */
  historyTokens: number;
  /** historyTokens plus the injected state pair when there is one — chat-edge's SentTokens, the meter's numerator. */
  sentTokens: number;
  /** The state pair rides in `messages` (compacted with a preamble). */
  stateInjected: boolean;
  /** FIX105: there was a first exchange and this plan did not pin it (too large, or no room for the newest unit). */
  headDropped: boolean;
  /** FIX105: the newest message alone is larger than the messages budget — no trim makes this request fit. */
  newestOverBudget: boolean;
  /** FIX105 Part B: transcript rows never loaded (beyond the load ceiling): counted and told, never summarised here. */
  omittedOlder: number;
  /** tokens of the history as LOADED (before any trim) — the meter's numerator. */
  historyTokensLoaded: number;
  /** triggerRatio × messagesRemaining — the meter's "compaction at" mark. */
  thresholdTokens: number;
  /** [dropStart, dropEnd) — history indices (0 = first message after the
   *  system message) of the dropped middle; both 0 when nothing dropped. */
  dropStart: number;
  dropEnd: number;
  /** model/window: what the budget was measured against (window 0 = the default was used). */
  budget: { total: number; system: number; toolReserve: number; outputReserve: number; messagesRemaining: number; model: string; window: number };
}

/** FIX33 knobs. Absent = FIX32 behaviour (compact only on overflow, no preamble). */
export interface CompileOptions {
  /** Compact when loaded history ≥ this × messagesRemaining (default 1.0 = overflow only). */
  triggerRatio?: number;
  /** …and trim down to this × messagesRemaining (default = triggerRatio). */
  targetRatio?: number;
  /** The <conversation_state> pair (user/assistant), injected right after the
   *  system message WHEN compaction happens; budgeted like the system. */
  preamble?: GatewayMessage[];
  /** Drop every history message at or before this index (head excepted),
   *  regardless of budget — A-W7's manual compact. */
  manualBoundaryIndex?: number | null;
  /** Whether a summary is present (the context note says so). */
  summaryPresent?: boolean;
  /** FIX107 — what stands in for the messages left out, as the context note states it (chat-edge's SummaryStatus). Without it: summaryPresent ? complete : off. */
  summaryStatus?: SummaryStatus;
  /** FIX105 Part B: older transcript rows that were never loaded. Like chat-edge's OmittedOlder they make the request compacted and are named in the context note. */
  omittedOlder?: number;
}

/** FIX107 — the sections of CONTEXT_NOTE_TEMPLATE by name (chat-edge's parseContextNote). */
export function parseContextNote(raw: string): Record<string, string> {
  const parts: Record<string, string> = {};
  let name = "";
  let buf: string[] = [];
  const flush = () => {
    if (name) parts[name] = buf.join("\n").trim();
  };
  for (const line of raw.replace(/\r\n/g, "\n").split("\n")) {
    if (line.length > 6 && line.startsWith("---") && line.endsWith("---")) {
      flush();
      name = line.replace(/^-+|-+$/g, "");
      buf = [];
      continue;
    }
    buf.push(line);
  }
  flush();
  return parts;
}

const CONTEXT_NOTE_PARTS = parseContextNote(CONTEXT_NOTE_TEMPLATE);

/**
 * The context note the MODEL sees, appended to the system message — chat-edge's
 * compactionContextNote on the same asset (prompt_context_note.txt). FIX107: it
 * says the earliest message in view is not where the conversation began when
 * the first exchange is out of view, and what to do when asked about that part.
 */
export function renderContextNote(missing: number, keptRecent: number, headKept: boolean, status: SummaryStatus): string {
  const p = CONTEXT_NOTE_PARTS;
  const summary = status === "complete" || status === "pending" ? "SUMMARY_COMPLETE" : status === "partial" ? "SUMMARY_PARTIAL" : status === "failed" ? "SUMMARY_FAILED" : "SUMMARY_OFF";
  const text = [p["OPEN"], p[headKept ? "HEAD_KEPT" : "HEAD_DROPPED"], p[summary], p["CLOSE"]].join(" ");
  const fill: Record<string, string> = {
    "{missing}": String(missing),
    "{missing_messages}": missing === 1 ? "message" : "messages",
    "{missing_are}": missing === 1 ? "is" : "are",
    "{kept}": String(keptRecent),
    "{kept_messages}": keptRecent === 1 ? "message" : "messages",
  };
  return "\n\n" + text.replace(/\{missing\}|\{missing_messages\}|\{missing_are\}|\{kept\}|\{kept_messages\}/g, (m) => fill[m]!);
}

/**
 * messages[0] must be the system message; the rest is history + the new user
 * turn (buildMessages' shape). Returns the same array when it fits (no copy),
 * so callers can cheaply detect "untouched".
 */
export function compileChain(messages: GatewayMessage[], basis: BudgetBasis, numTools: number, opts: CompileOptions = {}): CompiledChain {
  const system = messages[0];
  const history = messages.slice(1);
  const sysText = typeof system?.content === "string" ? system.content : "";
  const sysTokens = estimateTokens(sysText) + PER_MESSAGE_OVERHEAD;
  const total = workingLimitFor(basis);
  const toolReserve = numTools * PER_TOOL_TOKENS;
  const outputReserve = outputReserveForRequest(total, basis.outputTokens);
  let remaining = total - sysTokens - toolReserve - outputReserve;
  if (remaining < MESSAGES_FLOOR) remaining = MESSAGES_FLOOR;

  const trigger = ratio(opts.triggerRatio, 1);
  const target = Math.min(ratio(opts.targetRatio, trigger), trigger);
  const thresholdTokens = Math.floor(remaining * trigger);
  const preamble = opts.preamble ?? [];
  const preambleTokens = preamble.reduce((s, m) => s + messageTokens(m), 0);

  const historyTokens = history.reduce((s, m) => s + messageTokens(m), 0);
  const omittedOlder = Math.max(0, Math.trunc(opts.omittedOlder ?? 0));
  const newestOverBudget = history.length > 0 && messageTokens(history[history.length - 1]!) > remaining;
  const base = {
    totalMessages: history.length,
    omittedOlder,
    newestOverBudget,
    historyTokensLoaded: historyTokens,
    thresholdTokens,
    budget: { total, system: sysTokens, toolReserve, outputReserve, messagesRemaining: remaining, model: basis.model, window: basis.window },
  };
  const boundary = typeof opts.manualBoundaryIndex === "number" && opts.manualBoundaryIndex >= 0 && opts.manualBoundaryIndex < history.length ? opts.manualBoundaryIndex : null;
  const untouched = (): CompiledChain => ({ messages, compacted: false, altered: false, dropped: 0, sentMessages: history.length, historyTokens, sentTokens: historyTokens, stateInjected: false, headDropped: false, dropStart: 0, dropEnd: 0, ...base });

  const overThreshold = historyTokens >= thresholdTokens;
  if (!overThreshold && boundary === null && omittedOlder === 0) return untouched();

  const units = toUnits(history);
  const noteReserve = CONTEXT_NOTE_RESERVE;
  // The tail's budget: newest units first until it is spent (below).
  const limit = overThreshold ? Math.floor(remaining * target) : remaining;
  // Head: the first user message and the reply that answered it (units 0..1,
  // when they are the plain first exchange). Never more than 2 units.
  // FIX105 — pinned only while that cannot kill the conversation: at most
  // HEAD_PIN_MAX_SHARE of the messages budget, and never when keeping it
  // leaves no room for the newest unit. Otherwise it is ordinary old history,
  // summarised and dropped first, and headDropped says so. (FIX104 measured
  // the alternative: an 18,600-character first reply, pinned for good, and a
  // conversation refused on every turn after it.) chat-edge's
  // planHeadPreservingTrim applies the same rule.
  let naturalHead = 0;
  if (units.length > 0 && units[0]!.messages[0]!.role === "user") {
    naturalHead = 1;
    if (units.length > 1 && units[1]!.messages[0]!.role === "assistant" && !units[1]!.messages[0]!.tool_calls) naturalHead = 2;
  }
  const naturalHeadTokens = units.slice(0, naturalHead).reduce((s, u) => s + u.tokens, 0);
  const newestUnitTokens = units.length > 0 ? units[units.length - 1]!.tokens : 0;
  const unpinHead =
    naturalHead > 0 &&
    units.length > naturalHead &&
    (naturalHeadTokens > Math.trunc(remaining * HEAD_PIN_MAX_SHARE) || naturalHeadTokens + newestUnitTokens + noteReserve + preambleTokens > limit);
  const headCount = unpinHead ? 0 : naturalHead;
  const head = units.slice(0, headCount);
  const headTokens = head.reduce((s, u) => s + u.tokens, 0);
  const headMessages = head.reduce((s, u) => s + u.messages.length, 0);

  // The tail may not start before the boundary's unit (A-W7) or inside the head.
  let tailFloor = headCount;
  if (boundary !== null) {
    let idx = 0;
    for (let ui = 0; ui < units.length; ui++) {
      idx += units[ui]!.messages.length;
      if (idx - 1 >= boundary) {
        tailFloor = Math.max(tailFloor, ui + 1);
        break;
      }
    }
    if (tailFloor > units.length - 1) tailFloor = units.length - 1; // the newest unit always ships
  }

  // Tail: newest units first until the (target) budget is spent. The LAST
  // unit (the new user turn) always ships, even if it alone overflows — a
  // system-only request dead-ends the loop (chat-edge's rule).
  let used = headTokens + noteReserve + preambleTokens;
  let cut = units.length; // first kept tail unit index
  for (let i = units.length - 1; i >= tailFloor; i--) {
    const u = units[i]!;
    if (used + u.tokens > limit && i < units.length - 1) break;
    used += u.tokens;
    cut = i;
  }
  if (cut >= units.length) cut = units.length - 1;
  if (cut < tailFloor) cut = tailFloor;
  const tail = units.slice(cut);
  const kept = [...head, ...tail];
  const keptMessages = kept.flatMap((u) => u.messages);
  const dropped = history.length - keptMessages.length;
  if (dropped <= 0 && omittedOlder === 0) return untouched();

  const keptRecent = tail.reduce((s, u) => s + u.messages.length, 0);
  const noteStatus: SummaryStatus = opts.summaryStatus ?? (opts.summaryPresent === true ? "complete" : "off");
  const newSystem: GatewayMessage = { ...system!, content: sysText + renderContextNote(dropped + omittedOlder, keptRecent, headMessages > 0, noteStatus) };
  const out: GatewayMessage[] = [newSystem, ...preamble, ...keptMessages];
  const keptTokens = keptMessages.reduce((s, m) => s + messageTokens(m), 0);
  return {
    messages: out,
    compacted: true,
    altered: true,
    dropped,
    sentMessages: keptMessages.length,
    historyTokens: keptTokens,
    sentTokens: keptTokens + preambleTokens,
    stateInjected: preamble.length > 0,
    headDropped: unpinHead && dropped > 0,
    dropStart: dropped > 0 ? headMessages : 0,
    dropEnd: dropped > 0 ? headMessages + dropped : 0,
    ...base,
  };
}

function ratio(v: number | undefined, dflt: number): number {
  return typeof v === "number" && v > 0 && v <= 1 ? v : dflt;
}

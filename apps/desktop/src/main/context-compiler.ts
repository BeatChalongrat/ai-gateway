// FIX32 §1.2 — token-budgeted context for the DESKTOP turn path.
//
// Before this round the desktop had nothing: chain.ts appended forever and
// buildMessages loaded the whole local history verbatim, so a long
// conversation grew until the provider refused it (measured in the FIX32
// report — web-chat has had chat-edge's ContextCompiler since S4; the desktop
// had 22 lines). This is that compiler's shape, ported, not reinvented:
// the same token heuristic (ASCII/4, Thai-CJK ×2/3), the same conservative
// per-model windows, the same output/tool reserves, trim-when-over — with the
// deliberate divergences the report lists:
//
//   1. THE FIRST EXCHANGE ALWAYS SURVIVES. chat-edge trims oldest-first, so
//      the user's turn-1 standing instruction is the first thing to die; the
//      brief ranks it the thing that must die LAST. We keep the head (the
//      first user message and its reply) plus the newest tail, and drop the
//      middle.
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

import type { GatewayMessage } from "./gateway-client";

// The same conservative windows chat-edge budgets against (context_budget.go)
// for the models the desktop offers; unknown models get the cautious default.
export const MODEL_CONTEXT_LIMITS: Record<string, number> = {
  "gpt-5.6-terra": 120_000,
  "gpt-5.6-sol": 120_000,
  "gpt-5.6-luna": 120_000,
  "gpt-5.2": 120_000,
  "gpt-5.2-mini": 120_000,
  auto: 120_000,
};
export const DEFAULT_CONTEXT_LIMIT = 100_000;
const OUTPUT_RESERVE = 16_000;
const PER_TOOL_TOKENS = 200;
/** Never trim below this much history budget (chat-edge's floor). */
const MESSAGES_FLOOR = 2_000;
const PER_MESSAGE_OVERHEAD = 4;
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
  /** system (with the context note when compacted) + kept history. */
  messages: GatewayMessage[];
  compacted: boolean;
  /** messages dropped (message count, not units). */
  dropped: number;
  totalMessages: number;
  sentMessages: number;
  historyTokens: number;
  budget: { total: number; system: number; toolReserve: number; outputReserve: number; messagesRemaining: number };
}

/** The context note the MODEL sees, appended to the system message. */
function contextNote(dropped: number, keptRecent: number): string {
  return (
    `\n\n<context_note>\nThis conversation is long: ${dropped} earlier message${dropped === 1 ? "" : "s"} ` +
    `were omitted for length. The first exchange and the most recent ${keptRecent} messages are included ` +
    `verbatim. If the user refers to something no longer visible, say you may have lost that detail and ` +
    `ask them to restate it — do not guess.\n</context_note>`
  );
}

/**
 * messages[0] must be the system message; the rest is history + the new user
 * turn (buildMessages' shape). Returns the same array when it fits (no copy),
 * so callers can cheaply detect "untouched".
 */
export function compileChain(messages: GatewayMessage[], modelId: string, numTools: number): CompiledChain {
  const system = messages[0];
  const history = messages.slice(1);
  const sysText = typeof system?.content === "string" ? system.content : "";
  const sysTokens = estimateTokens(sysText) + PER_MESSAGE_OVERHEAD;
  const total = MODEL_CONTEXT_LIMITS[modelId] ?? DEFAULT_CONTEXT_LIMIT;
  const toolReserve = numTools * PER_TOOL_TOKENS;
  let remaining = total - sysTokens - toolReserve - OUTPUT_RESERVE;
  if (remaining < MESSAGES_FLOOR) remaining = MESSAGES_FLOOR;

  const historyTokens = history.reduce((s, m) => s + messageTokens(m), 0);
  const base = {
    totalMessages: history.length,
    budget: { total, system: sysTokens, toolReserve, outputReserve: OUTPUT_RESERVE, messagesRemaining: remaining },
  };
  if (historyTokens <= remaining) {
    return { messages, compacted: false, dropped: 0, sentMessages: history.length, historyTokens, ...base };
  }

  const units = toUnits(history);
  // Head: the first user message and the reply that answered it (units 0..1,
  // when they are the plain first exchange). Never more than 2 units.
  let headCount = 0;
  if (units.length > 0 && units[0]!.messages[0]!.role === "user") {
    headCount = 1;
    if (units.length > 1 && units[1]!.messages[0]!.role === "assistant" && !units[1]!.messages[0]!.tool_calls) headCount = 2;
  }
  const head = units.slice(0, headCount);
  const headTokens = head.reduce((s, u) => s + u.tokens, 0);
  const noteReserve = 120; // the context note joins the system message

  // Tail: newest units first until the budget is spent. The LAST unit (the
  // new user turn) always ships, even if it alone overflows — a system-only
  // request dead-ends the loop (chat-edge's rule).
  let used = headTokens + noteReserve;
  const tailStart = headCount;
  let cut = units.length; // first kept tail unit index
  for (let i = units.length - 1; i >= tailStart; i--) {
    const u = units[i]!;
    if (used + u.tokens > remaining && cut < units.length) break;
    if (used + u.tokens > remaining && cut === units.length && i < units.length - 1) break;
    used += u.tokens;
    cut = i;
  }
  const tail = units.slice(Math.max(cut, tailStart));
  const kept = [...head, ...tail];
  const keptMessages = kept.flatMap((u) => u.messages);
  const dropped = history.length - keptMessages.length;
  if (dropped <= 0) {
    return { messages, compacted: false, dropped: 0, sentMessages: history.length, historyTokens, ...base };
  }
  const keptRecent = tail.reduce((s, u) => s + u.messages.length, 0);
  const newSystem: GatewayMessage = { ...system!, content: sysText + contextNote(dropped, keptRecent) };
  const out: GatewayMessage[] = [newSystem, ...keptMessages];
  return {
    messages: out,
    compacted: true,
    dropped,
    sentMessages: keptMessages.length,
    historyTokens: keptMessages.reduce((s, m) => s + messageTokens(m), 0),
    ...base,
  };
}

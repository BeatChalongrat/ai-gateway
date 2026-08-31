// FIX33 A-D2 / A-D5 — the desktop context manager: the one place that
// decides "does this turn compact, what gets summarised first, what is
// injected, what is the user told". turn-coordinator.ts calls compile() and
// manualCompact() and otherwise knows nothing about summaries (§3.6). The
// summariser and the store sit behind interfaces, so routing either through
// chat-edge later touches nothing here.
//
// Order of operations on a turn (mirrors chat-edge's ContextCompiler):
//   1. plan — the pure compiler decides, with the CURRENT state block as a
//      preamble, whether the history crosses the trigger and which middle
//      range would go (never the first exchange, never half a tool group).
//   2. if something would go that the state does not cover yet → tell the
//      user ("organising…", A-W9), fold that range into the state in
//      batches from the anchor, persist; a failed batch keeps the old state
//      and the anchor where it was.
//   3. re-plan with the refreshed block (it changes the budget) and hand the
//      coordinator the messages to send + the numbers for the events.
//
// No 'electron' import: unit-tested in plain Node with fakes.

import { type CompileOptions, type CompiledChain, compileChain, estimateTokens } from "./context-compiler";
import {
  COMPACT_TARGET_RATIO,
  COMPACT_TRIGGER_RATIO,
  CONTEXT_METER_SHOW_RATIO,
  type ConversationStateRecord,
  type ConversationStateStore,
  EXTRACTION_BATCH_LIMIT,
  EXTRACTION_MAX_BATCHES,
  type StateExtractor,
  emptyState,
  isStateEmpty,
  mergeExtraction,
  renderStateMessages,
} from "./conversation-state";
import type { GatewayMessage } from "./gateway-client";
import type { Logger } from "./logger";

export interface ContextBudgetEvent {
  used_ratio: number;
  history_tokens: number;
  messages_budget: number;
  threshold_tokens: number;
  messages_total: number;
  messages_sent: number;
  state_present: boolean;
  compacted: boolean;
  dropped: number;
  est_messages_remaining: number;
  /** The page shows the meter + compact button only when true (≥ 70 %). */
  show_meter: boolean;
  meter_show_ratio: number;
}

export interface ContextManagerDeps {
  store: ConversationStateStore;
  /** null = summarise nothing (trim + honest note only, FIX32 behaviour). */
  extractor: StateExtractor | null;
  log: Logger;
  triggerRatio?: number;
  targetRatio?: number;
  /** Turn events to the page — the same channel as compaction_notice. */
  emit: (conversationId: string, event: string, data: unknown) => void;
}

export interface CompiledTurn extends CompiledChain {
  statePresent: boolean;
  extractionRan: boolean;
  budgetEvent: ContextBudgetEvent;
}

export class ContextManager {
  private readonly trigger: number;
  private readonly target: number;
  constructor(private readonly deps: ContextManagerDeps) {
    this.trigger = clampRatio(deps.triggerRatio, COMPACT_TRIGGER_RATIO);
    this.target = Math.min(clampRatio(deps.targetRatio, COMPACT_TARGET_RATIO), this.trigger);
  }

  /** The state a conversation has (null when none / empty). */
  state(conversationId: string): ConversationStateRecord | null {
    const s = this.deps.store.get(conversationId);
    return s && !isStateEmpty(s) ? s : null;
  }

  /**
   * messages[0] is the system message; the rest is the history + the new
   * user turn (buildMessages' shape). Returns what to SEND. The stored chain
   * is never touched here.
   */
  async compile(conversationId: string, messages: GatewayMessage[], model: string, numTools: number, signal: AbortSignal): Promise<CompiledTurn> {
    let state = this.deps.store.get(conversationId);
    const history = messages.slice(1);
    let plan = this.plan(messages, model, numTools, state);
    let extractionRan = false;

    if (plan.compacted && plan.dropEnd > 0 && this.deps.extractor) {
      const anchor = Math.min(Math.max(state?.message_count_at_summary ?? 0, 0), history.length);
      if (anchor < plan.dropEnd) {
        extractionRan = true;
        // A-W9 — the pause has a name.
        this.deps.emit(conversationId, "compaction_status", { status: "summarizing" });
        state = await this.fold(conversationId, model, history, anchor, plan.dropEnd, state, signal);
        // The block changed the budget: plan again against it.
        plan = this.plan(messages, model, numTools, state);
      }
    }

    const statePresent = !isStateEmpty(state);
    const budgetEvent = this.budgetEvent(plan, statePresent);
    this.deps.emit(conversationId, "context_budget", budgetEvent);
    return { ...plan, statePresent, extractionRan, budgetEvent };
  }

  /** Dry run for the page's context.info op — no extraction, no events. */
  info(conversationId: string, messages: GatewayMessage[], model: string, numTools: number): ContextBudgetEvent {
    const state = this.deps.store.get(conversationId);
    const plan = this.plan(messages, model, numTools, state);
    return this.budgetEvent(plan, !isStateEmpty(state));
  }

  /**
   * A-D5 / A-W7 — "summarise and continue": fold EVERYTHING from the anchor
   * to the newest message now (with the user's instructions), and — only if
   * that produced a summary — mark the newest message as the boundary the
   * next turn drops up to. A failed summary drops nothing.
   */
  async manualCompact(conversationId: string, history: readonly GatewayMessage[], model: string, instructions: string, signal: AbortSignal): Promise<{ ok: true; state: ConversationStateRecord } | { ok: false; error: string }> {
    if (!this.deps.extractor) return { ok: false, error: "no-extractor" };
    if (history.length === 0) return { ok: false, error: "empty" };
    const existing = this.deps.store.get(conversationId);
    const anchor = Math.min(Math.max(existing?.message_count_at_summary ?? 0, 0), history.length);
    const instr = instructions.trim() || existing?.compaction_instructions || "";
    this.deps.emit(conversationId, "compaction_status", { status: "summarizing" });
    let state = existing;
    if (anchor < history.length) {
      state = await this.fold(conversationId, model, history, anchor, history.length, existing, signal, instr);
    }
    if (!state || isStateEmpty(state)) return { ok: false, error: "no-summary" };
    if ((state.message_count_at_summary ?? 0) < history.length) {
      // A batch failed part-way: the summary does not cover the newest
      // messages, so a boundary there would drop un-summarised context.
      return { ok: false, error: "coverage-incomplete" };
    }
    state.manual_compact_boundary_index = history.length - 1;
    if (instr) state.compaction_instructions = instr;
    this.deps.store.put(state);
    this.deps.log.info(`MANUAL COMPACT conv=${conversationId}: boundary=${state.manual_compact_boundary_index} folded_to=${state.message_count_at_summary}`);
    return { ok: true, state };
  }

  // ── internals ──
  private plan(messages: GatewayMessage[], model: string, numTools: number, state: ConversationStateRecord | null): CompiledChain {
    const opts: CompileOptions = {
      triggerRatio: this.trigger,
      targetRatio: this.target,
      preamble: state && !isStateEmpty(state) ? renderStateMessages(state) : [],
      manualBoundaryIndex: state?.manual_compact_boundary_index ?? null,
      summaryPresent: !!state && !isStateEmpty(state),
    };
    return compileChain(messages, model, numTools, opts);
  }

  private budgetEvent(plan: CompiledChain, statePresent: boolean): ContextBudgetEvent {
    const budget = plan.budget.messagesRemaining;
    const used = budget > 0 ? plan.historyTokensLoaded / budget : 0;
    const avg = plan.totalMessages > 0 ? plan.historyTokensLoaded / plan.totalMessages : 0;
    const est = avg > 0 && plan.thresholdTokens > plan.historyTokensLoaded ? Math.floor((plan.thresholdTokens - plan.historyTokensLoaded) / avg) : 0;
    return {
      used_ratio: used,
      history_tokens: plan.historyTokensLoaded,
      messages_budget: budget,
      threshold_tokens: plan.thresholdTokens,
      messages_total: plan.totalMessages,
      messages_sent: plan.sentMessages,
      state_present: statePresent,
      compacted: plan.compacted,
      dropped: plan.dropped,
      est_messages_remaining: est,
      show_meter: used >= CONTEXT_METER_SHOW_RATIO,
      meter_show_ratio: CONTEXT_METER_SHOW_RATIO,
    };
  }

  /** Fold history[from, to) into the state in batches. Never wipes on failure. */
  private async fold(conversationId: string, model: string, history: readonly GatewayMessage[], from: number, to: number, existing: ConversationStateRecord | null, signal: AbortSignal, instructions?: string): Promise<ConversationStateRecord | null> {
    let state = existing;
    let cursor = from;
    for (let i = 0; i < EXTRACTION_MAX_BATCHES && cursor < to; i++) {
      const end = Math.min(cursor + EXTRACTION_BATCH_LIMIT, to);
      const slice = history.slice(cursor, end);
      const res = await this.deps.extractor!.extract({
        conversationId,
        model,
        existing: state,
        messages: slice,
        instructions: instructions ?? state?.compaction_instructions ?? "",
        signal,
      });
      if (!res.ok) {
        this.deps.log.warn(`COMPACTION extraction failed for ${conversationId} at [${cursor},${end}): ${res.error} — state kept, anchor stays ${cursor}`);
        break;
      }
      const merged = mergeExtraction(state, res.state, conversationId);
      merged.message_count_at_summary = end;
      if (instructions) merged.compaction_instructions = instructions;
      // Carry the manual boundary through a merge.
      merged.manual_compact_boundary_index = state?.manual_compact_boundary_index ?? merged.manual_compact_boundary_index ?? null;
      this.deps.store.put(merged);
      state = merged;
      cursor = end;
      this.deps.log.info(`COMPACTION folded conv=${conversationId} [${cursor - slice.length},${cursor}) → state (${estimateTokens(JSON.stringify(state.structured_summary))} summary tokens)${res.usage ? ` cost ${res.usage.promptTokens}+${res.usage.outputTokens} tokens` : ""}`);
    }
    return state ?? (existing ? existing : null) ?? null;
  }
}

function clampRatio(v: number | undefined, dflt: number): number {
  return typeof v === "number" && v > 0 && v <= 1 ? v : dflt;
}

export { emptyState };

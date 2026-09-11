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
// FIX104 — what the web learned in FIX102, applied here:
//   · the budget is measured against a BudgetBasis (the catalogue's window
//     for the model that answers), not a model id looked up in a local map;
//   · the meter reports the request the turn SENDS (chat-edge's
//     contextMeter): used_ratio is sent ÷ budget and never above 1,
//     `trimming` replaces the percent once older messages are left out;
//   · a trim is described by a summary status (chat-edge's summaryStatusOf),
//     and the user is told only when no summary stands in for everything
//     left out (chat-edge's LossNotice);
//   · a conversation with memory off keeps no summary, as on the web.
//
// No 'electron' import: unit-tested in plain Node with fakes.

import type { BudgetBasis } from "./context-budget";
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
  /** FIX104 (chat-edge FIX102): the SENT history over the budget, clamped to 1. */
  used_ratio: number;
  /** The newest message alone does not fit; used_ratio was clamped. */
  over_budget: boolean;
  /** Older messages are left out of what is sent — the page shows "sees N of M" instead of a percent. */
  trimming: boolean;
  history_tokens: number;
  sent_tokens: number;
  messages_budget: number;
  threshold_tokens: number;
  messages_total: number;
  messages_sent: number;
  state_present: boolean;
  compacted: boolean;
  dropped: number;
  est_messages_remaining: number;
  /** The page shows the meter + compact button only when true (trimming, or ≥ 70 %). */
  show_meter: boolean;
  meter_show_ratio: number;
  /** The model the budget was measured against ("" = none known → the default). */
  budget_model: string;
  /** Its advertised window from the catalogue (0 = the default was used). */
  budget_window: number;
  total_budget: number;
  output_reserve: number;
  /** FIX105 Part B: transcript rows never loaded — chat-edge's omitted_older. */
  omitted_older: number;
  /** FIX105: the first exchange was left out of what is sent (too large to pin, or no room for the newest message). */
  head_dropped: boolean;
  /** FIX105: the newest message alone is larger than the whole messages budget. */
  newest_over_budget: boolean;
}

/** chat-edge's Summary* values (FIX102 Part C). */
export type SummaryStatus = "complete" | "partial" | "failed" | "off" | "pending";

/** The `compaction_notice` turn event — the web's CompactionNotice payload. */
export interface CompactionLossNotice {
  dropped: number;
  sent: number;
  total: number;
  summary: "failed" | "partial";
}

/** One turn's inputs to the budget. */
export interface TurnBudget {
  /** The chat model — the summariser uses it (unchanged by FIX104). */
  model: string;
  basis: BudgetBasis;
  numTools: number;
  /** The conversation's memory switch; absent means on. */
  memoryEnabled?: boolean;
  /** FIX105 Part B: older transcript rows the host never loaded (beyond its load ceiling). */
  omittedOlder?: number;
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
  /** What stands in for the messages left out; "" when nothing was. */
  summaryStatus: SummaryStatus | "";
  /** Non-null when older messages were left out and no summary covers all of them. */
  lossNotice: CompactionLossNotice | null;
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
  async compile(conversationId: string, messages: GatewayMessage[], turn: TurnBudget, signal: AbortSignal): Promise<CompiledTurn> {
    const memoryOn = turn.memoryEnabled !== false;
    let state = memoryOn ? this.deps.store.get(conversationId) : null;
    const history = messages.slice(1);
    let plan = this.plan(messages, turn, state, this.noteStatus(memoryOn, false, false, state, turn));
    let extractionRan = false;
    let extractionOk = false;

    if (memoryOn && plan.compacted && plan.dropEnd > 0 && this.deps.extractor) {
      const anchor = Math.min(Math.max(state?.message_count_at_summary ?? 0, 0), history.length);
      if (anchor < plan.dropEnd) {
        extractionRan = true;
        const coverTo = plan.dropEnd;
        // A-W9 — the pause has a name.
        this.deps.emit(conversationId, "compaction_status", { status: "summarizing" });
        state = await this.fold(conversationId, turn.model, history, anchor, coverTo, state, signal);
        extractionOk = (state?.message_count_at_summary ?? 0) >= coverTo;
        // The block changed the budget: plan again against it.
        plan = this.plan(messages, turn, state, this.noteStatus(memoryOn, true, extractionOk, state, turn));
      }
    }

    const statePresent = !isStateEmpty(state);
    let summaryStatus: SummaryStatus | "" = plan.compacted ? summaryStatusOf(memoryOn, this.deps.extractor !== null, extractionRan, false, extractionOk, statePresent) : "";
    // FIX105 Part B — rows the host never loaded cannot be in a summary it
    // wrote: a summary that covers everything LOADED is still partial.
    if (summaryStatus === "complete" && plan.omittedOlder > 0) summaryStatus = "partial";
    const budgetEvent = this.budgetEvent(plan, statePresent);
    this.deps.emit(conversationId, "context_budget", budgetEvent);
    const lossNotice: CompactionLossNotice | null =
      plan.compacted && (summaryStatus === "failed" || summaryStatus === "partial")
        ? { dropped: plan.dropped + plan.omittedOlder, sent: plan.sentMessages, total: plan.totalMessages + plan.omittedOlder, summary: summaryStatus }
        : null;
    return { ...plan, statePresent, extractionRan, budgetEvent, summaryStatus, lossNotice };
  }

  /** Dry run for the page's context.info op — no extraction, no events. */
  info(conversationId: string, messages: GatewayMessage[], turn: TurnBudget): ContextBudgetEvent {
    const state = turn.memoryEnabled !== false ? this.deps.store.get(conversationId) : null;
    const plan = this.plan(messages, turn, state);
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
  private plan(messages: GatewayMessage[], turn: TurnBudget, state: ConversationStateRecord | null, summaryStatus?: SummaryStatus): CompiledChain {
    const opts: CompileOptions = {
      triggerRatio: this.trigger,
      targetRatio: this.target,
      preamble: state && !isStateEmpty(state) ? renderStateMessages(state) : [],
      manualBoundaryIndex: state?.manual_compact_boundary_index ?? null,
      summaryPresent: !!state && !isStateEmpty(state),
      summaryStatus,
      omittedOlder: turn.omittedOlder,
    };
    return compileChain(messages, turn.basis, turn.numTools, opts);
  }

  /**
   * FIX107 — the summary status the context note states, known before the
   * final plan is built (compile() reports the same value afterwards). The note
   * used to say only "a summary is above" or "no summary is available"; like
   * chat-edge's it now also says when the summary is partial or failed.
   */
  private noteStatus(memoryOn: boolean, extractionRan: boolean, extractionOk: boolean, state: ConversationStateRecord | null, turn: TurnBudget): SummaryStatus {
    const status = summaryStatusOf(memoryOn, this.deps.extractor !== null, extractionRan, false, extractionOk, !isStateEmpty(state));
    return status === "complete" && Math.trunc(turn.omittedOlder ?? 0) > 0 ? "partial" : status;
  }

  /** chat-edge's contextMeter (handler/conversation.go), on the desktop's plan. */
  private budgetEvent(plan: CompiledChain, statePresent: boolean): ContextBudgetEvent {
    const budget = plan.budget.messagesRemaining;
    let used = budget > 0 ? plan.sentTokens / budget : 0;
    let overBudget = false;
    if (used > 1) {
      used = 1;
      overBudget = true;
    }
    const trimming = plan.compacted;
    let est = 0;
    if (!trimming && plan.totalMessages > 0 && plan.thresholdTokens > plan.historyTokensLoaded) {
      const avg = Math.trunc(plan.historyTokensLoaded / plan.totalMessages);
      if (avg > 0) est = Math.trunc((plan.thresholdTokens - plan.historyTokensLoaded) / avg);
    }
    return {
      used_ratio: used,
      over_budget: overBudget,
      trimming,
      history_tokens: plan.historyTokensLoaded,
      sent_tokens: plan.sentTokens,
      messages_budget: budget,
      threshold_tokens: plan.thresholdTokens,
      messages_total: plan.totalMessages,
      // The conversation's own messages only; the state pair is not counted
      // (the page's messagesSeen subtracts it when state_injected says so —
      // this event never counts it in the first place).
      messages_sent: plan.sentMessages,
      state_present: statePresent,
      compacted: plan.compacted,
      dropped: plan.dropped,
      est_messages_remaining: est,
      show_meter: trimming || used >= CONTEXT_METER_SHOW_RATIO,
      meter_show_ratio: CONTEXT_METER_SHOW_RATIO,
      budget_model: plan.budget.model,
      budget_window: plan.budget.window,
      total_budget: plan.budget.total,
      output_reserve: plan.budget.outputReserve,
      omitted_older: plan.omittedOlder,
      head_dropped: plan.headDropped,
      newest_over_budget: plan.newestOverBudget,
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

/**
 * chat-edge's summaryStatusOf (context_compiler.go), argument for argument.
 * memoryEnabled: the conversation's switch; summariser: an extractor is
 * wired; needed: this plan's drop range was not yet covered; ok: the fold
 * covered it.
 */
export function summaryStatusOf(memoryEnabled: boolean, summariser: boolean, needed: boolean, dryRun: boolean, ok: boolean, statePresent: boolean): SummaryStatus {
  if (!memoryEnabled || (!summariser && !statePresent)) return "off";
  if (needed && dryRun) return "pending";
  if (needed && !ok && statePresent) return "partial";
  if (needed && !ok) return "failed";
  if (statePresent) return "complete";
  return "failed";
}

function clampRatio(v: number | undefined, dflt: number): number {
  return typeof v === "number" && v > 0 && v <= 1 ? v : dflt;
}

export { emptyState };

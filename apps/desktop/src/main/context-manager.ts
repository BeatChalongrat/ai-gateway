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
import { type CompileOptions, type CompiledChain, compileChain } from "./context-compiler";
import {
  COMPACT_TARGET_RATIO,
  COMPACT_TRIGGER_RATIO,
  CONTEXT_METER_SHOW_RATIO,
  type ConversationStateRecord,
  type ConversationStateStore,
  adoptServerState,
  anchorStateOnChain,
  emptyState,
  isStateEmpty,
  renderStateMessages,
} from "./conversation-state";
import { countVisibleRows } from "./chain-freshness";
import type { GatewayMessage } from "./gateway-client";
import type { Logger } from "./logger";
import type { CompactionTrigger } from "./chat-edge-client";
import type { ConversationSummariser } from "./server-summariser";

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
  /** FIX111 — the server, through POST /compact. null = summarise nothing
   *  (trim + honest note only, FIX32 behaviour; DESKTOP_COMPACTION_SUMMARY=off). */
  summariser: ConversationSummariser | null;
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
  /** FIX111 — the chain's visible-row count at the last server request per
   *  conversation: a second request with nothing new imported since would
   *  fold nothing and is not made (requirement: never fire every turn). */
  private readonly lastCompact = new Map<string, number>();
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

    if (memoryOn && plan.compacted && plan.dropEnd > 0 && this.deps.summariser) {
      const anchor = Math.min(Math.max(state?.message_count_at_summary ?? 0, 0), history.length);
      if (anchor < plan.dropEnd) {
        extractionRan = true;
        const coverTo = plan.dropEnd;
        // A-W9 — the pause has a name.
        this.deps.emit(conversationId, "compaction_status", { status: "summarizing" });
        // FIX111 — the server folds everything it holds and sets the boundary
        // there; the re-plan below then drops up to it (server-summariser.ts).
        state = await this.summarise(conversationId, history, state, state?.compaction_instructions ?? "", "auto", signal);
        extractionOk = (state?.message_count_at_summary ?? 0) >= coverTo;
        // The block changed the budget: plan again against it.
        plan = this.plan(messages, turn, state, this.noteStatus(memoryOn, true, extractionOk, state, turn));
      }
    }

    const statePresent = !isStateEmpty(state);
    let summaryStatus: SummaryStatus | "" = plan.compacted ? summaryStatusOf(memoryOn, this.deps.summariser !== null, extractionRan, false, extractionOk, statePresent) : "";
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

  /**
   * FIX109 Part B — the chain this conversation's state indexed is gone: the
   * host is building the turn from the server transcript (no local chain, or
   * one the server has moved past — Part A). Every anchor here is a chain
   * index, so on a different chain it names a different message; kept as it
   * was, a fold would resume PAST rows the summary never covered and the
   * trim would drop them unsummarised.
   *
   *   · the server has a state of its own → adopt it, its anchors translated
   *     through the transcript's row ids (adoptServerState): exact against
   *     the very rows now in the chain, and it covers the web turns the
   *     local summary never saw;
   *   · otherwise, a local summary stays (it is still true of the
   *     conversation) with its anchor at 0 and no boundary: re-folded from
   *     the start on the next compaction, at the price of tokens, never of
   *     content.
   *
   * A memory-off conversation keeps no summary on either surface (FIX104),
   * so nothing is adopted for one; a stale local anchor is still reset.
   * Idempotent — the page's context.info dry run may call it before the turn.
   */
  rebase(conversationId: string, rowIds: readonly string[], serverState: Record<string, unknown> | null, memoryEnabled = true): "adopted" | "reset" | "none" {
    const adopted = memoryEnabled ? adoptServerState(conversationId, serverState, rowIds) : null;
    if (adopted) {
      this.deps.store.put(adopted);
      this.deps.log.info(`STATE conv=${conversationId}: adopted the server summary — anchor ${adopted.message_count_at_summary} of ${rowIds.length} rows${adopted.manual_compact_boundary_index !== null ? `, boundary ${adopted.manual_compact_boundary_index}` : ""}`);
      return "adopted";
    }
    const local = this.deps.store.get(conversationId);
    if (local && ((local.message_count_at_summary ?? 0) > 0 || local.manual_compact_boundary_index !== null)) {
      local.message_count_at_summary = 0;
      local.manual_compact_boundary_index = null;
      this.deps.store.put(local);
      return "reset";
    }
    return "none";
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
    void model; // FIX111 — the server picks the summarising model
    if (!this.deps.summariser) return { ok: false, error: "no-summariser" };
    if (history.length === 0) return { ok: false, error: "empty" };
    const existing = this.deps.store.get(conversationId);
    // Requirement 6: the user's instructions, else the ones on file, else nothing — never invented here.
    const instr = instructions.trim() || existing?.compaction_instructions || "";
    this.deps.emit(conversationId, "compaction_status", { status: "summarizing" });
    // A manual request is the user's ask: it always goes to the server.
    this.lastCompact.delete(conversationId);
    const state = await this.summarise(conversationId, history, existing, instr, "manual", signal);
    if (!state || isStateEmpty(state)) return { ok: false, error: "no-summary" };
    if (state.manual_compact_boundary_index === null) return { ok: false, error: "no-boundary" };
    if ((state.message_count_at_summary ?? 0) < history.length) {
      // The server set its boundary but its fold stopped short (batch bound
      // or a failed pass): the boundary is on file on both surfaces — the
      // next turn drops to it with a partial summary and says so.
      this.deps.log.warn(`MANUAL COMPACT conv=${conversationId}: the server's summary covers ${state.message_count_at_summary} of ${history.length} chain messages`);
      return { ok: false, error: "coverage-incomplete" };
    }
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
    const status = summaryStatusOf(memoryOn, this.deps.summariser !== null, extractionRan, false, extractionOk, !isStateEmpty(state));
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

  /**
   * FIX111 — ask the server to summarise everything it holds (POST /compact),
   * then place its id-anchored state on this chain by row count. `history`
   * is the chain as built for the turn: what was imported through the last
   * turn, plus (in compile) the new user message that is not on the server
   * yet — the count-based placement never reaches past what the server
   * folded. Returns the existing state untouched when the server could not
   * summarise, so a turn goes out trimmed with the honest note rather than
   * not at all. Never throws.
   */
  private async summarise(conversationId: string, history: readonly GatewayMessage[], existing: ConversationStateRecord | null, instructions: string, trigger: CompactionTrigger, signal: AbortSignal): Promise<ConversationStateRecord | null> {
    if (!this.deps.summariser) return existing;
    const localVisible = countVisibleRows(history);
    if (this.lastCompact.get(conversationId) === localVisible) {
      this.deps.log.info(`SUMMARY conv=${conversationId}: server compact not requested — nothing imported since the last request (${localVisible} visible rows)`);
      return existing;
    }
    this.lastCompact.set(conversationId, localVisible);
    let res;
    try {
      res = await this.deps.summariser.compact(conversationId, instructions, trigger, signal);
    } catch (err) {
      this.deps.log.warn(`SUMMARY conv=${conversationId}: server compact threw: ${(err as Error).message} — state kept`);
      return existing;
    }
    if (!res) return existing;
    const adopted = adoptServerState(conversationId, res.state, res.rowIds);
    if (!adopted) {
      this.deps.log.warn(`SUMMARY conv=${conversationId}: the server returned no summary — state kept`);
      return existing;
    }
    const upTo = typeof res.state["summarized_up_to_message_id"] === "string" ? (res.state["summarized_up_to_message_id"] as string) : null;
    // A row past the ceiling is older than everything loaded: the server covers all of it.
    const rowsUpTo = upTo ? res.rowIds.indexOf(upTo) : -1;
    const rowsBoundary = res.boundaryMessageId ? res.rowIds.indexOf(res.boundaryMessageId) : -1;
    const coveredRows = rowsUpTo >= 0 ? rowsUpTo + 1 : upTo && res.rowsTruncated ? res.rowIds.length : 0;
    const boundaryRows = rowsBoundary >= 0 ? rowsBoundary + 1 : res.boundaryMessageId ? res.rowIds.length : 0;
    anchorStateOnChain(adopted, history, coveredRows, boundaryRows);
    this.deps.store.put(adopted);
    this.deps.log.info(`SUMMARY conv=${conversationId}: server summary adopted — covers ${coveredRows} of ${res.rowIds.length} server rows → chain anchor ${adopted.message_count_at_summary} of ${history.length}, boundary ${adopted.manual_compact_boundary_index ?? "none"}`);
    return adopted;
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

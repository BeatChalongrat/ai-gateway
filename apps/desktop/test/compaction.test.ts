// FIX33 §6.1 — the desktop compaction logic, provable without electron or a
// model: trigger at the threshold, the first exchange survives, tool groups
// stay atomic, no state → nothing breaks, a failed extraction keeps the old
// state and the anchor, the state rides as a USER-role pair (never in the
// system message — the DLP note in conversation-state.ts), the page gets the
// "organising" status before the extraction and the budget after it, and the
// meter is hidden below 70 %.
// FIX104: the budget comes from a BudgetBasis; the meter, the summary status,
// the loss notice and memory-off follow chat-edge (FIX102).
import { describe, expect, it } from "vitest";

import type { BudgetBasis } from "../src/main/context-budget";
import { compileChain, estimateTokens } from "../src/main/context-compiler";
import { ContextManager, type TurnBudget, summaryStatusOf } from "../src/main/context-manager";
import {
  CONTEXT_METER_SHOW_RATIO,
  COMPACT_TARGET_RATIO,
  COMPACT_TRIGGER_RATIO,
  type ExtractionInput,
  type ExtractionResult,
  MemoryConversationStateStore,
  type StateExtractor,
  buildExtractionUserBody,
  emptyState,
  parseExtraction,
  renderStateMessages,
} from "../src/main/conversation-state";
import type { GatewayMessage } from "../src/main/gateway-client";

const log = { info: () => {}, warn: () => {}, error: () => {}, attachFile: () => {} } as unknown as import("../src/main/logger").Logger;
const TERRA: BudgetBasis = { model: "gpt-5.6-terra", window: 128_000, outputTokens: 8192 };
const turn = (over: Partial<TurnBudget> = {}): TurnBudget => ({ model: "gpt-5.6-terra", basis: TERRA, numTools: 0, ...over });
const sys = (): GatewayMessage => ({ role: "system", content: "s".repeat(400) });
const u = (t: string): GatewayMessage => ({ role: "user", content: t });
const a = (t: string): GatewayMessage => ({ role: "assistant", content: t });

/** A chain whose history sits at `ratio` of the model's messages budget. */
function chainAt(ratio: number, basis: BudgetBasis = TERRA): GatewayMessage[] {
  const probe = compileChain([sys(), u("x")], basis, 0);
  const budget = probe.budget.messagesRemaining;
  const out: GatewayMessage[] = [sys(), u("STANDING: end every answer with ผ่าน"), a("รับทราบ ผ่าน")];
  const per = 120; // ≈ tokens per message below
  const n = Math.floor((budget * ratio) / per);
  for (let i = 0; i < n; i++) out.push(i % 2 === 0 ? u(`msg ${i} ${"y".repeat(470)}`) : a(`reply ${i} ${"z".repeat(470)}`));
  return out;
}

class FakeExtractor implements StateExtractor {
  calls: ExtractionInput[] = [];
  constructor(private readonly reply: (input: ExtractionInput) => ExtractionResult) {}
  async extract(input: ExtractionInput): Promise<ExtractionResult> {
    this.calls.push(input);
    return this.reply(input);
  }
}
const okReply = (goal: string) => (): ExtractionResult => ({ ok: true, state: { current_goal: goal, structured_summary: { key_context: "cat มะขาม 4 yrs; deadline 15 ก.ย." } } });

function events() {
  const list: { event: string; data: unknown }[] = [];
  return { list, emit: (_c: string, event: string, data: unknown) => void list.push({ event, data }) };
}

describe("shared numbers", () => {
  it("trigger 0.85 / target 0.60 / meter 0.70 — the same values chat-edge and web-chat use", () => {
    expect(COMPACT_TRIGGER_RATIO).toBe(0.85);
    expect(COMPACT_TARGET_RATIO).toBe(0.6);
    expect(CONTEXT_METER_SHOW_RATIO).toBe(0.7);
  });
});

describe("compileChain with options (the pure plan)", () => {
  it("below the trigger: untouched, the very same array, meter numbers still reported", () => {
    const msgs = chainAt(0.5);
    const cc = compileChain(msgs, TERRA, 0, { triggerRatio: 0.85, targetRatio: 0.6 });
    expect(cc.messages).toBe(msgs);
    expect(cc.compacted).toBe(false);
    expect(cc.thresholdTokens).toBe(Math.floor(cc.budget.messagesRemaining * 0.85));
    expect(cc.historyTokensLoaded).toBeGreaterThan(0);
  });

  it("at 90 % — above the trigger, BELOW overflow — it compacts to ≤ 60 %, keeps the first exchange and the newest turn", () => {
    const msgs = chainAt(0.9);
    const cc = compileChain(msgs, TERRA, 0, { triggerRatio: 0.85, targetRatio: 0.6 });
    expect(cc.compacted).toBe(true);
    expect(cc.historyTokens).toBeLessThanOrEqual(Math.floor(cc.budget.messagesRemaining * 0.6));
    expect(cc.messages[1]).toBe(msgs[1]);
    expect(cc.messages[2]).toBe(msgs[2]);
    expect(cc.messages[cc.messages.length - 1]).toBe(msgs[msgs.length - 1]);
    expect(cc.dropStart).toBe(2);
    expect(cc.dropEnd).toBeGreaterThan(2);
    expect(cc.messages[0].content as string).toContain("<context_note>");
    expect(cc.messages[0].content as string).toContain("do not guess");
  });

  it("without options the FIX32 behaviour is unchanged (overflow only)", () => {
    const msgs = chainAt(0.9);
    expect(compileChain(msgs, TERRA, 0).compacted).toBe(false);
  });

  it("the preamble (state pair) rides after the system message, before the preserved head, and is budgeted", () => {
    const msgs = chainAt(0.9);
    const pre = renderStateMessages({ ...emptyState("c"), current_goal: "plan the trip" });
    const cc = compileChain(msgs, TERRA, 0, { triggerRatio: 0.85, targetRatio: 0.6, preamble: pre, summaryPresent: true });
    expect(cc.messages[0].role).toBe("system");
    expect(cc.messages[1].role).toBe("user");
    expect(cc.messages[1].content as string).toContain("<conversation_state>");
    expect(cc.messages[2].role).toBe("assistant");
    expect(cc.messages[3]).toBe(msgs[1]); // the standing instruction
    // The SUMMARY TEXT never enters the system message (the note only names the tag).
    expect(cc.messages[0].content as string).not.toContain("plan the trip");
    expect(cc.messages[0].content as string).toContain("A summary of the older part is in <conversation_state>");
    // FIX104: the pair counts toward what is sent, like chat-edge's SentTokens.
    expect(cc.stateInjected).toBe(true);
    expect(cc.sentTokens).toBeGreaterThan(cc.historyTokens);
  });

  it("a manual boundary drops everything at or before it (head excepted) even when it all fits", () => {
    const msgs = [sys(), u("first"), a("hi"), u("q1"), a("a1"), u("q2"), a("a2"), u("q3")];
    const cc = compileChain(msgs, TERRA, 0, { triggerRatio: 0.85, targetRatio: 0.6, manualBoundaryIndex: 4 });
    expect(cc.compacted).toBe(true);
    // boundary index 4 = "q2": everything at or before it goes (q1, a1, q2), the head stays.
    expect(cc.messages.map((m) => m.content)).toEqual([cc.messages[0].content, "first", "hi", "a2", "q3"]);
    expect(cc.dropStart).toBe(2);
    expect(cc.dropEnd).toBe(5);
  });

  it("tool groups are atomic under the ratio trigger too", () => {
    const msgs = chainAt(0.9);
    // Splice a tool group into the middle.
    const call: GatewayMessage = { role: "assistant", content: "", tool_calls: [{ id: "c1", type: "function", function: { name: "read_file", arguments: "{}" } }] } as GatewayMessage;
    const result: GatewayMessage = { role: "tool", content: "x".repeat(400), tool_call_id: "c1" } as GatewayMessage;
    msgs.splice(Math.floor(msgs.length / 2), 0, call, result);
    const cc = compileChain(msgs, TERRA, 0, { triggerRatio: 0.85, targetRatio: 0.6 });
    const roles = cc.messages.map((m) => m.role);
    for (let i = 0; i < roles.length; i++) {
      if (roles[i] === "tool") expect(cc.messages[i - 1]!.role === "tool" || !!cc.messages[i - 1]!.tool_calls).toBe(true);
      if (cc.messages[i]!.tool_calls) expect(roles[i + 1]).toBe("tool");
    }
  });
});

describe("ContextManager", () => {
  it("no state, no extractor: trims with the honest note and reports the budget — nothing breaks", async () => {
    const ev = events();
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: null, log, emit: ev.emit });
    const out = await mgr.compile("c", chainAt(0.9), turn(), new AbortController().signal);
    expect(out.compacted).toBe(true);
    expect(out.statePresent).toBe(false);
    expect(out.extractionRan).toBe(false);
    expect(out.messages[0].content as string).toContain("No summary of the older part is available");
    expect(ev.list.map((e) => e.event)).toEqual(["context_budget"]);
    // chat-edge: no summariser and no state is "off" — the meter says so; no loss notice.
    expect(out.summaryStatus).toBe("off");
    expect(out.lossNotice).toBeNull();
  });

  it("crossing the trigger: says 'summarizing' first, folds exactly the range that will be dropped, injects the state as a USER pair, re-plans", async () => {
    const ev = events();
    const store = new MemoryConversationStateStore();
    const ex = new FakeExtractor(okReply("plan the trip"));
    const mgr = new ContextManager({ store, extractor: ex, log, emit: ev.emit });
    const msgs = chainAt(0.9);
    const out = await mgr.compile("c", msgs, turn(), new AbortController().signal);
    expect(out.extractionRan).toBe(true);
    expect(out.statePresent).toBe(true);
    expect(ev.list[0]!.event).toBe("compaction_status");
    expect((ev.list[0]!.data as { status: string }).status).toBe("summarizing");
    expect(ev.list[ev.list.length - 1]!.event).toBe("context_budget");
    // Folded from the anchor (0) up to the drop end, in ≤120-message batches.
    const folded = ex.calls.reduce((n, c) => n + c.messages.length, 0);
    expect(folded).toBe(store.get("c")!.message_count_at_summary);
    expect(folded).toBeGreaterThan(0);
    expect(ex.calls[0]!.messages[0]).toBe(msgs[1]); // starts at the oldest
    // The state pair rides as user/assistant right after the system message.
    expect(out.messages[1].role).toBe("user");
    expect(out.messages[1].content as string).toContain("<conversation_state>");
    expect(out.messages[1].content as string).toContain("plan the trip");
    expect(out.messages[0].content as string).not.toContain("plan the trip");
    // Head still survives after the state pair.
    expect(out.messages[3]).toBe(msgs[1]);
    expect(out.summaryStatus).toBe("complete");
    expect(out.lossNotice).toBeNull();
  });

  it("an incremental pass resumes from the anchor, not from zero", async () => {
    const ev = events();
    const store = new MemoryConversationStateStore();
    const ex = new FakeExtractor(okReply("g"));
    const mgr = new ContextManager({ store, extractor: ex, log, emit: ev.emit });
    const msgs = chainAt(0.9);
    await mgr.compile("c", msgs, turn(), new AbortController().signal);
    const anchor = store.get("c")!.message_count_at_summary;
    // Grow the conversation past the trigger again.
    const more = [...msgs, ...chainAt(0.5).slice(3)];
    ex.calls = [];
    await mgr.compile("c", more, turn(), new AbortController().signal);
    if (ex.calls.length > 0) expect(ex.calls[0]!.messages[0]).toBe(more[1 + anchor]);
    expect(ex.calls.every((c) => c.existing !== null)).toBe(true);
  });

  it("a failed extraction keeps the existing state AND the anchor; the turn still goes out trimmed, and says the summary is partial", async () => {
    const ev = events();
    const store = new MemoryConversationStateStore();
    store.put({ ...emptyState("c"), current_goal: "keep me", message_count_at_summary: 4 });
    const ex = new FakeExtractor(() => ({ ok: false, error: "gateway down" }));
    const mgr = new ContextManager({ store, extractor: ex, log, emit: ev.emit });
    const out = await mgr.compile("c", chainAt(0.9), turn(), new AbortController().signal);
    expect(out.compacted).toBe(true);
    expect(store.get("c")!.current_goal).toBe("keep me");
    expect(store.get("c")!.message_count_at_summary).toBe(4);
    expect(out.messages[1].content as string).toContain("keep me"); // the OLD state is still injected
    expect(out.summaryStatus).toBe("partial");
    expect(out.lossNotice).toEqual({ dropped: out.dropped, sent: out.sentMessages, total: out.totalMessages, summary: "partial" });
  });

  it("FIX104 — a failed first extraction trims and says no summary stands in (the web's compaction_notice)", async () => {
    const ev = events();
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: new FakeExtractor(() => ({ ok: false, error: "x" })), log, emit: ev.emit });
    const out = await mgr.compile("c", chainAt(0.9), turn(), new AbortController().signal);
    expect(out.compacted).toBe(true);
    expect(out.summaryStatus).toBe("failed");
    expect(out.lossNotice).toMatchObject({ summary: "failed", dropped: out.dropped });
    expect(out.lossNotice!.sent + out.lossNotice!.dropped).toBe(out.lossNotice!.total);
  });

  it("FIX104 — memory off: nothing is summarised, no stored state is injected, and nothing is announced (the web's temporary chat)", async () => {
    const ev = events();
    const store = new MemoryConversationStateStore();
    store.put({ ...emptyState("c"), current_goal: "from before", message_count_at_summary: 2 });
    const ex = new FakeExtractor(okReply("g"));
    const mgr = new ContextManager({ store, extractor: ex, log, emit: ev.emit });
    const out = await mgr.compile("c", chainAt(0.9), turn({ memoryEnabled: false }), new AbortController().signal);
    expect(out.compacted).toBe(true);
    expect(ex.calls).toHaveLength(0);
    expect(out.messages.some((m) => typeof m.content === "string" && m.content.includes("from before"))).toBe(false);
    expect(out.summaryStatus).toBe("off");
    expect(out.lossNotice).toBeNull();
    expect(ev.list.map((e) => e.event)).toEqual(["context_budget"]);
  });

  it("summaryStatusOf is chat-edge's table", () => {
    expect(summaryStatusOf(false, true, true, false, true, true)).toBe("off");
    expect(summaryStatusOf(true, false, false, false, false, false)).toBe("off");
    expect(summaryStatusOf(true, true, true, true, false, false)).toBe("pending");
    expect(summaryStatusOf(true, true, true, false, false, true)).toBe("partial");
    expect(summaryStatusOf(true, true, true, false, false, false)).toBe("failed");
    expect(summaryStatusOf(true, true, false, false, false, true)).toBe("complete");
    expect(summaryStatusOf(true, true, false, false, false, false)).toBe("failed");
  });

  it("a non-JSON reply is a failure, not a wipe", () => {
    expect(parseExtraction("Sure! Here is the summary…")).toBeNull();
    expect(parseExtraction('```json\n{"current_goal":"g"}\n```')).toEqual({ current_goal: "g" });
  });

  it("the extraction body carries the instructions, the existing state and the messages, tool rows named", () => {
    const body = buildExtractionUserBody({ ...emptyState("c"), current_goal: "old" }, [u("hello <<PHONE_1>>"), { role: "tool", content: "out", tool_call_id: "x" } as GatewayMessage], "keep code");
    expect(body).toContain("USER INSTRUCTIONS");
    expect(body).toContain("keep code");
    expect(body).toContain('"current_goal":"old"');
    expect(body).toContain("[user] hello <<PHONE_1>>");
    expect(body).toContain("[tool] out");
  });

  it("manualCompact folds to the newest message, sets the boundary, and the next compile drops up to it", async () => {
    const ev = events();
    const store = new MemoryConversationStateStore();
    const ex = new FakeExtractor(okReply("g"));
    const mgr = new ContextManager({ store, extractor: ex, log, emit: ev.emit });
    const msgs = [sys(), u("first"), a("hi"), u("q1"), a("a1"), u("q2"), a("a2")];
    const res = await mgr.manualCompact("c", msgs.slice(1), "gpt-5.6-terra", "keep the decisions", new AbortController().signal);
    expect(res.ok).toBe(true);
    const st = store.get("c")!;
    expect(st.manual_compact_boundary_index).toBe(5);
    expect(st.compaction_instructions).toBe("keep the decisions");
    expect(ex.calls[0]!.instructions).toBe("keep the decisions");
    const next = await mgr.compile("c", [...msgs, u("q3")], turn(), new AbortController().signal);
    expect(next.compacted).toBe(true);
    expect(next.messages.map((m) => m.role)).toEqual(["system", "user", "assistant", "user", "assistant", "user"]);
    expect(next.messages[5].content).toBe("q3");
    expect(next.messages[3].content).toBe("first");
  });

  it("manualCompact with a failing extractor sets NO boundary", async () => {
    const ev = events();
    const store = new MemoryConversationStateStore();
    const mgr = new ContextManager({ store, extractor: new FakeExtractor(() => ({ ok: false, error: "x" })), log, emit: ev.emit });
    const res = await mgr.manualCompact("c", [u("a"), a("b")], "gpt-5.6-terra", "", new AbortController().signal);
    expect(res.ok).toBe(false);
    expect(store.get("c")).toBeNull();
  });

  it("the meter is hidden below 70 % and shown from 70 % (spec addendum #1)", () => {
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: null, log, emit: () => {} });
    expect(mgr.info("c", chainAt(0.5), turn()).show_meter).toBe(false);
    expect(mgr.info("c", chainAt(0.75), turn()).show_meter).toBe(true);
    const i = mgr.info("c", chainAt(0.75), turn());
    expect(i.used_ratio).toBeGreaterThan(0.7);
    expect(i.est_messages_remaining).toBeGreaterThan(0);
    expect(i.trimming).toBe(false);
    expect(estimateTokens("ab")).toBeGreaterThanOrEqual(0);
  });

  it("FIX104 — once trimming, the meter reports what is SENT: ratio ≤ 1, trimming, no countdown (chat-edge's contextMeter)", () => {
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: null, log, emit: () => {} });
    const i = mgr.info("c", chainAt(3), turn()); // loaded history is 3× the budget
    expect(i.compacted).toBe(true);
    expect(i.trimming).toBe(true);
    expect(i.show_meter).toBe(true);
    expect(i.used_ratio).toBeLessThanOrEqual(1);
    expect(i.history_tokens).toBeGreaterThan(i.messages_budget); // the FIX33 ratio would have said ~300 %
    expect(i.sent_tokens).toBeLessThanOrEqual(i.messages_budget);
    expect(i.over_budget).toBe(false);
    expect(i.est_messages_remaining).toBe(0);
    expect(i.messages_sent).toBeLessThan(i.messages_total);
    expect(i).toMatchObject({ budget_model: "gpt-5.6-terra", budget_window: 128_000, total_budget: 115_000, output_reserve: 16_000 });
  });

  it("FIX104 — the newest message alone overflowing is over_budget, and the ratio still stops at 1", () => {
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: null, log, emit: () => {} });
    const i = mgr.info("c", [sys(), u("first"), a("hi"), u("x".repeat(600_000))], turn());
    expect(i.used_ratio).toBe(1);
    expect(i.over_budget).toBe(true);
  });
});

// FIX111 — the server is the one summariser.
//
// ChatEdgeSummariser asks chat-edge to compact (POST /compact) and returns
// the state row with the ids of the server's visible rows; the context
// manager places that id-anchored state on the local chain by row count.
// Pinned here: the placement arithmetic, the pager, every failure answering
// null (a turn then goes out trimmed with the honest note, never broken),
// and that memory-off never reaches the server.
import { describe, expect, it } from "vitest";

import { chainIndexAfterVisibleRows, countVisibleRows } from "../src/main/chain-freshness";
import { ContextManager } from "../src/main/context-manager";
import { anchorStateOnChain, emptyState, MemoryConversationStateStore } from "../src/main/conversation-state";
import type { BudgetBasis } from "../src/main/context-budget";
import type { GatewayMessage } from "../src/main/gateway-client";
import type { Logger } from "../src/main/logger";
import { ChatEdgeSummariser, isVisibleRow } from "../src/main/server-summariser";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
const u = (t: string): GatewayMessage => ({ role: "user", content: t });
const a = (t: string): GatewayMessage => ({ role: "assistant", content: t });
const call = (text: string | null): GatewayMessage => ({ role: "assistant", content: text, tool_calls: [{ id: "c", type: "function", function: { name: "run_python", arguments: "{}" } }] });
const tool = (t: string): GatewayMessage => ({ role: "tool", tool_call_id: "c", name: "run_python", content: t });

describe("FIX111 — placing a row-counted anchor on the local chain", () => {
  // Two tool turns, as the live run produced them: u, call, tool, a — imported as 2 rows each.
  const chain = [u("q1"), call(null), tool("out1"), a("17575"), u("q2"), call("Let me run it."), tool("out2"), a("75025"), u("q3")];

  it("counts what the server holds and ends a covered turn after its tool rows", () => {
    expect(countVisibleRows(chain)).toBe(5);
    expect(chainIndexAfterVisibleRows(chain, 0)).toBe(0);
    expect(chainIndexAfterVisibleRows(chain, 1)).toBe(1); // the user row alone — the turn went on to import text
    expect(chainIndexAfterVisibleRows(chain, 2)).toBe(4); // turn 1's text row covers its tool rows
    expect(chainIndexAfterVisibleRows(chain, 3)).toBe(5);
    expect(chainIndexAfterVisibleRows(chain, 4)).toBe(8); // turn 2's preamble text counts as the turn's row
    expect(chainIndexAfterVisibleRows(chain, 5)).toBe(9);
    expect(chainIndexAfterVisibleRows(chain, 99)).toBe(9);
  });

  it("a turn with no assistant text is covered through its tool rows by its user row", () => {
    const c = [u("q1"), call(null), tool("out"), u("q2"), a("ok")];
    expect(chainIndexAfterVisibleRows(c, 1)).toBe(3);
    expect(chainIndexAfterVisibleRows(c, 2)).toBe(4);
    expect(chainIndexAfterVisibleRows(c, 3)).toBe(5);
  });

  it("anchorStateOnChain: the server covering everything it holds anchors before the new user turn; a server behind covers less; never past the chain", () => {
    const s = anchorStateOnChain({ ...emptyState("c"), current_goal: "g" }, chain, 4, 4);
    expect(s.message_count_at_summary).toBe(8);
    expect(s.manual_compact_boundary_index).toBe(7);
    const behind = anchorStateOnChain({ ...emptyState("c"), current_goal: "g" }, chain, 2, 2);
    expect(behind.message_count_at_summary).toBe(4);
    expect(behind.manual_compact_boundary_index).toBe(3);
    const over = anchorStateOnChain({ ...emptyState("c"), current_goal: "g" }, chain, 40, 40);
    expect(over.message_count_at_summary).toBe(9);
    expect(over.manual_compact_boundary_index).toBe(8);
    const none = anchorStateOnChain({ ...emptyState("c"), current_goal: "g" }, chain, 0, 0);
    expect(none.message_count_at_summary).toBe(0);
    expect(none.manual_compact_boundary_index).toBeNull();
  });
});

type Row = { id: string; role: string; content: string | null; status: string };
function client(opts: { compact?: unknown | null; rows?: Row[]; failPage?: boolean; throwOnCompact?: boolean }) {
  const calls: { compact: { id: string; instructions: string; trigger?: string }[]; pages: number } = { compact: [], pages: 0 };
  const rows = opts.rows ?? [];
  return {
    calls,
    async compactConversation(id: string, instructions: string, trigger: string) {
      calls.compact.push({ id, instructions, trigger });
      if (opts.throwOnCompact) throw new Error("boom");
      return opts.compact === undefined ? null : (opts.compact as never);
    },
    async listMessagesPage(_id: string, limit: number, before?: string) {
      calls.pages++;
      if (opts.failPage) return null;
      const end = before ? rows.findIndex((m) => m.id === before) : rows.length;
      const start = Math.max(0, end - Math.min(limit, 100));
      return { messages: rows.slice(start, end), hasMore: start > 0 };
    },
  };
}
const ROWS: Row[] = [
  { id: "m1", role: "user", content: "q1", status: "completed" },
  { id: "m2", role: "assistant", content: "17575", status: "completed" },
  { id: "m3", role: "assistant", content: "", status: "error" }, // an errored row: not visible
  { id: "m4", role: "user", content: "q2", status: "completed" },
  { id: "m5", role: "assistant", content: "75025", status: "completed" },
];
const SERVER_STATE = { conversation_id: "c", current_goal: "compute things", structured_summary: { key_context: "17575, 75025" }, confirmed_decisions: [], active_artifacts: [], open_tasks: [], rejected_approaches: [], unresolved_questions: [], summarized_up_to_message_id: "m5", message_count_at_summary: 6, summary_token_count: 40, manual_compact_boundary_message_id: "m5", compaction_instructions: "" };

describe("FIX111 — ChatEdgeSummariser", () => {
  it("compacts, then reads the server's visible rows oldest first (errored rows left out) and reports the boundary", async () => {
    const c = client({ compact: { state: SERVER_STATE, boundary_message_id: "m5", coverage_reached: true, messages_total: 5 }, rows: ROWS });
    const res = await new ChatEdgeSummariser(c, quiet).compact("c", "keep numbers", "manual", new AbortController().signal);
    expect(c.calls.compact).toEqual([{ id: "c", instructions: "keep numbers", trigger: "manual" }]);
    expect(res).not.toBeNull();
    expect(res!.rowIds).toEqual(["m1", "m2", "m4", "m5"]);
    expect(res!.boundaryMessageId).toBe("m5");
    expect(res!.coverageReached).toBe(true);
    expect(res!.rowsTruncated).toBe(false);
    expect(isVisibleRow(ROWS[2] as never)).toBe(false);
  });

  it("the server refusing (null), an empty state, a page that cannot be read, or a throw → null, never a throw", async () => {
    const sig = new AbortController().signal;
    expect(await new ChatEdgeSummariser(client({}), quiet).compact("c", "", "auto", sig)).toBeNull();
    expect(await new ChatEdgeSummariser(client({ compact: { state: null } }), quiet).compact("c", "", "auto", sig)).toBeNull();
    expect(await new ChatEdgeSummariser(client({ compact: { state: SERVER_STATE }, rows: ROWS, failPage: true }), quiet).compact("c", "", "auto", sig)).toBeNull();
    const aborted = new AbortController();
    aborted.abort();
    const c = client({ compact: { state: SERVER_STATE }, rows: ROWS });
    expect(await new ChatEdgeSummariser(c, quiet).compact("c", "", "auto", aborted.signal)).toBeNull();
    expect(c.calls.compact).toHaveLength(0); // an aborted turn asks for nothing
  });
});

describe("FIX111 — the context manager with the server as summariser", () => {
  const TERRA: BudgetBasis = { model: "small-window", window: 16_384, outputTokens: 8192 };
  const sys = (): GatewayMessage => ({ role: "system", content: "s".repeat(400) });
  const big = (n: number) => `${"y".repeat(470)} ${n}`;
  /** A tool-heavy chain far over the trigger. */
  function chain(turns: number): GatewayMessage[] {
    const out: GatewayMessage[] = [sys(), u("first"), a("hi")];
    for (let i = 0; i < turns; i++) out.push(u(`q${i}`), call(null), tool(big(i)), a(`answer ${i}`));
    out.push(u("newest"));
    return out;
  }

  it("a throwing summariser leaves the turn intact: trimmed, honest note, no state", async () => {
    const store = new MemoryConversationStateStore();
    const mgr = new ContextManager({ store, summariser: new ChatEdgeSummariser(client({ throwOnCompact: true }), quiet), log: quiet, emit: () => {} });
    const msgs = chain(200);
    const out = await mgr.compile("c", msgs, { model: TERRA.model, basis: TERRA, numTools: 0 }, new AbortController().signal);
    expect(out.compacted).toBe(true);
    expect(out.summaryStatus).toBe("failed");
    expect(store.get("c")).toBeNull();
    expect(out.messages[out.messages.length - 1]).toBe(msgs[msgs.length - 1]);
  });

  it("memory off: the server is never asked, even far over the trigger", async () => {
    const c = client({ compact: { state: SERVER_STATE, boundary_message_id: "m5", coverage_reached: true }, rows: ROWS });
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), summariser: new ChatEdgeSummariser(c, quiet), log: quiet, emit: () => {} });
    const out = await mgr.compile("c", chain(200), { model: TERRA.model, basis: TERRA, numTools: 0, memoryEnabled: false }, new AbortController().signal);
    expect(out.compacted).toBe(true);
    expect(c.calls.compact).toHaveLength(0);
    expect(out.summaryStatus).toBe("off");
  });

  it("the instructions sent are the ones on file, else empty — never invented", async () => {
    const store = new MemoryConversationStateStore();
    store.put({ ...emptyState("c"), current_goal: "old", compaction_instructions: "keep the numbers", message_count_at_summary: 0 });
    const c = client({ compact: { state: { ...SERVER_STATE, compaction_instructions: "keep the numbers" }, boundary_message_id: "m5", coverage_reached: true }, rows: ROWS });
    const mgr = new ContextManager({ store, summariser: new ChatEdgeSummariser(c, quiet), log: quiet, emit: () => {} });
    await mgr.compile("c", chain(200), { model: TERRA.model, basis: TERRA, numTools: 0 }, new AbortController().signal);
    expect(c.calls.compact.map((x) => x.instructions)).toEqual(["keep the numbers"]);
    const fresh = new ContextManager({ store: new MemoryConversationStateStore(), summariser: new ChatEdgeSummariser(c, quiet), log: quiet, emit: () => {} });
    await fresh.compile("d", chain(200), { model: TERRA.model, basis: TERRA, numTools: 0 }, new AbortController().signal);
    expect(c.calls.compact[1]!.instructions).toBe("");
  });
});

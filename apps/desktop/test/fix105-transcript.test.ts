// FIX105 Part B — the desktop stops truncating in silence.
//
// A conversation with no local chain was read from chat-edge with one request
// that chat-edge caps at 100 rows, newest first. The page showed the whole
// conversation (it pages on its own) while the MODEL saw only the newest 100,
// and nothing said so. The host now pages back; rows beyond its ceiling are
// counted and said.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import type { BudgetBasis } from "../src/main/context-budget";
import { compileChain } from "../src/main/context-compiler";
import { ContextManager } from "../src/main/context-manager";
import { MemoryConversationStateStore, emptyState } from "../src/main/conversation-state";
import type { GatewayMessage } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const TYPHOON: BudgetBasis = { model: "Typhoon-S-ThaiLLM-8B-Instruct", window: 16_384, outputTokens: 8192 };
const RATIOS = { triggerRatio: 0.85, targetRatio: 0.6 };
const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
const sys = (): GatewayMessage => ({ role: "system", content: "s".repeat(4000) });
const u = (t: string): GatewayMessage => ({ role: "user", content: t });
const a = (t: string): GatewayMessage => ({ role: "assistant", content: t });

describe("FIX105 Part B — rows never loaded are counted and said", () => {
  it("omittedOlder makes a fitting request compacted, names the rows in the note, and asks for no fold", () => {
    const msgs = [sys(), u("first"), a("hi"), u("q")];
    const cc = compileChain(msgs, TYPHOON, 0, { ...RATIOS, omittedOlder: 150 });
    expect(cc.compacted).toBe(true);
    expect(cc.dropped).toBe(0);
    expect(cc.omittedOlder).toBe(150);
    expect([cc.dropStart, cc.dropEnd]).toEqual([0, 0]);
    expect(cc.messages[0]!.content as string).toContain("150 earlier messages are not included verbatim");
  });

  it("the meter and the loss notice count them; a summary of only the loaded part is partial", async () => {
    const store = new MemoryConversationStateStore();
    store.put({ ...emptyState("c"), current_goal: "plan", message_count_at_summary: 3 });
    const events: { event: string; data: unknown }[] = [];
    const mgr = new ContextManager({ store, extractor: null, log, emit: (_c, event, data) => void events.push({ event, data }) });
    const out = await mgr.compile("c", [sys(), u("first"), a("hi"), u("q")], { model: TYPHOON.model, basis: TYPHOON, numTools: 0, omittedOlder: 150 }, new AbortController().signal);
    expect(out.summaryStatus).toBe("partial");
    expect(out.lossNotice).toEqual({ dropped: 150, sent: 3, total: 153, summary: "partial" });
    expect(events.find((e) => e.event === "context_budget")!.data).toMatchObject({ omitted_older: 150, trimming: true });
  });
});

describe("FIX105 Part B — the host reads the whole transcript, page by page", () => {
  function coordinator(rows: number, opts: { failAt?: number } = {}) {
    const all = Array.from({ length: rows }, (_, i) => ({ id: `m${String(i).padStart(5, "0")}`, role: i % 2 === 0 ? "user" : "assistant", content: `row ${i}`, status: "completed" }));
    const calls: { limit: number; before?: string }[] = [];
    const chatEdge = {
      async listMessagesPage(_conv: string, limit: number, before?: string) {
        calls.push({ limit, before });
        if (opts.failAt !== undefined && calls.length === opts.failAt) return null;
        const end = before ? all.findIndex((m) => m.id === before) : all.length;
        const start = Math.max(0, end - Math.min(limit, 100));
        return { messages: all.slice(start, end), hasMore: start > 0 };
      },
    };
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix105-"));
    const gw = { listModels: async () => null, async *streamCompletion() { yield { kind: "done", finish_reason: "stop" }; } };
    const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }) };
    let co: TurnCoordinator;
    const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
    co = new TurnCoordinator(
      { postTurnEvent: () => {}, post: () => {}, postHostResult: () => {}, postHostError: () => {} } as never,
      gw as never, chatEdge as never, {} as never, sidecar as never, hostTools,
      new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
      { homeDirectory: tmp, dataDir: tmp, defaultModel: TYPHOON.model, approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null },
      log,
    );
    const load = () => (co as unknown as { loadServerTranscript: (c: string) => Promise<{ rows: { id: string }[]; hasMore: boolean; pages: number }> }).loadServerTranscript("c");
    return { load, calls };
  }

  it("250 rows: three pages, oldest first, nothing missing", async () => {
    const { load, calls } = coordinator(250);
    const t = await load();
    expect(t.rows).toHaveLength(250);
    expect(t.rows[0]!.id).toBe("m00000");
    expect(t.rows[249]!.id).toBe("m00249");
    expect(t.hasMore).toBe(false);
    expect(t.pages).toBe(3);
    expect(calls.map((c) => c.before)).toEqual([undefined, "m00150", "m00050"]);
  });

  it("past the ceiling it stops at 1,000 rows and says older rows remain", async () => {
    const { load } = coordinator(1_250);
    const t = await load();
    expect(t.rows).toHaveLength(1_000);
    expect(t.rows[999]!.id).toBe("m01249");
    expect(t.hasMore).toBe(true);
  });

  it("a page that cannot be read fails the load instead of passing off part of a transcript as the whole", async () => {
    const { load } = coordinator(250, { failAt: 2 });
    await expect(load()).rejects.toThrow(/page 2/);
  });
});

// FIX105 Part A — no conversation may become unrecoverable.
//
// FIX104 measured Typhoon answering an 824-character question with 18,600
// characters. The desktop pinned that first exchange for good, it alone was
// three times the history budget, and every later turn was refused. The head is
// now pinned only while that cannot kill the conversation, and a provider
// refusal of a message that is too long on its own is named for the message.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import type { BudgetBasis } from "../src/main/context-budget";
import { compileChain, estimateTokens } from "../src/main/context-compiler";
import { ContextManager, type TurnBudget } from "../src/main/context-manager";
import { MemoryConversationStateStore } from "../src/main/conversation-state";
import type { GatewayMessage } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const TYPHOON: BudgetBasis = { model: "Typhoon-S-ThaiLLM-8B-Instruct", window: 16_384, outputTokens: 8192 };
const RATIOS = { triggerRatio: 0.85, targetRatio: 0.6 };
const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
const thai = (n: number) => "ก".repeat(n);
const sys = (): GatewayMessage => ({ role: "system", content: "s".repeat(4000) });
const u = (t: string): GatewayMessage => ({ role: "user", content: t });
const a = (t: string): GatewayMessage => ({ role: "assistant", content: t });

describe("FIX105 Part A — the first exchange is pinned only while that cannot kill the conversation", () => {
  it("FIX104's case: an 18,600-character first reply is left out, the newest message fits, and the model is told truthfully", () => {
    const msgs = [sys(), u(thai(824)), a(thai(18_600)), u(thai(852))];
    const cc = compileChain(msgs, TYPHOON, 0, RATIOS);
    expect(cc.compacted).toBe(true);
    expect(cc.headDropped).toBe(true);
    expect(cc.newestOverBudget).toBe(false);
    expect(cc.messages.map((m) => m.role)).toEqual(["system", "user"]);
    expect(cc.messages[1]).toBe(msgs[3]);
    expect(cc.historyTokens).toBeLessThanOrEqual(cc.budget.messagesRemaining);
    // What the provider counts: prompt + max_tokens within the window (estimate).
    expect(cc.budget.system + cc.sentTokens + 160 + TYPHOON.outputTokens).toBeLessThanOrEqual(TYPHOON.window);
    const note = cc.messages[0]!.content as string;
    expect(note).toContain("The most recent 1 message is included verbatim");
    expect(note).not.toContain("The first exchange");
    // The summariser is asked to cover the whole first exchange.
    expect([cc.dropStart, cc.dropEnd]).toEqual([0, 2]);
  });

  it("a first exchange the size of a standing instruction is still pinned", () => {
    const msgs: GatewayMessage[] = [sys(), u("STANDING: end every answer with ผ่าน"), a("รับทราบ ผ่าน")];
    for (let i = 0; i < 40; i++) msgs.push(i % 2 === 0 ? u(`q${i} ${thai(300)}`) : a(`a${i} ${thai(300)}`));
    const cc = compileChain(msgs, TYPHOON, 0, RATIOS);
    expect(cc.compacted).toBe(true);
    expect(cc.headDropped).toBe(false);
    expect(cc.messages[1]).toBe(msgs[1]);
    expect(cc.messages[2]).toBe(msgs[2]);
    expect(cc.messages[0]!.content as string).toContain("The first exchange and the most recent");
  });

  it("a small head gives way when keeping it leaves no room for the newest message", () => {
    // History over the trigger, a head well under its cap (608 of 1,326 tokens),
    // and a newest message that fits the budget alone but not beside the head.
    const msgs = [sys(), u("a".repeat(1200)), a("b".repeat(1200)), u("q"), a("r"), u("c".repeat(16_800))];
    const cc = compileChain(msgs, TYPHOON, 0, RATIOS);
    expect(cc.headDropped).toBe(true);
    expect(cc.messages[cc.messages.length - 1]).toBe(msgs[5]);
    expect(cc.historyTokens).toBeLessThanOrEqual(cc.budget.messagesRemaining);
  });

  it("the invariant, swept: whenever the newest message fits on its own, what is sent fits", () => {
    for (const headChars of [40, 1000, 6000, 40_000]) {
      for (const newestChars of [40, 2000, 6000]) {
        for (const basis of [TYPHOON, { ...TYPHOON, window: 32_768 }, { ...TYPHOON, window: 131_072 }]) {
          const msgs = [sys(), u("h".repeat(headChars)), a(thai(headChars)), u("q1"), a("a1"), u("q2"), a("a2"), u("n".repeat(newestChars))];
          const cc = compileChain(msgs, basis, 0, RATIOS);
          const newest = estimateTokens(msgs[msgs.length - 1]!.content as string) + 4;
          const tag = `head=${headChars} newest=${newestChars} window=${basis.window}`;
          if (newest <= cc.budget.messagesRemaining) expect(cc.historyTokens, tag).toBeLessThanOrEqual(cc.budget.messagesRemaining);
          expect(cc.messages[cc.messages.length - 1], tag).toBe(msgs[msgs.length - 1]);
        }
      }
    }
  });

  it("a message larger than the whole budget is named as such (no trim can make it fit)", () => {
    const cc = compileChain([sys(), u("hi"), a("hello"), u("x".repeat(80_000))], TYPHOON, 0, RATIOS);
    expect(cc.newestOverBudget).toBe(true);
  });

  it("the manager reports head_dropped and newest_over_budget to the page", () => {
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: null, log, emit: () => {} });
    const turn: TurnBudget = { model: TYPHOON.model, basis: TYPHOON, numTools: 0 };
    // Right after turn 1 the history IS the first exchange: nothing newer to
    // make room for, so nothing is dropped yet and the meter reads "will
    // summarize older messages next" (over budget, not trimming).
    const afterTurn1 = mgr.info("c", [sys(), u(thai(824)), a(thai(18_600))], turn);
    expect(afterTurn1).toMatchObject({ trimming: false, over_budget: true, head_dropped: false });
    // With the next question in it, the head goes and the meter says so.
    const ev = mgr.info("c", [sys(), u(thai(824)), a(thai(18_600)), u(thai(852))], turn);
    expect(ev).toMatchObject({ trimming: true, head_dropped: true, newest_over_budget: false });
  });
});

describe("FIX105 — a refusal of one oversized message is named for the message", () => {
  async function runWith(newestOverBudget: boolean) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix105-"));
    const events: { event: string; data: Record<string, unknown> }[] = [];
    const gw = {
      listModels: async () => null,
      async *streamCompletion() {
        yield { kind: "error", code: "context_length_exceeded", message: "The conversation is too long for this model. Shorten it or start a new one." };
      },
    };
    const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }) };
    let co: TurnCoordinator;
    const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
    co = new TurnCoordinator(
      { postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => events.push({ event, data: data as Record<string, unknown> }), post: () => {}, postHostResult: () => {}, postHostError: () => {} } as never,
      gw as never, {} as never, {} as never, sidecar as never, hostTools,
      new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
      { homeDirectory: tmp, dataDir: tmp, defaultModel: TYPHOON.model, approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
      log,
    );
    await (co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({
      conversationId: "c", model: TYPHOON.model, messages: [{ role: "system", content: "s" }, { role: "user", content: "x" }], signal: new AbortController().signal,
      maxIterations: 2, maxMs: 5_000, tools: [], parentToolCallId: null, onText: () => {}, newestOverBudget,
    });
    return events.filter((e) => e.event === "error").map((e) => e.data);
  }

  it("newest message alone over the budget → message_too_long, with a sentence about this message", async () => {
    const [err] = await runWith(true);
    expect(err!.code).toBe("message_too_long");
    expect(String(err!.message)).toContain("This message alone is too long");
  });

  it("otherwise the conversation-length refusal keeps its code", async () => {
    const [err] = await runWith(false);
    expect(err!.code).toBe("context_length_exceeded");
  });
});

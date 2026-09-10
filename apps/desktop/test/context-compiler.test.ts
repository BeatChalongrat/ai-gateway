// FIX32 §1.2 — the desktop context compiler.
//
// Pins the four properties the brief ordered, in that order:
//   1. the system prompt and the FIRST exchange always survive;
//   2. the middle goes, the most recent turns stay verbatim;
//   3. it is never silent — the model gets a context note, the caller gets
//      compacted/dropped to tell the user;
//   4. tool chains stay coherent — no orphan tool result, no call whose
//      result was dropped.
// Plus: the estimator matches chat-edge's heuristic; a fitting chain is
// returned UNTOUCHED (same array).
// FIX104: the budget is the catalogue's window (a BudgetBasis), not a local map.
import { describe, expect, it } from "vitest";

import type { GatewayMessage } from "../src/main/gateway-client";
import type { BudgetBasis } from "../src/main/context-budget";
import { compileChain, estimateTokens } from "../src/main/context-compiler";

const TERRA: BudgetBasis = { model: "gpt-5.6-terra", window: 128_000, outputTokens: 8192 };
const TYPHOON: BudgetBasis = { model: "Typhoon-S-ThaiLLM-8B-Instruct", window: 16_384, outputTokens: 8192 };
const QWEN: BudgetBasis = { model: "qwen3.8-27b-fp8", window: 131_072, outputTokens: 8192 };

const sys = (len = 1000): GatewayMessage => ({ role: "system", content: "s".repeat(len) });
const u = (text: string): GatewayMessage => ({ role: "user", content: text });
const a = (text: string): GatewayMessage => ({ role: "assistant", content: text });

function chain(turns: number, longEvery = 0): GatewayMessage[] {
  const out: GatewayMessage[] = [sys()];
  out.push(u("ทุกคำตอบต้องลงท้ายด้วยคำว่า ผ่าน"), a("เข้าใจครับ ผ่าน"));
  for (let t = 2; t <= turns; t++) {
    const long = longEvery > 0 && t % longEvery === 0;
    out.push(u(long ? "รายละเอียดยาว ".repeat(2000) : `คำถามที่ ${t} สั้นๆ`));
    out.push(a(`คำตอบที่ ${t} ผ่าน`));
  }
  return out;
}

describe("estimateTokens — chat-edge's heuristic", () => {
  it("ASCII ≈ len/4, Thai ≈ 2·len/3, floor 1", () => {
    expect(estimateTokens("abcdefgh")).toBe(2);
    expect(estimateTokens("กขคง")).toBe(3); // ceil(8/3)
    expect(estimateTokens("a")).toBe(1);
    expect(estimateTokens("")).toBe(0);
  });
});

describe("compileChain", () => {
  it("a fitting chain is returned untouched — the very same array", () => {
    const msgs = chain(10);
    const cc = compileChain(msgs, TERRA, 20);
    expect(cc.compacted).toBe(false);
    expect(cc.messages).toBe(msgs);
    expect(cc.dropped).toBe(0);
    expect(cc.sentTokens).toBe(cc.historyTokens);
  });

  it("1+2: over budget → the middle goes; system, the FIRST exchange and the newest tail survive", () => {
    const msgs = chain(300, 5); // long paste every 5th turn → far over the budget
    const cc = compileChain(msgs, TERRA, 20);
    expect(cc.compacted).toBe(true);
    expect(cc.dropped).toBeGreaterThan(0);
    const kept = cc.messages;
    expect(kept[0]!.role).toBe("system");
    // property 1 — the turn-1 instruction is still there:
    expect(String(kept[1]!.content)).toContain("ลงท้ายด้วยคำว่า ผ่าน");
    expect(String(kept[2]!.content)).toContain("เข้าใจครับ");
    // property 2 — the newest turn (the incoming user message) is the last kept:
    expect(String(kept[kept.length - 1]!.content)).toContain("คำตอบที่ 300");
    // and the budget holds (conservatively):
    expect(cc.historyTokens).toBeLessThanOrEqual(cc.budget.messagesRemaining);
    // what was dropped is the MIDDLE: message count adds up
    expect(cc.sentMessages + cc.dropped).toBe(cc.totalMessages);
  });

  it("3: never silent — the model gets a context note; the caller gets the numbers", () => {
    const cc = compileChain(chain(300, 5), TERRA, 20);
    expect(String(cc.messages[0]!.content)).toContain("<context_note>");
    expect(String(cc.messages[0]!.content)).toContain(`${cc.dropped} earlier message`);
    expect(String(cc.messages[0]!.content)).toContain("do not guess");
    expect(cc.dropped).toBeGreaterThan(100);
  });

  it("4: tool chains are atomic — no orphan tool result, no unanswered tool call", () => {
    const msgs: GatewayMessage[] = [sys()];
    msgs.push(u("คำสั่งแรก"), a("รับทราบ ผ่าน"));
    for (let t = 0; t < 120; t++) {
      msgs.push(u(`งานที่ ${t}: ` + "ยาวมาก ".repeat(700)));
      msgs.push({ role: "assistant", content: "", tool_calls: [{ id: `c${t}`, type: "function", function: { name: "run_command", arguments: "{}" } }] });
      msgs.push({ role: "tool", content: "ผลลัพธ์ " + "x".repeat(500), tool_call_id: `c${t}`, name: "run_command" } as GatewayMessage);
      msgs.push(a(`สรุปงานที่ ${t} ผ่าน`));
    }
    const cc = compileChain(msgs, TERRA, 20);
    expect(cc.compacted).toBe(true);
    const kept = cc.messages;
    for (let i = 0; i < kept.length; i++) {
      const m = kept[i]!;
      if (m.role === "tool") {
        // its call must be the message(s) immediately before it
        let j = i - 1;
        while (j >= 0 && kept[j]!.role === "tool") j--;
        expect(kept[j]!.role).toBe("assistant");
        expect(kept[j]!.tool_calls?.length ?? 0).toBeGreaterThan(0);
      }
      if (m.role === "assistant" && m.tool_calls?.length) {
        expect(kept[i + 1]?.role).toBe("tool"); // the result travelled with it
      }
    }
  });

  it("the newest user turn ships even when it alone overflows the budget", () => {
    const msgs: GatewayMessage[] = [sys(), u("แรก"), a("ครับ ผ่าน"), u("มหึมา ".repeat(200_000))];
    const cc = compileChain(msgs, TERRA, 0);
    expect(String(cc.messages[cc.messages.length - 1]!.content)).toContain("มหึมา");
  });
});

describe("FIX104 — the budget is the catalogue's window, with chat-edge's rules", () => {
  it("a model the catalogue does not know budgets against the conservative default (14,500), not 100,000", () => {
    const cc = compileChain(chain(300, 5), { model: "some-unknown-model", window: 0, outputTokens: 8192 }, 0);
    expect(cc.budget.total).toBe(14_500);
    expect(cc.budget.window).toBe(0);
    expect(cc.compacted).toBe(true);
  });

  it("Typhoon (16,384): working limit 14,500, and the reserve is the 8,192 the request asks for, not chat-edge's 3,625", () => {
    const cc = compileChain(chain(3), TYPHOON, 0);
    expect(cc.budget.total).toBe(14_500);
    expect(cc.budget.outputReserve).toBe(8192);
    expect(cc.budget.messagesRemaining).toBe(14_500 - cc.budget.system - 8192);
    expect(cc.budget.model).toBe("Typhoon-S-ThaiLLM-8B-Instruct");
  });

  it("qwen3.8-27b-fp8 (131,072) and gpt-5.6-terra (128,000): chat-edge's working limits and its 16,000 cap", () => {
    expect(compileChain(chain(3), QWEN, 0).budget).toMatchObject({ total: 117_500, outputReserve: 16_000 });
    expect(compileChain(chain(3), TERRA, 0).budget).toMatchObject({ total: 115_000, outputReserve: 16_000 });
  });

  it("an untrimmed request never asks for more than the window: estimated prompt + max_tokens ≤ advertised", () => {
    // Grow a Typhoon conversation one exchange at a time; every chain the
    // compiler sends as-is must fit the provider's own arithmetic, the one
    // that refused the desktop at "8192 > 16384 - 12961".
    const msgs: GatewayMessage[] = [sys(4000), u("แรก"), a("ครับ")];
    for (let t = 0; t < 60; t++) {
      msgs.push(u(`คำถาม ${t} ${"ข้อความภาษาไทย".repeat(30)}`), a(`คำตอบ ${t} ${"reply ".repeat(60)}`));
      const cc = compileChain(msgs, TYPHOON, 0, { triggerRatio: 0.85, targetRatio: 0.6 });
      expect(cc.budget.system + cc.sentTokens + (cc.compacted ? 160 : 0) + TYPHOON.outputTokens).toBeLessThanOrEqual(TYPHOON.window);
    }
  });
});

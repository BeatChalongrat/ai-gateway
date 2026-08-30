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
import { describe, expect, it } from "vitest";

import type { GatewayMessage } from "../src/main/gateway-client";
import { compileChain, estimateTokens, MODEL_CONTEXT_LIMITS } from "../src/main/context-compiler";

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
    const cc = compileChain(msgs, "gpt-5.6-terra", 20);
    expect(cc.compacted).toBe(false);
    expect(cc.messages).toBe(msgs);
    expect(cc.dropped).toBe(0);
  });

  it("1+2: over budget → the middle goes; system, the FIRST exchange and the newest tail survive", () => {
    const msgs = chain(300, 5); // long paste every 5th turn → far over 120K
    const cc = compileChain(msgs, "gpt-5.6-terra", 20);
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
    const cc = compileChain(chain(300, 5), "gpt-5.6-terra", 20);
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
    const cc = compileChain(msgs, "gpt-5.6-terra", 20);
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
    const cc = compileChain(msgs, "gpt-5.6-terra", 0);
    expect(String(cc.messages[cc.messages.length - 1]!.content)).toContain("มหึมา");
  });

  it("unknown models budget against the cautious default", () => {
    expect(MODEL_CONTEXT_LIMITS["gpt-5.6-terra"]).toBe(120_000);
    const cc = compileChain(chain(300, 5), "some-unknown-model", 0);
    expect(cc.budget.total).toBe(100_000);
    expect(cc.compacted).toBe(true);
  });
});

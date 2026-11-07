// FIX108 Part A — the context note's reserve is measured from the note.
import { describe, expect, it } from "vitest";

import { CONTEXT_NOTE_MAX_COUNT, CONTEXT_NOTE_RESERVE, compileChain, estimateTokens, renderContextNote } from "../src/main/context-compiler";

describe("FIX108 — the note never outgrows what is reserved for it", () => {
  it("every rendered note fits the reserve, alone and as the tail of a system message", () => {
    const prefixes = ["", "S", "SS", "SSS", "You are SRAN Assistant. ".repeat(40)];
    let biggest = 0;
    for (const headKept of [false, true]) {
      for (const status of ["complete", "partial", "failed", "off", "pending"] as const) {
        for (let count = 1; count <= CONTEXT_NOTE_MAX_COUNT; count = count * 10 + 9) {
          const note = renderContextNote(count, count, headKept, status);
          biggest = Math.max(biggest, estimateTokens(note));
          for (const prefix of prefixes) {
            expect(estimateTokens(prefix + note) - estimateTokens(prefix), `head=${headKept} ${status} count=${count}`).toBeLessThanOrEqual(CONTEXT_NOTE_RESERVE);
          }
        }
      }
    }
    expect(CONTEXT_NOTE_RESERVE).toBe(biggest + 1);
  });

  it("a compacted chain's system message grows by no more than the reserve", () => {
    const sys = { role: "system" as const, content: "S".repeat(4001) };
    const msgs = [sys, ...Array.from({ length: 60 }, (_, i) => ({ role: (i % 2 ? "assistant" : "user") as "user" | "assistant", content: "x".repeat(4000) })), { role: "user" as const, content: "q" }];
    const cc = compileChain(msgs, { model: "m", window: 16_384, outputTokens: 8192 } as never, 0, { summaryStatus: "partial" });
    expect(cc.compacted).toBe(true);
    expect(estimateTokens(String(cc.messages[0]!.content)) - estimateTokens(sys.content)).toBeLessThanOrEqual(CONTEXT_NOTE_RESERVE);
  });
});

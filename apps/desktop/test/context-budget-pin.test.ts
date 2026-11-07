// FIX104 — the desktop's budget rules are chat-edge's, and this test is why
// that stays true.
//
// The desktop cannot call Go, so the rules are ported (context-budget.ts).
// A port is only safe if it cannot drift: every constant below is read out
// of chat-edge's own source and compared. Change a number on either side
// alone and this fails; change both together and it passes. A renamed Go
// constant fails loudly too ("not found") — fix the pin, never the number.
//
// It also refuses the thing FIX104 removed: a desktop table of per-model
// context windows. The window is the catalogue's.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import * as B from "../src/main/context-budget";
import { COMPACT_TARGET_RATIO, COMPACT_TRIGGER_RATIO } from "../src/main/conversation-state";

const SERVICE = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service");
const read = (f: string) => fs.readFileSync(path.join(SERVICE, f), "utf8").replace(/\r\n/g, "\n");

function goConst(src: string, name: string): number {
  const m = src.match(new RegExp(`^\\s*(?:const\\s+)?${name}\\s*=\\s*([0-9.]+)\\s*$`, "m"));
  if (!m) throw new Error(`${name} not found in chat-edge's source — renamed or moved? Update this pin; do not change the numbers to match.`);
  return Number(m[1]);
}

function goFunc(src: string, name: string): string {
  const start = src.indexOf(`func ${name}(`);
  if (start < 0) throw new Error(`func ${name} not found in chat-edge's source`);
  return src.slice(start, src.indexOf("\n}\n", start));
}

describe("FIX104 — the desktop budget rules are chat-edge's (context_budget.go, context_compiler.go)", () => {
  it("headroom: fraction, step and the smallest observed window", () => {
    const go = read("context_budget.go");
    expect(B.HEADROOM_FRACTION).toBe(goConst(go, "headroomFraction"));
    expect(B.HEADROOM_STEP).toBe(goConst(go, "headroomStep"));
    expect(B.SMALLEST_OBSERVED_WINDOW).toBe(goConst(go, "smallestObservedWindow"));
  });

  it("workingLimitFrom has the shape that was ported, and the default is built from it on both sides", () => {
    const go = read("context_budget.go");
    const fn = goFunc(go, "workingLimitFrom");
    expect(fn).toContain("advertised = smallestObservedWindow");
    expect(fn).toContain("w := int(float64(advertised) * headroomFraction)");
    expect(fn).toMatch(/if w >= headroomStep \{\s*w -= w % headroomStep\s*\}/);
    expect(go).toContain("var defaultContextLimit = workingLimitFrom(smallestObservedWindow)");
    expect(B.DEFAULT_CONTEXT_LIMIT).toBe(B.workingLimitFrom(B.SMALLEST_OBSERVED_WINDOW));
  });

  it("workingLimitFrom reproduces every measured seed chat-edge recorded next to its window", () => {
    // "Typhoon-S-ThaiLLM-8B-Instruct":         14500,  // 16,384
    const seeds = [...read("context_budget.go").matchAll(/^\s*"([^"]+)":\s*(\d+),\s*\/\/\s*([\d,]+)\s*$/gm)];
    expect(seeds.length).toBeGreaterThanOrEqual(7);
    for (const [, id, working, advertised] of seeds) {
      expect({ id, working: B.workingLimitFrom(Number(advertised!.replace(/,/g, ""))) }).toEqual({ id, working: Number(working) });
    }
  });

  it("output reserve: divisor, cap and floor", () => {
    const fn = goFunc(read("context_budget.go"), "outputReserveFor");
    const div = fn.match(/reserve := totalBudget \/ (\d+)/);
    const cap = fn.match(/if reserve > (\d+) \{\s*reserve = (\d+)/);
    const floor = fn.match(/if reserve < (\d+) \{\s*reserve = (\d+)/);
    expect(div && cap && floor).toBeTruthy();
    expect(B.OUTPUT_RESERVE_DIVISOR).toBe(Number(div![1]));
    expect([B.OUTPUT_RESERVE_MAX, B.OUTPUT_RESERVE_MAX]).toEqual([Number(cap![1]), Number(cap![2])]);
    expect([B.OUTPUT_RESERVE_MIN, B.OUTPUT_RESERVE_MIN]).toEqual([Number(floor![1]), Number(floor![2])]);
  });

  it("messages floor, per-tool reserve, trigger and target", () => {
    const floor = goFunc(read("context_budget.go"), "CalculateBudgetWith").match(/if remaining < (\d+) \{\s*remaining = (\d+)/);
    expect(floor).toBeTruthy();
    expect([B.MESSAGES_FLOOR, B.MESSAGES_FLOOR]).toEqual([Number(floor![1]), Number(floor![2])]);
    const compiler = read("context_compiler.go");
    expect(B.PER_TOOL_TOKENS).toBe(goConst(compiler, "perToolTokens"));
    expect(COMPACT_TRIGGER_RATIO).toBe(goConst(compiler, "DefaultCompactTriggerRatio"));
    expect(COMPACT_TARGET_RATIO).toBe(goConst(compiler, "DefaultCompactTargetRatio"));
  });

  it("FIX105 — the head is pinned by the same share and the same two conditions", () => {
    const compiler = read("context_compiler.go");
    expect(B.HEAD_PIN_MAX_SHARE).toBe(goConst(compiler, "headPinMaxShare"));
    expect(goFunc(compiler, "planHeadPreservingTrim")).toContain("headTokens > headCap || headTokens+cost(n-1) > budget");
    const ts = fs.readFileSync(path.join(__dirname, "..", "src", "main", "context-compiler.ts"), "utf8");
    expect(ts).toContain("naturalHeadTokens > Math.trunc(remaining * HEAD_PIN_MAX_SHARE) || naturalHeadTokens + newestUnitTokens + noteReserve + preambleTokens > limit");
  });

  it("FIX105 — a message too long on its own gets the same code and sentence on both surfaces", () => {
    const go = read("message_too_long.go");
    const goText = go.match(/const messageTooLongText = "([^"]+)"/)?.[1];
    const goCode = go.match(/const CodeMessageTooLong = "([^"]+)"/)?.[1];
    const ts = fs.readFileSync(path.join(__dirname, "..", "src", "main", "turn-coordinator.ts"), "utf8");
    expect(goText).toBeTruthy();
    expect(ts.match(/const MESSAGE_TOO_LONG_TEXT = "([^"]+)"/)?.[1]).toBe(goText);
    expect(ts.match(/const MESSAGE_TOO_LONG_CODE = "([^"]+)"/)?.[1]).toBe(goCode);
  });

  it("the desktop's only addition: the reserve never falls below the max_tokens a request sends", () => {
    expect(B.outputReserveForRequest(14_500, 8192)).toBe(8192); // chat-edge alone: 3,625
    expect(B.outputReserveForRequest(117_500, 8192)).toBe(16_000);
    expect(B.outputReserveForRequest(14_500, 0)).toBe(B.outputReserveFor(14_500));
  });

  it("no hand-maintained table of model context windows exists anywhere in the desktop host", () => {
    const dir = path.join(__dirname, "..", "src", "main");
    const table = /["'](?:gpt-|claude-|gemini-|o\d|qwen|Typhoon|OpenThaiGPT|Pathumma|THaLLE|deepseek)[^"']*["']\s*:\s*\d{1,3}[_,]?\d{3}\b|\bauto\s*:\s*\d{1,3}[_,]?\d{3}\b|MODEL_CONTEXT_LIMITS/;
    const hits = fs
      .readdirSync(dir)
      .filter((f) => f.endsWith(".ts"))
      .flatMap((f) => fs.readFileSync(path.join(dir, f), "utf8").split("\n").map((line, i) => ({ f, i: i + 1, line })))
      .filter(({ line }) => table.test(line));
    expect(hits).toEqual([]);
  });
});

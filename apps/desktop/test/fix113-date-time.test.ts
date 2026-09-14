// FIX113 Part A — the desktop tells the model what day it is, with the web's
// fixed wording from one asset and the machine's own clock and zone.
//
// Before: no date block at all. Asked "วันนี้วันที่เท่าไหร่" the desktop spent a
// run_python call and reported the sandbox's UTC clock (once with the wrong
// weekday); with no tools, qwen3.8-27b-fp8 returned an empty completion to
// both the date and the year question.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { DATE_TIME_LEAD, DATE_TIME_RULE, PromptAssembler, dateTimeBlock, utcOffsetLabel } from "../src/main/prompt-assembler";

const SERVICE = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service");
const read = (name: string) => fs.readFileSync(path.join(SERVICE, name), "utf8").replace(/\r\n/g, "\n");
const quiet = { info: () => {}, warn: () => {}, error: () => {}, attachFile: () => {} } as unknown as import("../src/main/logger").Logger;

describe("FIX113 — the date/time wording is one asset", () => {
  it("the lead and the rule are chat-edge's prompt_date_time.txt, line 1 and line 2, byte for byte", () => {
    const lines = read("prompt_date_time.txt").trim().split("\n");
    expect(lines).toHaveLength(2);
    expect(DATE_TIME_LEAD).toBe(lines[0]);
    expect(DATE_TIME_RULE).toBe(lines[1]);
  });
  it("chat-edge embeds the asset and no longer carries the sentence in code", () => {
    const go = read("prompt_compiler.go");
    expect(go).toContain("//go:embed prompt_date_time.txt");
    expect(go).not.toContain("Treat this as the present moment");
    expect(go).toContain("Asia/Bangkok (UTC+7)"); // the web's zone stays the web's own
  });
});

describe("FIX113 — the block on this side", () => {
  it("has the web's shape with the machine's zone: lead, date, weekday, time, zone (UTC±h), rule", () => {
    const now = new Date(2026, 8, 14, 20, 15); // local time on this machine
    const block = dateTimeBlock(now);
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(block).toBe(`${DATE_TIME_LEAD} 2026-09-14 (Monday) 20:15, ${zone} (${utcOffsetLabel(now)}). ${DATE_TIME_RULE}`);
    expect(block).toMatch(/^Current date and time: \d{4}-\d{2}-\d{2} \([A-Z][a-z]+day\) \d{2}:\d{2}, .+ \(UTC[+-]\d{1,2}(:\d{2})?\)\. Treat this as the present moment/);
  });

  it("the offset label reads like the web's (UTC+7), and handles negatives and half hours", () => {
    const at = (offsetMinutes: number) => ({ getTimezoneOffset: () => -offsetMinutes }) as unknown as Date;
    expect(utcOffsetLabel(at(420))).toBe("UTC+7");
    expect(utcOffsetLabel(at(0))).toBe("UTC+0");
    expect(utcOffsetLabel(at(-240))).toBe("UTC-4");
    expect(utcOffsetLabel(at(330))).toBe("UTC+5:30");
  });

  it("the assembled system prompt carries it, right after the identity, before the artifact dialect", async () => {
    const chatEdge = { listProjects: async () => [], listGlobalMemories: async () => [], listMemories: async () => [] };
    const asm = new PromptAssembler(chatEdge as never, quiet, {});
    const now = new Date(2026, 8, 14, 9, 5);
    const { text } = await asm.buildSystemPromptParts({ homeDirectory: "C:\\Users\\x", grantedFolders: [], conversationId: "c", activeProject: null, memoryEnabled: true, toolsAvailable: true, now });
    const line = /Current date and time:[^\n]*/.exec(text)?.[0];
    expect(line).toBeDefined();
    expect(line).toContain("2026-09-14 (Monday) 09:05");
    expect(text.indexOf("Current date and time:")).toBeLessThan(text.indexOf("<artifact_dialect>") >= 0 ? text.indexOf("<artifact_dialect>") : text.length);
    expect(text.indexOf("Current date and time:")).toBeGreaterThan(text.indexOf("You are SRAN Assistant"));
  });
});

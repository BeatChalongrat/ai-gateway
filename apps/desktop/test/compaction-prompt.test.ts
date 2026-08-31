// FIX33 A-D3 — the compaction extraction prompt exists ONCE.
//
// chat-edge embeds prompt_compaction_extraction.txt (Go: //go:embed) as the
// system message of every state-extraction call; the desktop holds the same
// bytes as COMPACTION_EXTRACTION_PROMPT. This test byte-compares the two and
// FAILS on drift — the guard FIX32 put on the language rules, applied to the
// summariser so the fifth divergence does not happen.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { COMPACTION_EXTRACTION_PROMPT } from "../src/main/compaction-prompt";
import { STATE_HONESTY_NOTE, renderStateBlock, emptyState } from "../src/main/conversation-state";

const CANONICAL = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service", "prompt_compaction_extraction.txt");
const GO_SERVICE = path.join(path.dirname(CANONICAL), "conversation_state_service.go");

describe("FIX33 A-D3 — one source for the extraction prompt", () => {
  it("the desktop constant is byte-identical to chat-edge's embedded asset", () => {
    const canon = fs.readFileSync(CANONICAL, "utf8").replace(/\r\n/g, "\n").trim();
    expect(COMPACTION_EXTRACTION_PROMPT.replace(/\r\n/g, "\n").trim()).toBe(canon);
  });

  it("chat-edge actually embeds that asset (no second wording left in Go)", () => {
    const go = fs.readFileSync(GO_SERVICE, "utf8");
    expect(go).toContain("//go:embed prompt_compaction_extraction.txt");
    // The old inline prompt is gone — the JSON shape lives ONLY in the .txt.
    expect(go).not.toContain('"current_goal": "One sentence describing');
  });

  it("the prompt forbids tool calls in words (A-W3 / A-D2)", () => {
    expect(COMPACTION_EXTRACTION_PROMPT).toContain("Do NOT call any tool");
    expect(COMPACTION_EXTRACTION_PROMPT).toContain("copy them exactly as they appear");
  });

  it("the honesty note is the same sentence chat-edge appends (A-W5)", () => {
    const go = fs.readFileSync(GO_SERVICE, "utf8");
    expect(go).toContain(`const StateHonestyNote = ${JSON.stringify(STATE_HONESTY_NOTE)}`);
    const s = emptyState("c");
    s.current_goal = "g";
    expect(renderStateBlock(s)).toContain(STATE_HONESTY_NOTE);
  });
});

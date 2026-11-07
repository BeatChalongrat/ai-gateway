// FIX33 A-D3 — the compaction extraction prompt exists ONCE.
//
// chat-edge embeds prompt_compaction_extraction.txt (Go: //go:embed) as the
// system message of every state-extraction call. FIX111: the desktop no
// longer holds a copy — it does not extract; the server summarises for both
// surfaces (server-summariser.ts). What the desktop still shares is the
// state INJECTION wording and the honesty note, pinned below.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { STATE_HONESTY_NOTE, renderStateBlock, renderStateMessages, emptyState } from "../src/main/conversation-state";
import { STATE_INJECTION_ASSISTANT_ACK, STATE_INJECTION_USER_PREAMBLE } from "../src/main/state-injection-prompt";

const INJECTION = path.join(path.dirname(CANONICAL_DIR()), "prompt_state_injection.txt");
function CANONICAL_DIR() {
  return path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service", "x");
}

describe("FIX33.1 Part 2 — one source for the state injection wording", () => {
  it("the desktop constants are byte-identical to chat-edge's prompt_state_injection.txt", () => {
    const raw = fs.readFileSync(INJECTION, "utf8").replace(/\r\n/g, "\n");
    const [user, ack] = raw.split("\n---ASSISTANT---\n");
    expect(STATE_INJECTION_USER_PREAMBLE).toBe(user!.trim());
    expect(STATE_INJECTION_ASSISTANT_ACK).toBe(ack!.trim());
  });
  it("chat-edge embeds that asset and no longer renders the state into the system prompt", () => {
    const go = fs.readFileSync(path.join(path.dirname(INJECTION), "conversation_state_service.go"), "utf8");
    expect(go).toContain("//go:embed prompt_state_injection.txt");
    const pc = fs.readFileSync(path.join(path.dirname(INJECTION), "prompt_compiler.go"), "utf8");
    expect(pc).not.toContain("add(stateBlock)");
  });
  it("the desktop pair is user + assistant, wording from the asset", () => {
    const s = emptyState("c");
    s.current_goal = "g";
    const pair = renderStateMessages(s);
    expect(pair.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(pair[0]!.content as string).toMatch(new RegExp("^" + STATE_INJECTION_USER_PREAMBLE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    expect(pair[1]!.content).toBe(STATE_INJECTION_ASSISTANT_ACK);
  });
});

const CANONICAL = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service", "prompt_compaction_extraction.txt");
const GO_SERVICE = path.join(path.dirname(CANONICAL), "conversation_state_service.go");

describe("FIX33 A-D3 / FIX111 — the extraction prompt lives in chat-edge alone", () => {
  it("the desktop holds no copy of the extraction prompt", () => {
    expect(fs.existsSync(path.join(__dirname, "..", "src", "main", "compaction-prompt.ts"))).toBe(false);
    expect(fs.existsSync(CANONICAL)).toBe(true);
  });

  it("chat-edge actually embeds that asset (no second wording left in Go)", () => {
    const go = fs.readFileSync(GO_SERVICE, "utf8");
    expect(go).toContain("//go:embed prompt_compaction_extraction.txt");
    // The old inline prompt is gone — the JSON shape lives ONLY in the .txt.
    expect(go).not.toContain('"current_goal": "One sentence describing');
  });

  it("the prompt forbids tool calls in words (A-W3 / A-D2)", () => {
    const canon = fs.readFileSync(CANONICAL, "utf8");
    expect(canon).toContain("Do NOT call any tool");
    expect(canon).toContain("copy them exactly as they appear");
  });

  it("the honesty note is the same sentence chat-edge appends (A-W5)", () => {
    const go = fs.readFileSync(GO_SERVICE, "utf8");
    expect(go).toContain(`const StateHonestyNote = ${JSON.stringify(STATE_HONESTY_NOTE)}`);
    const s = emptyState("c");
    s.current_goal = "g";
    expect(renderStateBlock(s)).toContain(STATE_HONESTY_NOTE);
  });
});

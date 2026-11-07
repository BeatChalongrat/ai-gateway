// FIX107 Part B1 — the context note exists ONCE.
//
// chat-edge embeds prompt_context_note.txt; the desktop holds the same bytes as
// CONTEXT_NOTE_TEMPLATE. This byte-compares the two and FAILS on drift; the
// rendered notes are compared case by case in state-contract.test.ts.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { compileChain, parseContextNote, renderContextNote } from "../src/main/context-compiler";
import { CONTEXT_NOTE_TEMPLATE } from "../src/main/context-note-prompt";

const SVC = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service");

describe("FIX107 — one source for the context note", () => {
  it("the desktop constant is byte-identical to chat-edge's embedded asset", () => {
    const canon = fs.readFileSync(path.join(SVC, "prompt_context_note.txt"), "utf8").replace(/\r\n/g, "\n").trim();
    expect(CONTEXT_NOTE_TEMPLATE).toBe(canon);
  });

  it("chat-edge actually embeds that asset, and its old inline wording is gone", () => {
    const go = fs.readFileSync(path.join(SVC, "context_compiler.go"), "utf8");
    expect(go).toContain("//go:embed prompt_context_note.txt");
    expect(go).not.toContain("This conversation is long:");
  });

  it("every section is present", () => {
    const parts = parseContextNote(CONTEXT_NOTE_TEMPLATE);
    expect(Object.keys(parts).sort()).toEqual(["CLOSE", "HEAD_DROPPED", "HEAD_KEPT", "OPEN", "SUMMARY_COMPLETE", "SUMMARY_FAILED", "SUMMARY_OFF", "SUMMARY_PARTIAL"]);
  });

  it("the owner's chat: the note says the earliest message in view is not where the conversation began", () => {
    const note = renderContextNote(17, 1, false, "complete");
    expect(note).toContain("the earliest message you can see is not where the conversation began");
    expect(note).toContain("Never present a message you can see as the first one");
    expect(renderContextNote(17, 4, true, "complete")).not.toContain("not where the conversation began");
  });

  it("compileChain appends it, and a status the caller passes is the one it states", () => {
    const sys = { role: "system" as const, content: "S" };
    const msgs = [sys, ...Array.from({ length: 40 }, (_, i) => ({ role: (i % 2 ? "assistant" : "user") as "user" | "assistant", content: "x".repeat(4000) })), { role: "user" as const, content: "q" }];
    const basis = { model: "m", window: 16_384, outputTokens: 8192 };
    const cc = compileChain(msgs, basis as never, 0, { summaryStatus: "partial" });
    expect(cc.compacted).toBe(true);
    expect(String(cc.messages[0]!.content)).toContain("A summary of part of it is in <conversation_state> above");
  });
});

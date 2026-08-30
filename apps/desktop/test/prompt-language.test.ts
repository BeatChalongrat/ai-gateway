// FIX32 Part 3b — the language/tone rules exist ONCE.
//
// The canonical asset is chat-edge's prompt_language_rules.txt (Go embeds it
// into languageRulesBlock); the desktop holds the same text as
// LANGUAGE_RULES_BLOCK. This test byte-compares the two and FAILS on drift —
// "we will remember to update both" is exactly what failed four rounds
// running (Rust detector, DLP passes, model capabilities, the shell line).
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { LANGUAGE_RULES_BLOCK, subAgentSystemPrompt } from "../src/main/prompt-assembler";
import { PromptAssembler } from "../src/main/prompt-assembler";

const CANONICAL = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service", "prompt_language_rules.txt");

describe("FIX32 Part 3b — one source for the language rules", () => {
  it("the desktop block is byte-identical to chat-edge's embedded asset", () => {
    const canon = fs.readFileSync(CANONICAL, "utf8").replace(/\r\n/g, "\n").trim();
    expect(LANGUAGE_RULES_BLOCK.replace(/\r\n/g, "\n").trim()).toBe(canon);
  });

  it("chat-edge actually embeds that asset (no second wording left in Go)", () => {
    const go = fs.readFileSync(path.join(path.dirname(CANONICAL), "prompt_compiler.go"), "utf8");
    expect(go).toContain('//go:embed prompt_language_rules.txt');
    // The old inline const is gone — the block text lives ONLY in the .txt.
    expect(go).not.toContain("pick ครับ or ค่ะ/คะ");
  });

  it("the rules carry what §3.2 said the desktop was missing", () => {
    expect(LANGUAGE_RULES_BLOCK).toContain("pick ครับ or ค่ะ/คะ and use it consistently");
    expect(LANGUAGE_RULES_BLOCK).toContain("Greet the user only once");
    expect(LANGUAGE_RULES_BLOCK).toContain("Don't repeat the user's question back");
    expect(LANGUAGE_RULES_BLOCK).toContain("Reply in the user's language");
  });

  it("both desktop prompts carry the shared block", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "src", "main", "prompt-assembler.ts"), "utf8");
    // main prompt + sub-agent prompt each interpolate the ONE constant.
    expect(src.match(/\$\{LANGUAGE_RULES_BLOCK\}/g) ?? []).toHaveLength(2);
    expect(subAgentSystemPrompt("C:\\Users\\x", [])).toContain("pick ครับ or ค่ะ/คะ");
    // No leftover second wording of the language-match rule.
    expect(src).not.toContain("Always respond in the SAME language");
    void PromptAssembler;
  });
});

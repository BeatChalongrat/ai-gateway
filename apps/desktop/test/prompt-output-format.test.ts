// FIX107 Part B2 — one format rule for both surfaces.
//
// The same question on qwen3.8 came back as a table on the web and a numbered
// list on the desktop. The web prompt has an <output_format> layer; the
// desktop's "## Response Style" had no format rule at all. The web text is now
// chat-edge's prompt_output_format.txt, and the desktop sends it byte for byte.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { OUTPUT_FORMAT_BLOCK } from "../src/main/output-format-prompt";
import { baseSystemPrompt } from "../src/main/prompt-assembler";

const SVC = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service");

describe("FIX107 — the desktop tells the model how to choose a format, in the web's words", () => {
  it("the desktop constant is byte-identical to chat-edge's embedded asset", () => {
    const canon = fs.readFileSync(path.join(SVC, "prompt_output_format.txt"), "utf8").replace(/\r\n/g, "\n").trim();
    expect(OUTPUT_FORMAT_BLOCK).toBe(canon);
  });

  it("chat-edge embeds that asset as its output format layer", () => {
    const go = fs.readFileSync(path.join(SVC, "prompt_compiler.go"), "utf8");
    expect(go).toContain("//go:embed prompt_output_format.txt");
    expect(go).not.toContain("const outputFormatBlock = `");
  });

  it("the base prompt carries it right after Response Style, with and without tools", () => {
    for (const tools of [true, false]) {
      const p = baseSystemPrompt("C:\\Users\\u", tools, []);
      expect(p).toContain(`## Output format\n${OUTPUT_FORMAT_BLOCK}`);
      expect(p.indexOf("## Output format")).toBeGreaterThan(p.indexOf("## Response Style"));
    }
  });
});

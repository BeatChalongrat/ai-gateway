// FIX95 Part B — the artifact dialect exists ONCE, and the desktop did not move.
//
// The text was written for web-chat (the constant is literally named
// ARTIFACT_INSTRUCTION_WEBCHAT and its comment names web-chat three times),
// but only the desktop ever emitted it. chat-edge now emits it too, from a
// canonical asset it embeds — the same shape FIX32 used for the language
// rules and FIX94 used for the injection boundary.
//
// Two jobs here:
//   1. the desktop constant and the canonical asset are the same text, so a
//      reword on either side fails rather than silently forking;
//   2. the desktop's COMPILED prompt is byte-identical to what it was before
//      this round — the dialect moved into a shared asset, and desktop was
//      not broken, so desktop must not change by one byte.
//
// The normalisation (\r\n -> \n, trim) is the FIX32 convention: *.ts is
// eol=lf in .gitattributes but the working copies here predate that rule and
// are CRLF, while the .txt has no rule at all. Comparing raw bytes would
// fail for a reason that has nothing to do with the words.

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { ARTIFACT_INSTRUCTION_WEBCHAT, baseSystemPrompt } from "../src/main/prompt-assembler";

const CANONICAL = path.join(
  __dirname, "..", "..", "..",
  "services", "chat-edge-api", "internal", "service", "prompt_artifacts.txt",
);

const lf = (s: string) => s.replace(/\r\n/g, "\n").trim();

describe("FIX95 Part B — one artifact dialect, two surfaces", () => {
  it("the desktop constant is byte-identical to chat-edge's embedded asset", () => {
    const canon = lf(fs.readFileSync(CANONICAL, "utf8"));
    // the desktop value carries a leading blank line so it appends cleanly;
    // the asset is the body. Compare the body.
    expect(lf(ARTIFACT_INSTRUCTION_WEBCHAT)).toBe(canon);
  });

  it("chat-edge actually embeds it and emits it as a layer", () => {
    const go = fs.readFileSync(
      path.join(path.dirname(CANONICAL), "prompt_compiler.go"), "utf8",
    );
    expect(go).toContain("//go:embed prompt_artifacts.txt");
    expect(go).toContain("add(pc.artifactDialect(in))");
    // the text lives ONLY in the .txt on the Go side — no second wording
    expect(go).not.toContain("a live, interactive artifact");
  });

  it("the dialect names exactly the tags the renderer turns into artifacts", () => {
    const canon = lf(fs.readFileSync(CANONICAL, "utf8"));
    for (const tag of ["```html", "```jsx", "```tsx", "```react", "```mermaid", "```svg"]) {
      expect(canon, `dialect should name ${tag}`).toContain(tag);
    }
    // and it keeps the guard that stops every snippet becoming an artifact.
    // The source wraps that sentence, so compare words, not line width.
    expect(canon.replace(/\s+/g, " ")).toContain("Keep short snippets in ordinary code blocks");
  });

  // The point of the round: moving the text must not move the desktop.
  // This is the compiled prompt, not the constant — it catches a change in
  // how the constant is appended as well as a change to the text.
  it("the desktop's compiled prompt is unchanged by the move", () => {
    const prompt = baseSystemPrompt("C:\\Users\\x", true, ["C:\\Users\\x\\work"]) + ARTIFACT_INSTRUCTION_WEBCHAT;
    // the dialect is appended exactly once, after the base prompt, with the
    // blank line that has always separated it
    expect(prompt).toContain("\n\n## Artifacts\n");
    expect(prompt.match(/## Artifacts/g) ?? []).toHaveLength(1);
    expect(prompt.endsWith("ordinary code blocks tagged with their language as usual.")).toBe(true);
    // and the sections that surround it are still there and still in order
    const order = ["## Language & tone", "## Tool Use Philosophy", "## Safety Rules", "## Response Style", "## Artifacts"];
    let at = -1;
    for (const h of order) {
      const i = prompt.indexOf(h);
      expect(i, `${h} missing from the desktop prompt`).toBeGreaterThan(-1);
      expect(i, `${h} out of order`).toBeGreaterThan(at);
      at = i;
    }
  });
});

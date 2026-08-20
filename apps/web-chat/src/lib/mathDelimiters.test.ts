// Batch 2 T1 — math delimiter normalization corpus.
//
// GPT-family models emit \[…\] / \(…\) by default; remark-math only
// parses $-delimiters. The normalizer converts the backslash forms to
// the $-forms WITHOUT touching anything that merely looks like math:
// fenced code, inline code, escaped backslashes, markdown-link
// lookalikes, unterminated (still-streaming) delimiters, and the $
// forms themselves. The SAME corpus runs against the desktop port
// (SranDesktop.Tests Rendering/MathDelimiterTests.cs) — the two
// implementations must agree byte-for-byte.

import { describe, expect, it } from "vitest";

import { normalizeMathDelimiters } from "./mathDelimiters";

const n = normalizeMathDelimiters;

describe("normalizeMathDelimiters — conversions", () => {
  it("converts a standalone \\[x^2\\] to a $$ BLOCK (true display mode)", () => {
    // remark-math renders single-line $$x$$ inline; only the block form
    // (closing $$ on its own line) gives display mode. Desktop's
    // MathGuard treats both as display, so the block form agrees
    // everywhere.
    expect(n("\\[x^2\\]")).toBe("$$\nx^2\n$$");
  });

  it("converts a mid-paragraph \\[x\\] to inline $$x$$ (paragraph must not split)", () => {
    expect(n("the result \\[x^2\\] follows")).toBe("the result $$x^2$$ follows");
  });

  it("converts inline \\(a+b\\) to $a+b$", () => {
    expect(n("\\(a+b\\)")).toBe("$a+b$");
  });

  it("converts a multi-line display block", () => {
    expect(n("\\[\nE = mc^2\n\\]")).toBe("$$\nE = mc^2\n$$");
  });

  it("converts mid-paragraph occurrences with Thai text either side (no whitespace assumptions)", () => {
    expect(n("ราคา \\(x\\) บาท")).toBe("ราคา $x$ บาท");
    expect(n("ผลลัพธ์คือ\\[x^2\\]ครับ")).toBe("ผลลัพธ์คือ$$x^2$$ครับ");
  });

  it("converts several delimiters in one message", () => {
    expect(n("first \\(a\\) then \\[b\\] end")).toBe("first $a$ then $$b$$ end");
  });
});

describe("normalizeMathDelimiters — must NOT touch", () => {
  it("leaves $$…$$ and $…$ unchanged", () => {
    const src = "display $$E = mc^2$$ and inline $x^2$ stay";
    expect(n(src)).toBe(src);
  });

  it("leaves currency untouched — between $100 and $200 is not math", () => {
    const src = "between $100 and $200";
    expect(n(src)).toBe(src);
  });

  it("leaves \\[ inside a fenced code block alone", () => {
    const src = "```latex\n\\[x^2\\]\n```";
    expect(n(src)).toBe(src);
  });

  it("leaves a C# fence with attribute brackets alone", () => {
    const src = '```csharp\n[property: JsonPropertyName("x")] string X\n```';
    expect(n(src)).toBe(src);
  });

  it("leaves \\[ inside inline code alone", () => {
    const src = "use `\\[x\\]` to write display math";
    expect(n(src)).toBe(src);
  });

  it("leaves \\[ inside double-backtick inline code alone", () => {
    const src = "escape it as ``\\[x\\]`` here";
    expect(n(src)).toBe(src);
  });

  it("leaves an unterminated \\[ alone (still streaming)", () => {
    const src = "the result is \\[x^2 + ";
    expect(n(src)).toBe(src);
  });

  it("leaves a chunk boundary that split \\ from [ alone", () => {
    const src = "the result is \\";
    expect(n(src)).toBe(src);
  });

  it("leaves \\( unterminated on its line alone (inline must close on the same line)", () => {
    const src = "value \\(a + b\nmore text \\)";
    expect(n(src)).toBe(src);
  });

  it("leaves a markdown link with escaped brackets alone", () => {
    const src = "\\[not math\\](https://example.com)";
    expect(n(src)).toBe(src);
  });

  it("leaves escaped backslashes alone (\\\\[ is a literal backslash + bracket)", () => {
    const src = "literal \\\\[x\\\\] brackets";
    expect(n(src)).toBe(src);
  });

  it("leaves bare brackets without a backslash alone", () => {
    const src = "[property: JsonPropertyName] and [a link](https://x)";
    expect(n(src)).toBe(src);
  });

  it("leaves content inside an unclosed (streaming) fence alone", () => {
    const src = "```python\nprint('\\[not math\\]')";
    expect(n(src)).toBe(src);
  });

  it("leaves a $$ math block's body alone", () => {
    const src = "$$\n\\[nested\\]\n$$";
    expect(n(src)).toBe(src);
  });
});

describe("normalizeMathDelimiters — streaming stability", () => {
  it("is byte-stable on the already-complete prefix as the tail grows", () => {
    // The block-splitter memoization contract: appending text may only
    // change the tail. The normalizer must preserve that — a completed
    // \[…\] stays converted identically at every later length.
    const full = "intro \\[x^2\\] middle \\(a\\) tail text that keeps growing";
    const closedAt = full.indexOf("\\)") + 2;
    const converted = n(full.slice(0, closedAt));
    for (let len = closedAt; len <= full.length; len++) {
      expect(n(full.slice(0, len)).startsWith(converted)).toBe(true);
    }
  });

  it("converts the moment the closer arrives", () => {
    expect(n("value \\[x^2")).toBe("value \\[x^2");
    expect(n("value \\[x^2\\]")).toBe("value $$x^2$$");
  });
});

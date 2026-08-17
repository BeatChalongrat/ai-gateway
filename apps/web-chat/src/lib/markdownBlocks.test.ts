import { describe, expect, it } from "vitest";
import { splitMarkdownBlocks } from "./markdownBlocks";

describe("splitMarkdownBlocks", () => {
  it("keeps a code fence with blank lines inside as one block", () => {
    const src = [
      "```python",
      "def a():",
      "    pass",
      "",
      "def b():",
      "    pass",
      "```",
    ].join("\n");
    const blocks = splitMarkdownBlocks(src);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].text).toBe(src);
  });

  it("splits two paragraphs separated by a blank line into two blocks", () => {
    const blocks = splitMarkdownBlocks("first paragraph\n\nsecond paragraph");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toEqual({ key: "0", text: "first paragraph" });
    expect(blocks[1]).toEqual({ key: "1", text: "second paragraph" });
  });

  it("keeps a GFM table (no blank lines) as one block", () => {
    const src = ["| a | b |", "|---|---|", "| 1 | 2 |", "| 3 | 4 |"].join("\n");
    const blocks = splitMarkdownBlocks(src);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].text).toBe(src);
  });

  it("keeps everything after an unclosed fence as the final block", () => {
    const src = "intro text\n\n```js\nconst a = 1;\n\nconst b = 2;";
    const blocks = splitMarkdownBlocks(src);
    expect(blocks).toHaveLength(2);
    expect(blocks[0].text).toBe("intro text");
    expect(blocks[1].text).toBe("```js\nconst a = 1;\n\nconst b = 2;");
  });

  it("keeps a math block ($$) with blank lines inside as one block", () => {
    const src = "$$\nE = mc^2\n\nF = ma\n$$";
    const blocks = splitMarkdownBlocks(src);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].text).toBe(src);
  });

  it("collapses consecutive blank lines into a single boundary and skips empty blocks", () => {
    const blocks = splitMarkdownBlocks("\n\nfirst\n\n\n\nsecond\n\n");
    expect(blocks.map((b) => b.text)).toEqual(["first", "second"]);
    expect(blocks.map((b) => b.key)).toEqual(["0", "1"]);
  });

  it("stability: appending tokens only ever changes the last block", () => {
    // Simulate a streamed reply arriving in chunks, mid-fence included.
    const chunks = [
      "Here is the plan:",
      "\n\nStep one is easy.\n\nStep two needs code:\n\n```py\nx = 1",
      "\n\ny = 2",
      "\n```",
      "\n\nAll done.",
    ];
    let src = "";
    let prev = splitMarkdownBlocks(src);
    for (const chunk of chunks) {
      src += chunk;
      const next = splitMarkdownBlocks(src);
      // Every block except the previous snapshot's last one must be
      // byte-identical — this is what lets the renderer memo per block.
      for (let i = 0; i < prev.length - 1; i++) {
        expect(next[i]).toEqual(prev[i]);
      }
      prev = next;
    }
    expect(prev.map((b) => b.text)).toEqual([
      "Here is the plan:",
      "Step one is easy.",
      "Step two needs code:",
      "```py\nx = 1\n\ny = 2\n```",
      "All done.",
    ]);
  });
});

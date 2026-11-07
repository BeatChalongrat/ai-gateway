// FIX76 — every attached image is announced by a label text block right
// before it ("Image 2 of 3: design-ref.png"), and a label never outlives
// its image: the blind-model strip and the history prune both take the
// label with the block. The wording is one fixed English form shared
// with chat-edge (its contract test reads attachments.ts and fails on
// drift), pinned here by the literal.
import { describe, expect, it } from "vitest";

import {
  composeUserContent,
  IMAGE_LABEL_MAX_NAME,
  IMAGE_LABEL_RE,
  IMAGE_LABEL_TEMPLATE,
  IMAGE_NOT_SENT_NOTE,
  imageLabel,
  imageLabelName,
  isImageLabelBlock,
  pruneAttachedImages,
  resolveAttachments,
  stripImageBlocks,
  type AttachmentFileRow,
  type AttachmentSource,
  type ResolvedAttachments,
} from "../src/main/attachments";
import type { GatewayContentBlock } from "../src/main/gateway-client";

const log = { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} } as never;
const img = (n: number) => ({ type: "image_url" as const, image_url: { url: `data:image/png;base64,${n}` } });
const resolved = (names: string[], text = ""): ResolvedAttachments => ({
  imageBlocks: names.map((_, i) => img(i + 1)),
  imageNames: names,
  observations: text,
  fileIds: names.map((_, i) => `file_${i}`),
  attached: names.map((n, i) => ({ id: `file_${i}`, filename: n, kind: "image" as const })),
  failures: [],
});
const texts = (blocks: GatewayContentBlock[]) => blocks.map((b) => (b.type === "text" ? b.text : "<image>"));

describe("FIX76 — the label", () => {
  it("is one fixed English form: 'Image 2 of 3: design-ref.png'", () => {
    expect(IMAGE_LABEL_TEMPLATE).toBe("Image {n} of {total}: {filename}");
    expect(imageLabel(2, 3, "design-ref.png")).toBe("Image 2 of 3: design-ref.png");
    expect(IMAGE_LABEL_RE.test("Image 2 of 3: design-ref.png")).toBe(true);
    expect(IMAGE_LABEL_RE.test("Image two: design-ref.png")).toBe(false);
  });

  it("keeps a filename on one line and bounded", () => {
    expect(imageLabelName("a\nb\tc.png")).toBe("a b c.png");
    expect(imageLabelName("   ")).toBe("image");
    const long = "x".repeat(200) + ".png";
    const name = imageLabelName(long);
    expect(name.length).toBe(IMAGE_LABEL_MAX_NAME);
    expect(name.endsWith("…")).toBe(true);
  });
});

describe("FIX76 — composeUserContent interleaves label then image, user text last", () => {
  it("two images → [label 1, image, label 2, image, text]", () => {
    const blocks = composeUserContent("ใช้สีจากรูป 2", resolved(["screenshot-old.png", "design-ref.png"])) as GatewayContentBlock[];
    expect(texts(blocks)).toEqual(["Image 1 of 2: screenshot-old.png", "<image>", "Image 2 of 2: design-ref.png", "<image>", "ใช้สีจากรูป 2"]);
    expect(blocks.filter(isImageLabelBlock)).toHaveLength(2);
  });

  it("one image, no text → label, image, the default prompt", () => {
    const blocks = composeUserContent("  ", resolved(["shot.png"])) as GatewayContentBlock[];
    expect(texts(blocks)).toEqual(["Image 1 of 1: shot.png", "<image>", "What's in this image?"]);
  });

  it("no images → the plain string, no label anywhere", () => {
    expect(composeUserContent("hello", resolved([]))).toBe("hello");
  });

  it("the resolver records the filename for each image in attach order", async () => {
    const rows: AttachmentFileRow[] = [
      { id: "file_a", filename: "first.png", content_type: "image/png", file_size_bytes: 1, extraction_status: "completed" },
      { id: "file_b", filename: "second.jpg", content_type: "image/jpeg", file_size_bytes: 1, extraction_status: "completed" },
    ];
    const src: AttachmentSource = {
      listFiles: async () => rows,
      getFileText: async () => ({ status: "completed", text: "", truncated: false, ocr: false }),
      downloadFileBytes: async (id) => ({ bytes: Buffer.from(id), mime: "image/png" }),
    };
    const r = await resolveAttachments(src, async (b, m) => `data:${m};base64,${b.toString("base64")}`, "conv", ["file_b", "file_a"], { log });
    expect(r.imageNames).toEqual(["second.jpg", "first.png"]);
    expect(texts(composeUserContent("x", r) as GatewayContentBlock[])).toEqual(["Image 1 of 2: second.jpg", "<image>", "Image 2 of 2: first.png", "<image>", "x"]);
  });
});

describe("FIX76 — a label never outlives its image", () => {
  it("stripImageBlocks (blind model) drops the labels with the images", () => {
    const messages = [{ role: "user", content: composeUserContent("what colour?", resolved(["a.png", "b.png"])) }];
    expect(stripImageBlocks(messages)).toBe(2);
    const content = messages[0]!.content as string;
    expect(content).toBe(`${IMAGE_NOT_SENT_NOTE}\n${IMAGE_NOT_SENT_NOTE}\nwhat colour?`);
    expect(content).not.toMatch(IMAGE_LABEL_RE);
    expect(content).not.toContain("a.png");
  });

  it("pruneAttachedImages drops the label of a dropped image and keeps the kept one, number unchanged", () => {
    const messages = [
      { role: "user", content: composeUserContent("first turn", resolved(["one.png", "two.png", "three.png"])) },
      { role: "assistant", content: "ok" },
      { role: "user", content: composeUserContent("second turn", resolved(["four.png"])) },
    ];
    pruneAttachedImages(messages, 2, () => false);
    const first = messages[0]!.content as GatewayContentBlock[];
    // 1 kept out of 3 (the newest turn took 1 of the 2 allowed): the LAST image and ITS label only
    expect(texts(first)).toEqual(["Image 3 of 3: three.png", "<image>", "first turn (2 attached images removed to save context)"]);
    expect(first.filter(isImageLabelBlock)).toHaveLength(1);
    const second = messages[2]!.content as GatewayContentBlock[];
    expect(texts(second)).toEqual(["Image 1 of 1: four.png", "<image>", "second turn"]);
  });

  it("pruning every image of a message leaves plain text with no label", () => {
    const messages = [
      { role: "user", content: composeUserContent("old", resolved(["a.png"])) },
      { role: "user", content: composeUserContent("new", resolved(["b.png", "c.png"])) },
    ];
    pruneAttachedImages(messages, 2, () => false);
    expect(messages[0]!.content).toBe("old (1 attached image removed to save context)");
  });

  it("every content shape: no text block left anywhere matches the label pattern once its image is gone", () => {
    const messages = [{ role: "user", content: composeUserContent("q", resolved(["a.png", "b.png", "c.png"])) }];
    pruneAttachedImages(messages, 1, () => false);
    stripImageBlocks(messages);
    const c = messages[0]!.content;
    const all = typeof c === "string" ? [c] : (c as GatewayContentBlock[]).map((b) => (b.type === "text" ? b.text : ""));
    for (const t of all) for (const line of t.split("\n")) expect(IMAGE_LABEL_RE.test(line)).toBe(false);
  });
});

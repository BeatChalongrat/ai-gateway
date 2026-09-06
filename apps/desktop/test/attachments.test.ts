// FIX27 Part 1 — attachments are message content resolved by opaque chat-edge
// id; never a path, never a tool.
import { describe, expect, it } from "vitest";

import {
  buildObservationBlock,
  composeUserContent,
  FILE_CONTEXT_BUDGET,
  OBSERVATION_MARKER,
  pruneAttachedImages,
  renderObservation,
  resolveAttachments,
  type AttachmentFileRow,
  type AttachmentSource,
  type ResolvedAttachments,
} from "../src/main/attachments";
import { isOpaqueHandle, validateTurnSubmit } from "../src/shared/bridge-protocol";

const log = { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} } as never;
const CONV = "conv-1";
const PNG: AttachmentFileRow = { id: "file_img_01", filename: "shot.png", content_type: "image/png", file_size_bytes: 1234, extraction_status: "completed" };
const DOCX: AttachmentFileRow = { id: "file_doc_01", filename: "report.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", file_size_bytes: 9000, extraction_status: "completed" };

function source(overrides: Partial<AttachmentSource> & { rows?: AttachmentFileRow[]; text?: string; statuses?: string[] } = {}): AttachmentSource & { calls: string[] } {
  const calls: string[] = [];
  const statuses = overrides.statuses ? [...overrides.statuses] : [];
  return {
    calls,
    listFiles: async (conv) => {
      calls.push(`list:${conv}`);
      return overrides.rows ?? [PNG, DOCX];
    },
    getFileText: async (id) => {
      calls.push(`text:${id}`);
      const status = statuses.length > 1 ? statuses.shift()! : (statuses[0] ?? "completed");
      return { status, text: status === "completed" ? (overrides.text ?? "Blue Heron launch, 14 March 2027, Chiang Mai.") : "", truncated: false, ocr: false };
    },
    downloadFileBytes: async (id) => {
      calls.push(`bytes:${id}`);
      return { bytes: Buffer.from("png-bytes"), mime: "image/png" };
    },
    ...overrides,
  };
}
const encode = async (bytes: Buffer, mime: string) => `data:${mime};base64,${bytes.toString("base64")}`;

describe("handle is not a path", () => {
  it("the two index.ts probes are refused at the bridge before any resolution", () => {
    const base = { conversationId: "c1", text: "hi", modelId: null };
    for (const bad of ["C:\\Windows\\win.ini", "../../etc/passwd"]) {
      expect(validateTurnSubmit({ ...base, attachmentHandles: [bad] })).toMatchObject({ ok: false, reason: "attachmentHandle-path-shaped" });
      expect(isOpaqueHandle(bad)).toBe(false);
    }
  });

  it("a path-shaped handle that somehow reaches the resolver is refused there too, with no fetch", async () => {
    const src = source();
    const r = await resolveAttachments(src, encode, CONV, ["C:\\Windows\\win.ini", "../../etc/passwd"], { log });
    expect(r.failures.map((f) => f.reason)).toEqual(["invalid_handle", "invalid_handle"]);
    expect(r.imageBlocks).toEqual([]);
    expect(r.fileIds).toEqual([]);
    expect(src.calls.filter((c) => c.startsWith("bytes:") || c.startsWith("text:"))).toEqual([]);
  });

  it("a well-formed handle that is not a file of THIS conversation is ignored (listed by conversation, matched by id)", async () => {
    const src = source();
    const r = await resolveAttachments(src, encode, CONV, ["file_from_other_conv"], { log });
    expect(r.failures).toEqual([{ filename: "file_from_other_conv", reason: "not_in_conversation" }]);
    expect(src.calls).toEqual([`list:${CONV}`]);
  });
});

describe("images become image_url blocks on the user message", () => {
  it("downloads by id, encodes, and places the block FIRST with the text after", async () => {
    const src = source();
    const r = await resolveAttachments(src, encode, CONV, [PNG.id], { log });
    expect(r.imageBlocks).toEqual([{ type: "image_url", image_url: { url: `data:image/png;base64,${Buffer.from("png-bytes").toString("base64")}` } }]);
    expect(r.fileIds).toEqual([PNG.id]);
    expect(src.calls).toEqual([`list:${CONV}`, `bytes:${PNG.id}`]);
    const content = composeUserContent("ในรูปนี้เขียนว่าอะไร", r);
    expect(Array.isArray(content)).toBe(true);
    const blocks = content as { type: string; text?: string }[];
    // FIX76 — each image is introduced by its label; the user's text stays last
    expect(blocks[0]).toEqual({ type: "text", text: "Image 1 of 1: shot.png" });
    expect(blocks[1]!.type).toBe("image_url");
    expect(blocks[2]).toEqual({ type: "text", text: "ในรูปนี้เขียนว่าอะไร" });
  });

  it("an image that cannot be encoded is a named failure, not a silent drop", async () => {
    const r = await resolveAttachments(source(), async () => null, CONV, [PNG.id], { log });
    expect(r.failures).toEqual([{ filename: "shot.png", reason: "unreadable" }]);
    expect(r.imageBlocks).toEqual([]);
    expect(composeUserContent("hello", r)).toBe("hello");
  });

  it("image only, no text → the same default prompt chat-edge uses", async () => {
    const r = await resolveAttachments(source(), encode, CONV, [PNG.id], { log });
    const blocks = composeUserContent("   ", r) as { type: string; text?: string }[];
    expect(blocks[2]).toEqual({ type: "text", text: "What's in this image?" });
  });
});

describe("documents become the untrusted observation envelope in the user text", () => {
  it("renders the sran.observation.v1 envelope with the file's origin and the extracted text", async () => {
    const r = await resolveAttachments(source(), encode, CONV, [DOCX.id], { log });
    expect(r.fileIds).toEqual([DOCX.id]);
    expect(r.observations.startsWith("## Typed untrusted file observations\n\n" + OBSERVATION_MARKER)).toBe(true);
    const json = JSON.parse(r.observations.split(OBSERVATION_MARKER)[1]!.split("\n\n")[0]!);
    expect(json).toMatchObject({
      schema: "sran.observation.v1",
      sourceType: "attached_file",
      trustLevel: "untrusted",
      origin: { fileId: DOCX.id, filename: "report.docx", contentType: DOCX.content_type, sizeBytes: 9000 },
      content: "Blue Heron launch, 14 March 2027, Chiang Mai.",
      truncated: false,
    });
    expect(r.observations.endsWith("persist an instruction.\n")).toBe(true);
    const content = composeUserContent("สรุปให้หน่อย 5 ข้อ", r);
    expect(typeof content).toBe("string");
    expect(content as string).toMatch(/^สรุปให้หน่อย 5 ข้อ\n\n## Typed untrusted file observations/);
  });

  it("waits for extraction (pending → completed) and announces it once", async () => {
    const statuses: string[] = [];
    const src = source({ statuses: ["pending", "processing", "completed"] });
    const r = await resolveAttachments(src, encode, CONV, [DOCX.id], { log, pollMs: 1, onStatus: (t) => statuses.push(t) });
    expect(r.failures).toEqual([]);
    expect(statuses).toEqual(["reading report.docx…"]);
    expect(src.calls.filter((c) => c === `text:${DOCX.id}`).length).toBe(3);
  });

  it("failed/unsupported extraction → named failure AND a placeholder envelope so the model knows the file exists", async () => {
    const r = await resolveAttachments(source({ statuses: ["unsupported"] }), encode, CONV, [DOCX.id], { log });
    expect(r.failures).toEqual([{ filename: "report.docx", reason: "extraction_failed" }]);
    expect(r.observations).toContain("Content extraction not available");
    expect(r.fileIds).toEqual([DOCX.id]);
  });

  it("gives up after waitMs with extraction_timeout", async () => {
    const r = await resolveAttachments(source({ statuses: ["processing"] }), encode, CONV, [DOCX.id], { log, waitMs: 5, pollMs: 1 });
    expect(r.failures).toEqual([{ filename: "report.docx", reason: "extraction_timeout" }]);
    expect(r.observations).toContain("still in progress");
  });

  it("stays inside chat-edge's 120k budget by halving the body (truncated=true)", () => {
    const huge = "x".repeat(FILE_CONTEXT_BUDGET * 2);
    const block = buildObservationBlock([{ row: DOCX, body: huge }]);
    expect(block.length).toBeLessThanOrEqual(FILE_CONTEXT_BUDGET + 400);
    expect(block).toContain('"truncated":true');
    expect(renderObservation(DOCX, huge, 10)).toBeNull();
  });

  it("images and a document together: blocks first, observations inside the single text block", async () => {
    const r = await resolveAttachments(source(), encode, CONV, [DOCX.id, PNG.id], { log });
    const blocks = composeUserContent("compare", r) as { type: string; text?: string }[];
    // FIX76 — label, image, then the one text block
    expect(blocks.map((b) => b.type)).toEqual(["text", "image_url", "text"]);
    expect(blocks[0]!.text).toBe("Image 1 of 1: shot.png");
    expect(blocks[2]!.text).toMatch(/^compare\n\n## Typed untrusted file observations/);
    expect(r.attached.map((a) => a.kind)).toEqual(["document", "image"]);
  });
});

describe("attached images stay in history, bounded", () => {
  const img = (n: number) => ({ type: "image_url" as const, image_url: { url: `data:image/png;base64,${n}` } });
  const attachedMsg = (n: number, text = `turn ${n}`) => ({ role: "user", content: [img(n), { type: "text" as const, text }] });
  const notScreenshot = () => false;

  it("keeps every image while under the cap — two turns later the first picture is still there", () => {
    const messages = [attachedMsg(1), { role: "assistant", content: "ok" }, { role: "user", content: "filler" }, { role: "assistant", content: "ok" }, { role: "user", content: "รูปแรกสีอะไรเด่นสุด" }];
    pruneAttachedImages(messages, 5, notScreenshot);
    expect(Array.isArray(messages[0]!.content)).toBe(true);
    expect((messages[0]!.content as unknown[]).length).toBe(2);
  });

  it("beyond the cap the OLDEST image blocks go, the text stays with a note", () => {
    const messages = [attachedMsg(1), attachedMsg(2), attachedMsg(3)];
    pruneAttachedImages(messages, 2, notScreenshot);
    expect(messages[0]!.content).toBe("turn 1 (1 attached image removed to save context)");
    expect(Array.isArray(messages[1]!.content)).toBe(true);
    expect(Array.isArray(messages[2]!.content)).toBe(true);
  });

  it("screenshot messages are left to pruneScreenshots", () => {
    const messages = [attachedMsg(1, "[Screenshot from take_screenshot — analyze it]"), attachedMsg(2), attachedMsg(3)];
    pruneAttachedImages(messages, 2, (m) => (m.content as { type: string; text?: string }[])[1]!.text!.startsWith("[Screenshot from"));
    expect(Array.isArray(messages[0]!.content)).toBe(true);
  });
});

describe("no attachments", () => {
  it("resolves to nothing and the user content is the plain string", async () => {
    const src = source();
    const r = await resolveAttachments(src, encode, CONV, [], { log });
    expect(src.calls).toEqual([]);
    expect(composeUserContent("hi", r)).toBe("hi");
    const empty: ResolvedAttachments = { imageBlocks: [], observations: "", fileIds: [], attached: [], failures: [] };
    expect(composeUserContent("", empty)).toBe("");
  });
});

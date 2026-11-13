import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HostTools } from "../src/main/host-tools";
import { ChatEdgeClient } from "../src/main/chat-edge-client";
import { LOCAL_FILE_MAX_BYTES } from "../src/main/local-office-files";
import { applyApprovalMode, classifyDanger, resolveScope, toolMeta } from "../src/main/permissions";

const fileId = "b602f006-8a1c-4a95-81bc-e541fa177ae0";
let temp: string;
let granted: string;
beforeEach(async () => {
  temp = await fs.mkdtemp(path.join(os.tmpdir(), "sran-office-test-"));
  granted = path.join(temp, "granted");
  await fs.mkdir(granted);
});
afterEach(async () => {
  vi.unstubAllGlobals();
  if (path.dirname(temp) !== path.resolve(os.tmpdir()) || !path.basename(temp).startsWith("sran-office-test-")) throw new Error("Invalid cleanup path");
  await fs.rm(temp, { recursive: true, force: true });
});

function setup() {
  const chat = {
    uploadFileBytes: vi.fn(async () => fileId as string | null),
    listFiles: vi.fn(async () => [{ id: fileId }]),
    downloadFileBytes: vi.fn(async () => ({ bytes: Buffer.from("edited document bytes"), mime: "application/pdf" })),
  };
  const tools = new HostTools({} as never, chat as never, vi.fn() as never, {} as never, () => false);
  const ctx = { conversationId: "conv", toolCallId: "tool", signal: new AbortController().signal, grantedFolders: [granted] };
  return { chat, tools, ctx };
}

describe("local Office file transfer", () => {
  it("reads a chunked download without Content-Length", async () => {
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(Buffer.from("abc"));
      controller.enqueue(Buffer.from("def"));
      controller.close();
    } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { headers: { "content-type": "application/pdf" } })));
    const chat = new ChatEdgeClient("https://example.test", () => "test-token", { warn: vi.fn() } as never);
    expect(await chat.downloadFileBytes(fileId)).toEqual({ bytes: Buffer.from("abcdef"), mime: "application/pdf" });
  });

  it("cancels a chunked download once it exceeds the size cap", async () => {
    const cancelled = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) { controller.enqueue(new Uint8Array(1024 * 1024)); },
      cancel: cancelled,
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream)));
    const chat = new ChatEdgeClient("https://example.test", () => "test-token", { warn: vi.fn() } as never);
    expect(await chat.downloadFileBytes(fileId)).toBeNull();
    expect(cancelled).toHaveBeenCalledOnce();
  });

  it("uploads actual bytes and returns a reusable conversation file ID", async () => {
    const { tools, chat, ctx } = setup();
    const source = path.join(granted, "รายงาน.docx");
    await fs.writeFile(source, Buffer.from([0x50, 0x4b, 0, 0xff]));
    const result = await tools.execute("import_local_file", { path: source }, ctx);
    expect(result.ok).toBe(true);
    expect(result.text).toContain(fileId);
    expect(result.attachedFiles?.[0].filename).toBe("รายงาน.docx");
    expect(result.attachedFiles?.[0].extraction_status).toBe("pending");
    expect(chat.uploadFileBytes).toHaveBeenCalledWith("conv", Buffer.from([0x50, 0x4b, 0, 0xff]), "รายงาน.docx", expect.stringContaining("wordprocessingml"), ctx.signal);
    expect(await fs.readFile(source)).toEqual(Buffer.from([0x50, 0x4b, 0, 0xff]));
  });

  it("saves bytes to a new file and never overwrites the original", async () => {
    const { tools, chat, ctx } = setup();
    const output = path.join(granted, "edited.pdf");
    const first = await tools.execute("save_conversation_file", { file_id: fileId, path: output }, ctx);
    expect(first.ok).toBe(true);
    expect(chat.listFiles).toHaveBeenCalledWith("conv");
    expect((await fs.readFile(output)).toString()).toBe("edited document bytes");
    chat.downloadFileBytes.mockResolvedValue({ bytes: Buffer.from("replacement"), mime: "application/pdf" });
    const second = await tools.execute("save_conversation_file", { file_id: fileId, path: output }, ctx);
    expect(second.ok).toBe(false);
    expect((await fs.readFile(output)).toString()).toBe("edited document bytes");
  });

  it("refuses ungranted, traversal, relative and unsupported paths before upload", async () => {
    const { tools, chat, ctx } = setup();
    const outside = path.join(temp, "private.pdf");
    await fs.writeFile(outside, "private");
    for (const candidate of [outside, path.join(granted, "..", "private.pdf"), "private.pdf", path.join(granted, "payload.exe")]) {
      expect((await tools.execute("import_local_file", { path: candidate }, ctx)).ok).toBe(false);
    }
    expect(chat.uploadFileBytes).not.toHaveBeenCalled();
  });

  it("refuses junctions even when the lexical path is inside a grant", async () => {
    const { tools, chat, ctx } = setup();
    await fs.writeFile(path.join(temp, "private.pdf"), "private");
    await fs.symlink(temp, path.join(granted, "link"), "junction");
    expect((await tools.execute("import_local_file", { path: path.join(granted, "link", "private.pdf") }, ctx)).ok).toBe(false);
    expect((await tools.execute("save_conversation_file", { file_id: fileId, path: path.join(granted, "link", "new.pdf") }, ctx)).ok).toBe(false);
    expect(chat.uploadFileBytes).not.toHaveBeenCalled();
    expect(chat.downloadFileBytes).not.toHaveBeenCalled();
  });

  it("does not expose application state inside a broadly granted parent folder", async () => {
    const { tools, chat, ctx } = setup();
    const protectedFolder = path.join(granted, "app-state");
    await fs.mkdir(protectedFolder);
    const source = path.join(protectedFolder, "settings.json");
    await fs.writeFile(source, "private");
    const restricted = { ...ctx, forbiddenFolders: [protectedFolder] };
    expect((await tools.execute("import_local_file", { path: source }, restricted)).ok).toBe(false);
    expect((await tools.execute("save_conversation_file", { file_id: fileId, path: path.join(protectedFolder, "new.json") }, restricted)).ok).toBe(false);
    expect(chat.uploadFileBytes).not.toHaveBeenCalled();
    expect(chat.downloadFileBytes).not.toHaveBeenCalled();
  });

  it("refuses cross-conversation downloads and failed ownership lookup", async () => {
    const { tools, chat, ctx } = setup();
    chat.listFiles.mockResolvedValue([]);
    const output = path.join(granted, "foreign.pdf");
    expect((await tools.execute("save_conversation_file", { file_id: fileId, path: output }, ctx)).ok).toBe(false);
    expect(chat.downloadFileBytes).not.toHaveBeenCalled();
    await expect(fs.stat(output)).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("enforces the upload cap before reading or uploading", async () => {
    const { tools, chat, ctx } = setup();
    const source = path.join(granted, "large.pdf");
    const handle = await fs.open(source, "w");
    await handle.truncate(LOCAL_FILE_MAX_BYTES + 1);
    await handle.close();
    expect((await tools.execute("import_local_file", { path: source }, ctx)).ok).toBe(false);
    expect(chat.uploadFileBytes).not.toHaveBeenCalled();
  });

  it("does not claim a successful attachment when the upload fails", async () => {
    const { tools, chat, ctx } = setup();
    const source = path.join(granted, "source.pdf");
    await fs.writeFile(source, "bytes");
    chat.uploadFileBytes.mockResolvedValue(null);
    const result = await tools.execute("import_local_file", { path: source }, ctx);
    expect(result.ok).toBe(false);
    expect(result.attachedFiles).toBeUndefined();
  });

  it("honors cancellation before any transfer", async () => {
    const { tools, chat, ctx } = setup();
    const signal = AbortSignal.abort();
    expect((await tools.execute("import_local_file", { path: path.join(granted, "source.pdf") }, { ...ctx, signal })).ok).toBe(false);
    expect(chat.uploadFileBytes).not.toHaveBeenCalled();
  });

  it("asks for both operations in Auto mode and scopes consent to the folder", () => {
    for (const name of ["import_local_file", "save_conversation_file"]) {
      const meta = toolMeta(name);
      expect(meta.defaultPermission).toBe("Ask");
      expect(applyApprovalMode("NeedsConsent", meta, "auto")).toBe("NeedsConsent");
      expect(resolveScope(meta, { path: "C:\\Work\\report.docx" }, ["C:\\Work"], "C:\\Users\\User", null)).toBe("C:\\Work");
    }
    expect(classifyDanger("import_local_file", "FileSystem").reasonKey).toBe("danger.fileUpload");
  });
});

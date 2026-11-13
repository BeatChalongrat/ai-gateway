// FIX80 — desktop run_python delivers files into the chat. The host names
// its conversation on /code/run so chat-edge persists every artifact; the
// result then carries attachedFiles (id + display fields) and the tool
// text tells the model the files are ALREADY in the chat — the chain that
// used to end in "ผมส่งไฟล์เข้าแชทโดยตรงไม่ได้ครับ" and a base64 dump.
import { describe, expect, it } from "vitest";
import { HostTools } from "../src/main/host-tools";
import type { RunCodeResult } from "../src/main/chat-edge-client";

const noopLog = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] as string[] };

function toolsWith(result: RunCodeResult | null, seen: { conv?: string; files?: string[] }) {
  const chatEdge = {
    runCode: async (_code: string, _files: string[], _signal: AbortSignal, conversationId?: string) => {
      seen.conv = conversationId;
      seen.files = _files;
      return result;
    },
  };
  return new HostTools(
    {} as never,
    chatEdge as never,
    async () => ({ completed: true, answer: "", trace: [] }) as never,
    noopLog as never,
    () => false,
  );
}

const ctx = { conversationId: "conv-123", toolCallId: "call-1", signal: new AbortController().signal, memoryEnabled: true };

describe("FIX80 — run_python delivery", () => {
  it("passes attached and generated file IDs to the sandbox for editing", async () => {
    const fileId = "b602f006-8a1c-4a95-81bc-e541fa177ae0";
    const seen: { files?: string[] } = {};
    const tools = toolsWith({ exit: 0, stdout: "edited" }, seen);
    const result = await tools.execute("run_python", { code: "edit()", files: [fileId, ` ${fileId} `] }, ctx);
    expect(result.ok).toBe(true);
    expect(seen.files).toEqual([fileId]);
  });

  it.each(["report.xlsx", ["C:\\private\\report.xlsx"], [12]])("rejects invalid file references %j before executing code", async (files) => {
    const seen: { files?: string[] } = {};
    const result = await toolsWith({ exit: 0 }, seen).execute("run_python", { code: "edit()", files }, ctx);
    expect(result.ok).toBe(false);
    expect(seen.files).toBeUndefined();
  });

  it.each([{ exit: 1, stderr: "Invalid workbook" }, { exit: 0, timed_out: true }])("reports failed execution to the tool loop %j", async (execution) => {
    const result = await toolsWith(execution, {}).execute("run_python", { code: "edit()" }, ctx);
    expect(result.ok).toBe(false);
  });

  it("names the conversation on /code/run and returns attachedFiles for persisted artifacts", async () => {
    const seen: { conv?: string } = {};
    const tools = toolsWith(
      {
        exit: 0,
        stdout: "saved",
        images: [{ id: "img-1", filename: "chart.png", data_uri: "data:image/png;base64,QUJDRA==" }],
        files: [{ id: "file-1", filename: "report.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size_bytes: 1234 }],
      },
      seen,
    );
    const r = await tools.execute("run_python", { code: "x" }, ctx);
    expect(seen.conv).toBe("conv-123");
    expect(r.ok).toBe(true);
    expect(r.attachedFiles).toEqual([
      { id: "img-1", filename: "chart.png", content_type: "image/png", file_size_bytes: 6 },
      { id: "file-1", filename: "report.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", file_size_bytes: 1234 },
    ]);
    // the model is TOLD the files are already in the chat — the honest
    // sentence that replaces the old "I can't put files in the chat"
    expect(r.text).toContain("Attached to this chat");
    expect(r.text).toContain("report.xlsx");
    expect(r.text).toContain('"file_id":"file-1"');
    expect(r.imageDataUri).toContain("data:image/png");
  });

  it("without persisted ids (old server) the result degrades: names the files, attaches nothing", async () => {
    const tools = toolsWith(
      { exit: 0, stdout: "ok", images: [{ filename: "a.png", data_uri: "data:image/png;base64,QQ==" }], files: [{ filename: "b.csv", content_type: "text/csv", size_bytes: 9 }] },
      {},
    );
    const r = await tools.execute("run_python", { code: "x" }, ctx);
    expect(r.attachedFiles).toBeUndefined();
    expect(r.text).toContain("Generated: a.png, b.csv");
  });

  it("run_python's description names /job/out/ as THE delivery path and warns off local files and base64", () => {
    const def = new HostTools({} as never, {} as never, (() => {}) as never, noopLog as never, () => false)
      .definitions()
      .find((d) => d.name === "run_python")!;
    expect(def.description).toContain("/job/out/");
    expect(def.description).toContain("attached to the chat");
    expect(def.description).toContain("run_command or write_file stay on the user's computer");
    expect(def.description).toMatch(/base64/);
    expect(def.description).toContain("/job/in/");
    expect(def.inputSchema).toMatchObject({ properties: { files: { type: "array" } } });
  });
});

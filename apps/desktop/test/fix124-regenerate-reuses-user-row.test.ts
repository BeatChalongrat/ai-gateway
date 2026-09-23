// FIX124 R4 — Regenerate / Retry answers the SAME question; it does not ask
// it again.
//
// The hosted page sends Regenerate, and Retry of a failed turn, as a submit
// with EMPTY text and no message id. The host recovered the last user
// message's text from its history (right: that is what the model must be
// sent) and then imported that text as a user row under a client id minted
// for the new turn. chat-edge dedupes imports on (conversation, client_id), so
// a new client id was a SECOND user row: the transcript read U, A1, U', A2 on
// the web, on the other device and after a reload.
//
// Whole turns are driven through onTurnSubmit against a scripted gateway and a
// fake chat-edge that keeps what /messages/import receives and serves it back
// from /messages (the FIX121 harness, with a transcript):
//   (a) a normal turn imports its user row and its assistant row;
//   (b) a submit with text "" imports ONE item — the new assistant row, naming
//       the assistant row it replaces — and no user row;
//   (c) a regenerate whose stream fails before any text still leaves its
//       (empty) errored assistant row (FIX121), and still no user row;
//   (d) the local chain file holds the question once.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { ImportMessageItem, ImportOutcome, MessageDto } from "../src/main/chat-edge-client";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };
type Script = (signal: AbortSignal) => AsyncGenerator<Ev>;

function host(scripts: Script[], opts: { seed?: MessageDto[]; importAnswer?: (call: number, items: ImportMessageItem[]) => ImportOutcome } = {}) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix124-"));
  const imports: ImportMessageItem[][] = [];
  const posted: { event: string; data: Record<string, unknown> }[] = [];
  // The server's transcript: what the imports inserted, deduped on client_id
  // the way the endpoint does.
  const rows: MessageDto[] = [...(opts.seed ?? [])];
  let streams = 0;
  const chatEdge = {
    async getConversation(id: string) { return { id, message_count: rows.length, memory_enabled: false }; },
    async listMessagesPage() { return { messages: rows, hasMore: false }; },
    async listMessages() { return rows; },
    async getConversationState() { return null; },
    async listProjects() { return []; },
    async listGlobalMemories() { return []; },
    async listMemories() { return []; },
    async importMessages(_id: string, items: ImportMessageItem[]) {
      imports.push(items);
      const answer = opts.importAnswer?.(imports.length, items);
      if (answer !== undefined) return answer;
      let imported = 0;
      for (const it of items) {
        if (rows.some((r) => r.client_id === it.client_id)) continue;
        rows.push({ id: `srv_${rows.length + 1}`, role: it.role, content: it.content, status: it.status ?? "completed", client_id: it.client_id });
        imported++;
      }
      return { imported, skipped: items.length - imported };
    },
    async updateConversation() { return null; },
  };
  const gw = { listModels: async () => null, streamCompletion: (_req: unknown, signal: AbortSignal) => scripts[Math.min(streams++, scripts.length - 1)]!(signal) };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
  const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), quiet, () => false);
  const bridge = {
    postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }),
    post: () => {},
    postHostResult: () => {}, postHostError: () => {},
  };
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", forceModel: "qwen3.8-27b-fp8", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    quiet,
  );
  const run = async (text: string) => {
    const from = posted.length;
    co.onTurnSubmit({ conversationId: "c1", text, attachmentHandles: [], modelId: "qwen3.8-27b-fp8", imageMode: false, editMessageId: null, quick: false } as never);
    await co.waitForTurn("c1");
    return posted.slice(from).filter((p) => p.event === "message_complete" || p.event === "error" || p.event === "cancelled").pop();
  };
  const chain = () => (JSON.parse(fs.readFileSync(path.join(tmp, "history", "c1.json"), "utf8")) as { messages: { role: string; content: unknown }[] }).messages;
  return { run, imports, rows, chain };
}

const says = (text: string): Script =>
  async function* (): AsyncGenerator<Ev> {
    yield { kind: "content_delta", delta: text };
    yield { kind: "done", finish_reason: "stop" };
  };
const failsSilently: Script = async function* (): AsyncGenerator<Ev> {
  yield { kind: "error", code: "provider_timeout", message: "no first token", cause: "timeout", retryable: true };
};

describe("FIX124 R4 — a regenerate reuses the user row", () => {
  it("(a) a normal turn imports one user item and one assistant item", async () => {
    const h = host([says("first answer")]);
    await h.run("the question");
    expect(h.imports).toHaveLength(1);
    expect(h.imports[0].map((i) => i.role)).toEqual(["user", "assistant"]);
    expect(h.imports[0][0]).toMatchObject({ content: "the question" });
    expect(h.imports[0][1]).not.toHaveProperty("replaces_client_id");
  });

  it("(b) a submit with empty text imports ONE assistant item that names the row it replaces — and no user item", async () => {
    const h = host([says("first answer"), says("second answer")]);
    await h.run("the question");
    const firstAssistant = h.imports[0].find((i) => i.role === "assistant")!;
    const terminal = await h.run("");

    expect(h.imports).toHaveLength(2);
    expect(h.imports[1]).toHaveLength(1);
    const [item] = h.imports[1];
    expect(item).toMatchObject({ role: "assistant", content: "second answer", status: "completed", replaces_client_id: firstAssistant.client_id });
    expect(item.client_id).not.toBe(firstAssistant.client_id);
    expect(h.imports.flat().filter((i) => i.role === "user")).toHaveLength(1);
    // The server transcript: one question, two answers.
    expect(h.rows.map((r) => r.role)).toEqual(["user", "assistant", "assistant"]);
    // The page is handed the EXISTING user row's id, and the new assistant's.
    expect(terminal?.event).toBe("message_complete");
    expect(terminal?.data).toMatchObject({ user_message_id: "srv_1", assistant_message_id: "srv_3" });
  });

  it("(b) a second regenerate replaces the answer the first one wrote", async () => {
    const h = host([says("one"), says("two"), says("three")]);
    await h.run("the question");
    await h.run("");
    await h.run("");
    expect(h.imports[2]).toHaveLength(1);
    expect(h.imports[2][0]).toMatchObject({ role: "assistant", content: "three", replaces_client_id: h.imports[1][0].client_id });
    expect(h.imports.flat().filter((i) => i.role === "user")).toHaveLength(1);
  });

  it("(b) an answer that came from the web has no client id: the field is omitted, the user row still is not repeated", async () => {
    const seed: MessageDto[] = [
      { id: "web_u", role: "user", content: "asked on the web", status: "completed", client_id: null },
      { id: "web_a", role: "assistant", content: "answered on the web", status: "completed", client_id: null },
    ];
    const h = host([says("answered here")], { seed });
    const terminal = await h.run("");
    expect(h.imports).toHaveLength(1);
    expect(h.imports[0]).toHaveLength(1);
    expect(h.imports[0][0]).toMatchObject({ role: "assistant", content: "answered here" });
    expect(h.imports[0][0]).not.toHaveProperty("replaces_client_id");
    expect(terminal?.data).toMatchObject({ user_message_id: "web_u" });
  });

  it("(b) a first turn whose import failed is resent as it was, with the regenerate's one row after it", async () => {
    const h = host([says("first answer"), says("second answer")], { importAnswer: (call) => (call === 1 ? { failed: true, status: 502 } : undefined) as ImportOutcome });
    await h.run("the question");
    await h.run("");
    expect(h.imports[1].map((i) => i.role)).toEqual(["user", "assistant", "assistant"]);
    expect(h.imports[1].slice(0, 2)).toEqual(h.imports[0]);
    // The transcript never had the first answer, but the host wrote it.
    expect(h.imports[1][2]).toMatchObject({ content: "second answer", replaces_client_id: h.imports[0][1].client_id });
  });

  it("(c) a regenerate that fails with no text still leaves its errored assistant row, and no user row", async () => {
    const h = host([says("first answer"), failsSilently]);
    await h.run("the question");
    const firstAssistant = h.imports[0].find((i) => i.role === "assistant")!;
    const terminal = await h.run("");

    expect(terminal?.event).toBe("error");
    expect(h.imports).toHaveLength(2);
    expect(h.imports[1]).toHaveLength(1);
    expect(h.imports[1][0]).toMatchObject({ role: "assistant", content: "", status: "error", terminal_cause: "timeout", error_code: "provider_timeout", replaces_client_id: firstAssistant.client_id });
    expect(typeof h.imports[1][0].run_id).toBe("string");
    expect(h.imports.flat().filter((i) => i.role === "user")).toHaveLength(1);
  });

  it("(c) Retry of a turn that failed with no text: one new assistant row, replacing the failed one", async () => {
    const h = host([failsSilently, says("now it works")]);
    await h.run("the question");
    const failed = h.imports[0].find((i) => i.role === "assistant")!;
    expect(failed).toMatchObject({ status: "error", content: "" });
    await h.run("");
    expect(h.imports[1]).toHaveLength(1);
    expect(h.imports[1][0]).toMatchObject({ role: "assistant", content: "now it works", status: "completed", replaces_client_id: failed.client_id });
    expect(h.imports.flat().filter((i) => i.role === "user")).toHaveLength(1);
  });

  it("(d) the local chain holds the question once, followed by the new answer only", async () => {
    const h = host([says("first answer"), says("second answer")]);
    await h.run("the question");
    expect(h.chain().map((m) => [m.role, m.content])).toEqual([["user", "the question"], ["assistant", "first answer"]]);
    await h.run("");
    expect(h.chain().map((m) => [m.role, m.content])).toEqual([["user", "the question"], ["assistant", "second answer"]]);
  });
});

// FIX118 Part C (desktop half) — the imported assistant row says how the
// turn ended.
//
// A whole turn is driven through onTurnSubmit against a scripted gateway
// and a fake chat-edge that records what /messages/import receives:
//   cut stream  → status error, terminal_cause eof_without_terminal,
//                 error_code stream_interrupted; the page's terminal frame
//                 carries the same code and cause
//   Stop        → status cancelled, terminal_cause stopped_by_user
//   normal      → the pre-FIX118 item shape (no status field)
//   gateway 429 → status error, cause error, the gateway's own code and
//                 its retryable verdict forwarded on the frame
// Before FIX118 all four imported the same plain row, which the server
// stored as `completed`.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { GatewayMessage } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { ImportMessageItem } from "../src/main/chat-edge-client";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };

function host(script: (signal: AbortSignal) => AsyncGenerator<Ev>) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix118-"));
  const imports: ImportMessageItem[][] = [];
  const posted: { event: string; data: Record<string, unknown> }[] = [];
  const chatEdge = {
    async getConversation(id: string) { return { id, message_count: 0, memory_enabled: false }; },
    async listMessagesPage() { return { messages: [], hasMore: false }; },
    async listMessages() { return []; },
    async getConversationState() { return null; },
    async listProjects() { return []; },
    async listGlobalMemories() { return []; },
    async listMemories() { return []; },
    async importMessages(_id: string, items: ImportMessageItem[]) { imports.push(items); return { imported: items.length, skipped: 0 }; },
    async updateConversation() { return null; },
  };
  const gw = { listModels: async () => null, streamCompletion: (_req: unknown, signal: AbortSignal) => script(signal) };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
  const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), quiet, () => false);
  const bridge = { postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }), post: () => {}, postHostResult: () => {}, postHostError: () => {} };
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", forceModel: "qwen3.8-27b-fp8", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    quiet,
  );
  const run = async (text = "hello") => {
    co.onTurnSubmit({ conversationId: "c1", text, attachmentHandles: [], modelId: "qwen3.8-27b-fp8", imageMode: false, editMessageId: null, quick: false } as never);
    await co.waitForTurn("c1");
    const terminal = posted.filter((p) => p.event === "message_complete" || p.event === "error" || p.event === "cancelled").pop();
    const assistant = imports.flat().find((i) => i.role === "assistant");
    return { terminal, assistant, imports, posted };
  };
  return { co, run };
}

async function* cutStream(): AsyncGenerator<Ev> {
  yield { kind: "content_delta", delta: "half an " };
  yield { kind: "content_delta", delta: "answer" };
  // What gateway-client now yields for a socket that closed with nothing said.
  yield { kind: "error", code: "stream_interrupted", message: "The response was interrupted. Please try again.", retryable: true, cause: "eof_without_terminal" };
}
async function* normalStream(): AsyncGenerator<Ev> {
  yield { kind: "content_delta", delta: "fine" };
  yield { kind: "done", finish_reason: "stop" };
}
async function* rateLimited(): AsyncGenerator<Ev> {
  yield { kind: "error", code: "provider_rate_limited", message: "slow down", retryable: true, details: { status: 429 } };
}

describe("FIX118 — the desktop imports the ending with the row", () => {
  it("a cut stream: the row is error / eof_without_terminal / stream_interrupted, and the frame says the same", async () => {
    const h = host(cutStream);
    const r = await h.run();
    expect(r.terminal?.event).toBe("error");
    expect(r.terminal?.data).toMatchObject({ code: "stream_interrupted", cause: "eof_without_terminal", retryable: true });
    expect(r.assistant).toMatchObject({ role: "assistant", content: "half an answer", status: "error", terminal_cause: "eof_without_terminal", error_code: "stream_interrupted" });
  });

  it("Stop: the row is cancelled / stopped_by_user; the frame is `cancelled` with the cause", async () => {
    let release!: () => void;
    const gate = new Promise<void>((res) => { release = res; });
    const h = host(async function* (signal: AbortSignal): AsyncGenerator<Ev> {
      yield { kind: "content_delta", delta: "stopped he" };
      await gate;
      if (signal.aborted) return;
      yield { kind: "done", finish_reason: "stop" };
    });
    const p = h.run();
    // Wait for the first delta to land, then press Stop.
    await new Promise((r) => setTimeout(r, 30));
    h.co.onTurnCancel({ conversationId: "c1" } as never);
    release();
    const r = await p;
    expect(r.terminal?.event).toBe("cancelled");
    expect(r.terminal?.data).toMatchObject({ partial: true, cause: "stopped_by_user" });
    expect(r.assistant).toMatchObject({ status: "cancelled", terminal_cause: "stopped_by_user" });
    expect(r.assistant).not.toHaveProperty("error_code");
  });

  it("a normal turn says status completed explicitly (FIX119: the server keeps NULL for an item that says nothing)", async () => {
    const r = await host(normalStream).run();
    expect(r.terminal?.event).toBe("message_complete");
    expect(r.assistant).toMatchObject({ role: "assistant", content: "fine", status: "completed" });
    expect(r.assistant).not.toHaveProperty("terminal_cause");
    expect(r.assistant).not.toHaveProperty("error_code");
  });

  it("a gateway error frame with no text still leaves an errored row carrying the gateway's code, and the frame keeps the verdict", async () => {
    const r = await host(rateLimited).run();
    expect(r.terminal?.event).toBe("error");
    expect(r.terminal?.data).toMatchObject({ code: "provider_rate_limited", cause: "error", retryable: true, details: { status: 429 } });
    expect(r.assistant).toMatchObject({ role: "assistant", content: "", status: "error", terminal_cause: "error", error_code: "provider_rate_limited" });
  });
});

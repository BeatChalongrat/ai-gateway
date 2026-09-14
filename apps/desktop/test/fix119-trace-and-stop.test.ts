// FIX119 — the desktop host: one id per turn, and why the model stopped.
//
//   - the gateway request carries X-Request-Id = the run id; the gateway's
//     own request id from the response header is logged beside it
//   - a `done` with stop_reason max_tokens reaches message_complete and the
//     imported row; a completed turn now says status: completed explicitly
//     (the server keeps NULL for an item that says nothing)
//   - an error frame's request_id rides details.gateway_request_id
//   - the stop-reason table is the contract's openai row
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { GatewayMessage } from "../src/main/gateway-client";
import { GatewayClient, parseChunk } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { ImportMessageItem } from "../src/main/chat-edge-client";
import { STOP_REASONS, STOP_REASON_FROM_FINISH, stopReasonFor } from "../src/main/terminal-cause";

const REPO = path.join(__dirname, "..", "..", "..");
const contract = JSON.parse(fs.readFileSync(path.join(REPO, "docs", "contracts", "terminal_cause.json"), "utf8")) as { stop_reasons: string[]; stop_reason_map: { openai: Record<string, string> } };

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };

describe("FIX119 — the stop-reason table", () => {
  it("is the contract's set and its openai row", () => {
    expect([...STOP_REASONS]).toEqual(contract.stop_reasons);
    expect({ ...STOP_REASON_FROM_FINISH }).toEqual(contract.stop_reason_map.openai);
  });
  it("prefers the gateway's stop_reason, falls back to finish_reason, and says nothing for nothing", () => {
    expect(stopReasonFor("max_tokens", "stop")).toBe("max_tokens");
    expect(stopReasonFor(undefined, "length")).toBe("max_tokens");
    expect(stopReasonFor(undefined, "weird")).toBe("unknown");
    expect(stopReasonFor(undefined, null)).toBe("");
    expect(stopReasonFor("length", null)).toBe("");
  });
});

describe("FIX119 — parseChunk", () => {
  it("reads stop_reason and the ids from the final chunk", () => {
    const evs = parseChunk('{"id":"req_abc","provider_request_id":"prov-1","choices":[{"index":0,"delta":{},"finish_reason":"length","stop_reason":"max_tokens"}]}');
    expect(evs).toEqual([{ kind: "done", finish_reason: "length", stop_reason: "max_tokens", provider_request_id: "prov-1", gateway_request_id: "req_abc" }]);
  });
  it("puts an error frame's request_id under details.gateway_request_id", () => {
    const evs = parseChunk('{"error":{"code":"provider_timeout","message":"slow","retryable":true,"request_id":"req_fail"}}');
    expect(evs[0]).toMatchObject({ kind: "error", code: "provider_timeout", details: { gateway_request_id: "req_fail" } });
  });
});

describe("FIX119 — the gateway request carries the run id", () => {
  it("sends X-Request-Id and logs the backend's own id from the response", async () => {
    lines.length = 0;
    let sentHeaders: Record<string, string> = {};
    const orig = globalThis.fetch;
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      sentHeaders = init.headers as Record<string, string>;
      return new Response('data: {"id":"req_9","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: [DONE]\n\n', {
        status: 200,
        headers: { "Content-Type": "text/event-stream", "X-Request-Id": "5f3c1a2b-0000-4000-8000-0123456789ab, req_9", "X-Gateway-Provider": "sranai" },
      });
    }) as typeof fetch;
    try {
      const gw = new GatewayClient("http://gw.test", () => "t", { load: () => "k", save: () => {}, clear: () => {} }, log);
      const out: Ev[] = [];
      for await (const ev of gw.streamCompletion({ model: "m", messages: [{ role: "user", content: "hi" }] }, new AbortController().signal, { requestId: "5f3c1a2b-0000-4000-8000-0123456789ab" })) out.push(ev);
      expect(sentHeaders["X-Request-Id"]).toBe("5f3c1a2b-0000-4000-8000-0123456789ab");
      expect(out.map((e) => e.kind)).toEqual(["content_delta", "done"]);
      expect(lines.find((l) => l.includes("GATEWAY stream opened"))).toContain("run=5f3c1a2b-0000-4000-8000-0123456789ab");
      expect(lines.find((l) => l.includes("GATEWAY stream opened"))).toContain("gateway_request_id=req_9");
    } finally {
      globalThis.fetch = orig;
    }
  });
});

function host(script: () => AsyncGenerator<Ev>) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix119-"));
  const imports: { items: ImportMessageItem[]; runId?: string }[] = [];
  const requestIds: (string | undefined)[] = [];
  const posted: { event: string; data: Record<string, unknown> }[] = [];
  const chatEdge = {
    async getConversation(id: string) { return { id, message_count: 0, memory_enabled: false }; },
    async listMessagesPage() { return { messages: [], hasMore: false }; },
    async listMessages() { return []; },
    async getConversationState() { return null; },
    async listProjects() { return []; },
    async listGlobalMemories() { return []; },
    async listMemories() { return []; },
    async importMessages(_id: string, items: ImportMessageItem[], runId?: string) { imports.push({ items, runId }); return { imported: items.length, skipped: 0 }; },
    async updateConversation() { return null; },
  };
  const gw = { listModels: async () => null, streamCompletion: (_req: unknown, _signal: AbortSignal, opts?: { requestId?: string }) => { requestIds.push(opts?.requestId); return script(); } };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
  const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }), temporaryChatReadsMemory: true };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), log, () => false);
  const bridge = { postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }), post: () => {}, postHostResult: () => {}, postHostError: () => {} };
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", forceModel: "qwen3.8-27b-fp8", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    log,
  );
  return {
    co,
    run: async () => {
      lines.length = 0;
      co.onTurnSubmit({ conversationId: "c1", text: "hello", attachmentHandles: [], modelId: "qwen3.8-27b-fp8", imageMode: false, editMessageId: null, quick: false } as never);
      await co.waitForTurn("c1");
      const terminal = posted.filter((p) => p.event === "message_complete" || p.event === "error" || p.event === "cancelled").pop();
      const assistant = imports.flatMap((i) => i.items).find((i) => i.role === "assistant");
      return { terminal, assistant, imports, requestIds, posted };
    },
  };
}

describe("FIX119 — a desktop turn", () => {
  it("cut at max_tokens: message_complete and the imported row both say max_tokens; the run id is sent to the gateway and the import", async () => {
    const r = await host(async function* () {
      yield { kind: "content_delta", delta: "cut off he" };
      yield { kind: "done", finish_reason: "length", stop_reason: "max_tokens", provider_request_id: "prov-1", gateway_request_id: "req_1" };
    }).run();
    expect(r.terminal?.event).toBe("message_complete");
    expect(r.terminal?.data).toMatchObject({ stop_reason: "max_tokens" });
    const runId = r.requestIds[0];
    expect(runId).toMatch(/^[0-9a-f-]{36}$/);
    expect(r.assistant).toMatchObject({ role: "assistant", content: "cut off he", status: "completed", stop_reason: "max_tokens", run_id: runId });
    expect(r.imports[0].runId).toBe(runId);
    expect(lines.some((l) => l.includes("TURN OUTPUT LIMIT") && l.includes(`run=${runId}`))).toBe(true);
    expect(lines.some((l) => l.includes("TURN DONE") && l.includes(`run=${runId}`) && l.includes("stop_reason=max_tokens"))).toBe(true);
  });

  it("a clean finish says status completed with no stop-reason news beyond end_turn", async () => {
    const r = await host(async function* () {
      yield { kind: "content_delta", delta: "fine" };
      yield { kind: "done", finish_reason: "stop", stop_reason: "end_turn" };
    }).run();
    expect(r.assistant).toMatchObject({ status: "completed", stop_reason: "end_turn" });
    expect(r.assistant).toHaveProperty("run_id");
  });

  it("an older gateway (finish_reason only) still yields a closed-set stop reason", async () => {
    const r = await host(async function* () {
      yield { kind: "content_delta", delta: "x" };
      yield { kind: "done", finish_reason: "length" };
    }).run();
    expect(r.terminal?.data).toMatchObject({ stop_reason: "max_tokens" });
  });

  it("a failed turn's frame carries the backend's request id under details", async () => {
    const r = await host(async function* () {
      yield { kind: "error", code: "provider_timeout", message: "slow", retryable: true, details: { gateway_request_id: "req_fail" } };
    }).run();
    expect(r.terminal?.event).toBe("error");
    expect(r.terminal?.data).toMatchObject({ code: "provider_timeout", details: { gateway_request_id: "req_fail" } });
    expect(r.assistant).toMatchObject({ status: "error", error_code: "provider_timeout" });
    expect(r.assistant).toHaveProperty("run_id");
    expect(lines.some((l) => l.includes("TURN ERROR") && l.includes("gateway_request_id=req_fail"))).toBe(true);
  });
});

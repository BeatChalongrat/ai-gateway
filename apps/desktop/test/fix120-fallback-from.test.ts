// FIX120 — a desktop turn the gateway rescued with another model is
// attributed to the model that answered, and the requested model rides
// message_complete and the imported row (fallback_from) — so the page shows
// "answered by X" now and after a reload. A normal turn carries nothing.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { ImportMessageItem } from "../src/main/chat-edge-client";

const REPO = path.join(__dirname, "..", "..", "..");
const policy = JSON.parse(fs.readFileSync(path.join(REPO, "docs", "contracts", "fallback_policy.json"), "utf8")) as {
  decision_table: { terminal_cause: Record<string, string> };
  hard_forbidden: { terminal_cause: string[] };
};

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };

function host(script: () => AsyncGenerator<Ev>) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix120-"));
  const imports: { items: ImportMessageItem[]; runId?: string }[] = [];
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
  const gw = { listModels: async () => null, streamCompletion: () => script() };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
  const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), log, () => false);
  const bridge = { postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }), post: () => {}, postHostResult: () => {}, postHostError: () => {} };
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "gpt-5.6-luna", forceModel: "gpt-5.6-luna", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    log,
  );
  return async () => {
    lines.length = 0;
    co.onTurnSubmit({ conversationId: "c1", text: "hello", attachmentHandles: [], modelId: "gpt-5.6-luna", imageMode: false, editMessageId: null, quick: false } as never);
    await co.waitForTurn("c1");
    const terminal = posted.filter((p) => p.event === "message_complete" || p.event === "error" || p.event === "cancelled").pop();
    const fallback = posted.find((p) => p.event === "fallback_info");
    const assistant = imports.flatMap((i) => i.items).find((i) => i.role === "assistant");
    return { terminal, fallback, assistant, posted };
  };
}

describe("FIX120 — the policy file, as the desktop reads it", () => {
  it("never asks the desktop to re-run a turn: every terminal cause but error is give_up, stopped_by_user is hard-forbidden", () => {
    for (const [cause, action] of Object.entries(policy.decision_table.terminal_cause)) {
      expect(action, cause).toBe(cause === "error" ? "by_error_code" : "give_up");
    }
    expect(policy.hard_forbidden.terminal_cause).toContain("stopped_by_user");
  });
});

describe("FIX120 — a rescued desktop turn", () => {
  it("is attributed to the answering model; message_complete and the import carry fallback_from", async () => {
    const r = await host(async function* () {
      yield { kind: "fallback_info", requested_model: "gpt-5.6-luna", actual_model: "qwen3.8-27b-fp8", reason: "Primary model unavailable (provider_unavailable)" };
      yield { kind: "content_delta", delta: "สวัสดี" };
      yield { kind: "done", finish_reason: "stop", stop_reason: "end_turn" };
    })();
    expect(r.fallback?.data).toMatchObject({ requested_model: "gpt-5.6-luna", actual_model: "qwen3.8-27b-fp8" });
    expect(r.terminal?.event).toBe("message_complete");
    expect(r.terminal?.data).toMatchObject({ model_id: "qwen3.8-27b-fp8", fallback_from: "gpt-5.6-luna", stop_reason: "end_turn" });
    expect(r.assistant).toMatchObject({ role: "assistant", content: "สวัสดี", status: "completed", model_id: "qwen3.8-27b-fp8", fallback_from: "gpt-5.6-luna" });
  });

  it("a normal turn is attributed to the requested model and carries no fallback_from", async () => {
    const r = await host(async function* () {
      yield { kind: "content_delta", delta: "ok" };
      yield { kind: "done", finish_reason: "stop", stop_reason: "end_turn" };
    })();
    expect(r.fallback).toBeUndefined();
    expect(r.terminal?.data).not.toHaveProperty("fallback_from");
    expect(r.assistant).toMatchObject({ model_id: "gpt-5.6-luna" });
    expect(r.assistant).not.toHaveProperty("fallback_from");
  });

  it("a frame naming the same model twice is not a swap", async () => {
    const r = await host(async function* () {
      yield { kind: "fallback_info", requested_model: "gpt-5.6-luna", actual_model: "gpt-5.6-luna", reason: null };
      yield { kind: "content_delta", delta: "ok" };
      yield { kind: "done", finish_reason: "stop", stop_reason: "end_turn" };
    })();
    expect(r.terminal?.data).not.toHaveProperty("fallback_from");
    expect(r.assistant).not.toHaveProperty("fallback_from");
  });
});

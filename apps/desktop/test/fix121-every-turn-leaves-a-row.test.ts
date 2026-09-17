// FIX121 Part C (desktop half) — every turn leaves a row, and a row that did
// not reach the server is SAID.
//
// Whole turns are driven through onTurnSubmit against a scripted gateway and
// a fake chat-edge that records what /messages/import receives (the FIX118
// harness):
//   C1  Stop before the first character → an assistant item, status
//       cancelled / stopped_by_user, empty content. Before: NO assistant item
//       at all, so the web showed a question with no answer and no label.
//   C4  the import is refused (or names rejected items) → a `host.notice`
//       `turn-not-saved` reaches the page; the rows are resent with the next
//       turn. Before: one log line, the person saw a normal answer.
//   C5  the gateway's per-model attempt log rides the terminal error frame
//       (`attempts`, as a web frame carries it) and the imported error row
//       (`error_attempts`). Before: parsed nowhere on the desktop.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { parseChunk } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { ImportMessageItem, ImportOutcome } from "../src/main/chat-edge-client";
import { normalizeHostError } from "../src/main/terminal-cause";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };

function host(script: (signal: AbortSignal) => AsyncGenerator<Ev>, importAnswer: (call: number, items: ImportMessageItem[]) => ImportOutcome = (_c, items) => ({ imported: items.length, skipped: 0 })) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix121-"));
  const imports: ImportMessageItem[][] = [];
  const posted: { event: string; data: Record<string, unknown> }[] = [];
  const notices: Record<string, unknown>[] = [];
  const chatEdge = {
    async getConversation(id: string) { return { id, message_count: 0, memory_enabled: false }; },
    async listMessagesPage() { return { messages: [], hasMore: false }; },
    async listMessages() { return []; },
    async getConversationState() { return null; },
    async listProjects() { return []; },
    async listGlobalMemories() { return []; },
    async listMemories() { return []; },
    async importMessages(_id: string, items: ImportMessageItem[]) { imports.push(items); return importAnswer(imports.length, items); },
    async updateConversation() { return null; },
  };
  const gw = { listModels: async () => null, streamCompletion: (_req: unknown, signal: AbortSignal) => script(signal) };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
  const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }), temporaryChatReadsMemory: true };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), quiet, () => false);
  const bridge = {
    postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }),
    post: (type: string, payload: unknown) => { if (type === "host.notice") notices.push(payload as Record<string, unknown>); },
    postHostResult: () => {}, postHostError: () => {},
  };
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", forceModel: "qwen3.8-27b-fp8", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    quiet,
  );
  const submit = (text = "hello") => co.onTurnSubmit({ conversationId: "c1", text, attachmentHandles: [], modelId: "qwen3.8-27b-fp8", imageMode: false, editMessageId: null, quick: false } as never);
  const run = async (text = "hello") => {
    submit(text);
    await co.waitForTurn("c1");
    const terminal = posted.filter((p) => p.event === "message_complete" || p.event === "error" || p.event === "cancelled").pop();
    return { terminal, imports, posted, notices };
  };
  return { co, run, submit, imports, notices };
}

async function* normalStream(): AsyncGenerator<Ev> {
  yield { kind: "content_delta", delta: "fine" };
  yield { kind: "done", finish_reason: "stop" };
}

const ATTEMPTS = [
  { provider: "sranai", model: "qwen3.8-27b-fp8", error_code: "provider_timeout", error_message: "no first token", latency_ms: 30000 },
  { provider: "openai", model: "gpt-5-mini", error_code: "provider_no_credit" },
];

describe("FIX121 C1 — Stop before the first character still leaves the answer's row", () => {
  it("imports user + an EMPTY assistant item marked cancelled / stopped_by_user, and the page gets `cancelled`", async () => {
    let release!: () => void;
    const gate = new Promise<void>((res) => { release = res; });
    const h = host(async function* (signal: AbortSignal): AsyncGenerator<Ev> {
      await gate; // the model has said nothing yet
      if (signal.aborted) return;
      yield { kind: "content_delta", delta: "too late" };
      yield { kind: "done", finish_reason: "stop" };
    });
    const p = h.run("a question");
    await new Promise((r) => setTimeout(r, 30));
    h.co.onTurnCancel({ conversationId: "c1" } as never);
    release();
    const r = await p;

    expect(r.terminal?.event).toBe("cancelled");
    expect(r.imports).toHaveLength(1);
    const [user, assistant] = r.imports[0];
    expect(user).toMatchObject({ role: "user", content: "a question" });
    expect(assistant).toMatchObject({ role: "assistant", content: "", status: "cancelled", terminal_cause: "stopped_by_user" });
    expect(typeof assistant.run_id).toBe("string");
    expect(assistant).not.toHaveProperty("error_code");
    expect(assistant).not.toHaveProperty("error_attempts");
    expect(r.notices).toEqual([]); // it WAS saved: nothing to tell
  });
});

describe("FIX121 C5 — the attempt log reaches the page and the row", () => {
  it("parseChunk keeps the gateway frame's `attempts` (it sits beside code/message, not inside details)", () => {
    const [ev] = parseChunk(JSON.stringify({ error: { code: "all_models_failed", message: "every candidate failed", attempts: ATTEMPTS, details: { a: 1 } } }));
    expect(ev).toMatchObject({ kind: "error", code: "all_models_failed", attempts: ATTEMPTS, details: { a: 1 } });
    const [bare] = parseChunk(JSON.stringify({ error: { code: "provider_timeout", message: "x", attempts: [] } }));
    expect(bare).not.toHaveProperty("attempts");
  });

  it("normalizeHostError keeps `attempts` when it rewrites a host code", () => {
    const { frame } = normalizeHostError({ code: "stream_interrupted", message: "m", attempts: ATTEMPTS });
    expect(frame.attempts).toEqual(ATTEMPTS);
  });

  it("an exhausted walk: the terminal frame carries `attempts`, the imported error row carries `error_attempts`", async () => {
    const r = await host(async function* (): AsyncGenerator<Ev> {
      yield { kind: "error", code: "all_models_failed", message: "every candidate failed", cause: "error", attempts: ATTEMPTS };
    }).run();
    expect(r.terminal?.event).toBe("error");
    expect(r.terminal?.data).toMatchObject({ code: "all_models_failed", attempts: ATTEMPTS });
    const assistant = r.imports[0].find((i) => i.role === "assistant");
    expect(assistant).toMatchObject({ status: "error", error_code: "all_models_failed", error_attempts: ATTEMPTS });
  });

  it("a turn that did not fail carries no attempt log", async () => {
    const r = await host(normalStream).run();
    expect(r.imports[0].find((i) => i.role === "assistant")).not.toHaveProperty("error_attempts");
  });
});

describe("FIX121 C4 — a turn that did not reach the server is said, and resent", () => {
  it("a refused import posts host.notice turn-not-saved with the status; the answer on screen is otherwise unchanged", async () => {
    const r = await host(normalStream, () => ({ failed: true, status: 503, detail: "upstream down" })).run();
    expect(r.terminal?.event).toBe("message_complete");
    expect(r.notices).toHaveLength(1);
    expect(r.notices[0]).toMatchObject({ kind: "turn-not-saved", conversationId: "c1", reason: "failed", status: 503 });
    expect(typeof r.notices[0].runId).toBe("string");
  });

  it("a fake that answers null (no session) is a failure too — never silence", async () => {
    const r = await host(normalStream, () => null).run();
    expect(r.notices).toHaveLength(1);
    expect(r.notices[0]).toMatchObject({ kind: "turn-not-saved", reason: "failed" });
  });

  it("the next turn resends the rows that were not delivered, ahead of its own, and then owes nothing", async () => {
    const h = host(normalStream, (call, items) => (call === 1 ? { failed: true, status: 502 } : { imported: items.length, skipped: 0 }));
    await h.run("first");
    await h.run("second");
    expect(h.imports).toHaveLength(2);
    expect(h.imports[1].map((i) => i.content)).toEqual(["first", "fine", "second", "fine"]);
    expect(h.imports[1].slice(0, 2)).toEqual(h.imports[0]); // same client_ids: the endpoint dedupes on them
    expect(h.notices).toHaveLength(1);
    await h.run("third");
    expect(h.imports[2].map((i) => i.content)).toEqual(["third", "fine"]);
  });

  it("items the server REJECTED are named to the page and are not resent", async () => {
    const h = host(normalStream, (call, items) => (call === 1 ? { imported: 1, skipped: 0, rejected: [{ index: 1, client_id: items[1].client_id, reason: "status must be completed, error or cancelled" }] } : { imported: items.length, skipped: 0 }));
    const r = await h.run("q");
    expect(r.notices).toHaveLength(1);
    expect(r.notices[0]).toMatchObject({ kind: "turn-not-saved", reason: "rejected" });
    expect(String(r.notices[0].detail)).toContain("status must be");
    await h.run("next");
    expect(h.imports[1].map((i) => i.content)).toEqual(["next", "fine"]);
  });

  it("a saved turn posts no notice", async () => {
    const r = await host(normalStream).run();
    expect(r.notices).toEqual([]);
  });
});

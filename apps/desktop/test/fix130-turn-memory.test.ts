// FIX130 — the desktop host's half of the memory card, driven through a whole
// turn (onTurnSubmit) against a scripted gateway and a fake chat-edge.
//
//   A2b  a temporary chat (is_temporary, whatever its switch says) gets no
//        memory-write tool and a `remember` it asks for anyway is refused;
//        the prompt assembler is told the chat is temporary.
//   A3d  when the prompt carried saved memories, the page gets `memory_used`
//        (the same frame chat-edge sends the web) before the answer, keyed to
//        the assistant bubble; none when nothing was injected.
//   A4b  `memory.changed` (the Memory page edited/deleted/accepted/rejected)
//        drops the host's server-memory cache.
//   D5   `remember` keeps its ACTIVE save only when the user's own message
//        asked to remember; otherwise the note becomes a pending suggestion
//        (source_type assistant_inferred, the chat named as its source), the
//        model is told so, and the page gets `memory_saved` with the status.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatEdgeClient } from "../src/main/chat-edge-client";
import type { GatewayMessage, GatewayToolDefinition } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import { HOST_QUERY_OPS } from "../src/shared/bridge-protocol";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };
const CONV = "5f0c7a52-3b1e-4c1a-9a55-0d6f3c2b9e11";

type CreateArgs = Parameters<ChatEdgeClient["createMemory"]>[0];

function host(opts: {
  conversation?: Record<string, unknown>;
  memoryUsed?: { id: string; label: string }[];
  /** The model calls `remember` with this note on its first round. */
  rememberNote?: string;
}) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix130-"));
  const posted: { event: string; data: Record<string, unknown> }[] = [];
  const results: { id: string; ok: boolean; data?: unknown; error?: string }[] = [];
  const created: CreateArgs[] = [];
  const toolsSent: string[][] = [];
  const assemblerInputs: Record<string, unknown>[] = [];
  let invalidations = 0;
  const chatEdge = {
    async getConversation(id: string) { return { id, message_count: 0, memory_enabled: true, ...(opts.conversation ?? {}) }; },
    async listMessagesPage() { return { messages: [], hasMore: false }; },
    async listMessages() { return []; },
    async getConversationState() { return null; },
    async listProjects() { return []; },
    async listGlobalMemories() { return []; },
    async importMessages(_id: string, items: unknown[]) { return { imported: items.length, skipped: 0 }; },
    async updateConversation() { return null; },
    async createMemory(a: CreateArgs) {
      created.push(a);
      return { ok: true as const, id: "mem-1", content: a.content, status: a.sourceType === "assistant_inferred" ? "pending_confirmation" : "active" };
    },
  };
  let round = 0;
  const gw = {
    listModels: async () => null,
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[] }): AsyncGenerator<Ev> {
      toolsSent.push((req.tools ?? []).map((t) => t.function.name));
      round++;
      if (round === 1 && opts.rememberNote) {
        yield { kind: "tool_call_delta", index: 0, id: "call_rem", name: "remember", argumentsDelta: JSON.stringify({ note: opts.rememberNote, scope: "global" }) };
        yield { kind: "done", finish_reason: "tool_calls" };
        return;
      }
      yield { kind: "content_delta", delta: "fine" };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
  const assembler = {
    buildSystemPromptParts: async (inputs: Record<string, unknown>) => {
      assemblerInputs.push(inputs);
      return { text: "system", parts: [], memoryUsed: opts.memoryUsed ?? [] };
    },
    invalidateServerMemories: () => {
      invalidations++;
    },
  };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), quiet, () => false);
  const bridge = {
    postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }),
    post: () => {},
    postHostResult: (id: string, ok: boolean, data?: unknown, error?: string) => results.push({ id, ok, data, error }),
    postHostError: () => {},
  };
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m1", forceModel: "m1", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    quiet,
  );
  const run = async (text: string) => {
    co.onTurnSubmit({ conversationId: CONV, text, attachmentHandles: [], modelId: "m1", imageMode: false, editMessageId: null, quick: false } as never);
    await co.waitForTurn(CONV);
  };
  return { co, run, posted, results, created, toolsSent, assemblerInputs, invalidations: () => invalidations };
}

const names = (posted: { event: string }[]) => posted.map((p) => p.event);

describe("FIX130 A3d — memory_used rides the turn when the prompt carried memories", () => {
  it("is sent once, before the answer, keyed to the assistant bubble, with the count and the items", async () => {
    const h = host({ memoryUsed: [{ id: "s1", label: "ชื่อเล่นคือบีท" }, { id: "local:0", label: "ทีม Platform" }] });
    await h.run("สวัสดี");
    const start = h.posted.find((p) => p.event === "message_start")!;
    const used = h.posted.filter((p) => p.event === "memory_used");
    expect(used).toHaveLength(1);
    expect(used[0]!.data).toEqual({
      message_id: start.data["message_id"],
      count: 2,
      items: [{ id: "s1", label: "ชื่อเล่นคือบีท" }, { id: "local:0", label: "ทีม Platform" }],
    });
    const order = names(h.posted);
    expect(order.indexOf("memory_used")).toBeLessThan(order.indexOf("content_delta"));
    expect(order.indexOf("memory_used")).toBeGreaterThan(order.indexOf("message_start"));
  });

  it("is not sent when nothing was injected", async () => {
    const h = host({ memoryUsed: [] });
    await h.run("สวัสดี");
    expect(names(h.posted)).not.toContain("memory_used");
  });
});

describe("FIX130 A2b — a temporary chat neither reads nor writes", () => {
  it("the assembler is told the chat is temporary, even with its switch on", async () => {
    const h = host({ conversation: { memory_enabled: true, is_temporary: true } });
    await h.run("สวัสดี");
    expect(h.assemblerInputs[0]).toMatchObject({ isTemporary: true });
  });

  it("offers no memory-write tool in a temporary chat, and offers it in an ordinary one", async () => {
    const temp = host({ conversation: { memory_enabled: true, is_temporary: true } });
    await temp.run("สวัสดี");
    expect(temp.toolsSent[0]).not.toContain("remember");
    const normal = host({});
    await normal.run("สวัสดี");
    expect(normal.toolsSent[0]).toContain("remember");
  });

  it("refuses a remember the model asks for anyway in a temporary chat — nothing reaches chat-edge", async () => {
    const h = host({ conversation: { memory_enabled: true, is_temporary: true }, rememberNote: "The user's name is Beat." });
    await h.run("จำไว้นะ ผมชื่อบีท");
    expect(h.created).toHaveLength(0);
    expect(names(h.posted)).not.toContain("memory_saved");
  });
});

describe("FIX130 D5 — remember is active only when the user asked", () => {
  it("a note the user did not ask for becomes a pending suggestion naming this chat", async () => {
    const said = "ผมชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธาอยู่ที่เชียงใหม่ ช่วยแนะนำร้านกาแฟหน่อย";
    const h = host({ rememberNote: "ชื่อเล่นคือ ภูผา ทำงานเป็นวิศวกรโยธา อยู่ที่เชียงใหม่" });
    await h.run(said);
    expect(h.created).toHaveLength(1);
    expect(h.created[0]).toMatchObject({ sourceType: "assistant_inferred", sourceConversationId: CONV });
    expect(h.created[0]!.evidenceExcerpt).toBe(said);
    const saved = h.posted.find((p) => p.event === "memory_saved");
    const start = h.posted.find((p) => p.event === "message_start")!;
    expect(saved?.data).toEqual({
      message_id: start.data["message_id"],
      items: [{ id: "mem-1", label: "ชื่อเล่นคือ ภูผา ทำงานเป็นวิศวกรโยธา อยู่ที่เชียงใหม่", status: "pending_confirmation" }],
    });
    expect(h.invalidations()).toBeGreaterThanOrEqual(1);
  });

  it("a note the user explicitly asked for is saved active, as before", async () => {
    const h = host({ rememberNote: "The user's name is Beat." });
    await h.run("จำไว้นะ ผมชื่อบีท");
    expect(h.created).toHaveLength(1);
    expect(h.created[0]!.sourceType).toBeUndefined();
    // Saved from a chat, so the Memory page links back to it (not "เพิ่มเอง").
    expect(h.created[0]!.sourceConversationId).toBe(CONV);
    const saved = h.posted.find((p) => p.event === "memory_saved");
    expect(saved?.data["items"]).toEqual([{ id: "mem-1", label: "The user's name is Beat.", status: "active" }]);
  });
});

describe("FIX130 D5 — the remember host tool, unit level", () => {
  function tools(createMemory: (a: CreateArgs) => Promise<unknown>) {
    const calls: CreateArgs[] = [];
    const chatEdge = {
      getConversation: async () => ({ project_id: null }),
      createMemory: async (a: CreateArgs) => {
        calls.push(a);
        return createMemory(a);
      },
    };
    const t = new HostTools({} as never, chatEdge as never, (async () => ({})) as never, quiet as never, () => false);
    return { t, calls };
  }
  const ctx = (userMessage?: string) => ({ conversationId: CONV, toolCallId: "tc1", signal: new AbortController().signal, userMessage });

  it("tells the model honestly that a suggestion is not active yet", async () => {
    const { t, calls } = tools(async () => ({ ok: true, id: "m9", content: "Prefers short answers", status: "pending_confirmation" }));
    const r = await t.execute("remember", { note: "Prefers short answers" }, ctx("ขอสรุปสั้นๆ หน่อย"));
    expect(r.ok).toBe(true);
    expect(calls[0]).toMatchObject({ sourceType: "assistant_inferred", sourceConversationId: CONV });
    expect(r.text).toMatch(/queued for the user's review/i);
    expect(r.text).toMatch(/not active yet/i);
    expect(r.memorySaved).toEqual({ id: "m9", label: "Prefers short answers", status: "pending_confirmation" });
  });

  it("with no user message to check (a path that did not pass one) it errs to the review queue", async () => {
    const { t, calls } = tools(async () => ({ ok: true, id: "m9", content: "x", status: "pending_confirmation" }));
    await t.execute("remember", { note: "x" }, ctx(undefined));
    expect(calls[0]!.sourceType).toBe("assistant_inferred");
  });

  it("keeps the evidence short", async () => {
    const { t, calls } = tools(async () => ({ ok: true, id: "m9", content: "x", status: "pending_confirmation" }));
    await t.execute("remember", { note: "x" }, ctx("ก".repeat(500)));
    expect(Array.from(calls[0]!.evidenceExcerpt ?? "").length).toBeLessThanOrEqual(201);
  });
});

describe("FIX130 D5 — createMemory sends the suggestion fields to chat-edge", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts source_type / source_conversation_id / evidence_excerpt and reads back the status", async () => {
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: { body: string }) => {
      bodies.push(JSON.parse(init.body) as Record<string, unknown>);
      return new Response(JSON.stringify({ id: "m1", content: "c", status: "pending_confirmation" }), { status: 201, headers: { "Content-Type": "application/json" } });
    });
    const c = new ChatEdgeClient("http://chat-edge.test", () => "tok", quiet);
    const r = await c.createMemory({ content: "c", scopeType: "global_user", sourceType: "assistant_inferred", sourceConversationId: CONV, evidenceExcerpt: "ev" });
    expect(bodies[0]).toMatchObject({ content: "c", source_type: "assistant_inferred", source_conversation_id: CONV, evidence_excerpt: "ev" });
    expect(r).toEqual({ ok: true, id: "m1", content: "c", status: "pending_confirmation" });
  });

  it("an explicit save sends none of them", async () => {
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: { body: string }) => {
      bodies.push(JSON.parse(init.body) as Record<string, unknown>);
      return new Response(JSON.stringify({ id: "m1", content: "c", status: "active" }), { status: 201, headers: { "Content-Type": "application/json" } });
    });
    const c = new ChatEdgeClient("http://chat-edge.test", () => "tok", quiet);
    await c.createMemory({ content: "c", scopeType: "global_user" });
    expect(bodies[0]).not.toHaveProperty("source_type");
    expect(bodies[0]).not.toHaveProperty("source_conversation_id");
    expect(bodies[0]).not.toHaveProperty("evidence_excerpt");
  });
});

describe("FIX130 A4b — memory.changed drops the host's memory cache", () => {
  it("is a page op in the closed set", () => {
    expect(HOST_QUERY_OPS).toContain("memory.changed");
  });

  it("invalidates the assembler's cache and answers ok", async () => {
    const h = host({});
    await h.co.onHostQuery({ requestId: "q1", op: "memory.changed", args: {} } as never);
    expect(h.invalidations()).toBe(1);
    expect(h.results[0]).toMatchObject({ id: "q1", ok: true });
  });
});

// FIX124 R2 (desktop half) — a network loss is not an "AI service" failure.
//
// When the host↔gateway stream dies the gateway cancels the generation, so
// there is nothing to resume and nothing is re-sent. What was wrong is what
// the person was TOLD: a silent drop (Wi-Fi off, a cable pulled — no RST, no
// FIN) waited out the 60 s idle watchdog and ended as `stream_stalled` /
// `timeout`, whose sentence on the page blames the model. The machine knew it
// was offline the whole time.
//
//   (a) a stall while the machine is offline is stream_interrupted /
//       disconnected, retryable — and it is said after a short silence, not
//       after the whole idle timeout;
//   (b) a stall while online is unchanged: stream_stalled / timeout at 60 s;
//   (c) a thrown read while offline is disconnected (never unclassified), and
//       says the machine was offline;
//   (d) the text received before the loss stays on the imported row, which
//       ends error / disconnected.
// No new terminal cause: the set is closed (docs/contracts/terminal_cause.json).
import { afterEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { GatewayClient } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { ImportMessageItem } from "../src/main/chat-edge-client";
import { classifyStreamFailure, isTerminalCause } from "../src/main/terminal-cause";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
const keyStore = { load: () => "k", save: () => {}, clear: () => {} };
const DELTA = (text: string) => `data: ${JSON.stringify({ choices: [{ delta: { content: text }, finish_reason: null }] })}\n\n`;

type Frame = { kind: string; code?: string; cause?: string; retryable?: boolean; message?: string; details?: Record<string, unknown>; delta?: string };

/** A 200 SSE response that sends `chunks` and then goes SILENT — the socket
 *  of a machine whose network vanished. Like a real fetch body, it errors
 *  only when the request's signal aborts. */
function silentAfter(chunks: string[]): typeof fetch {
  return (async (_url: unknown, init?: RequestInit) => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const chunk of chunks) c.enqueue(new TextEncoder().encode(chunk));
        init?.signal?.addEventListener("abort", () => c.error(new Error("This operation was aborted")), { once: true });
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }) as typeof fetch;
}

/** A 200 SSE response whose body tears after `chunks` (a reset the OS saw). */
function tearsAfter(chunks: string[], text = "terminated"): typeof fetch {
  return (async () => {
    let sent = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        if (sent < chunks.length) c.enqueue(new TextEncoder().encode(chunks[sent++]!));
        else c.error(new Error(text));
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }) as typeof fetch;
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  vi.useRealTimers();
});

function stream(gw: GatewayClient): { frames: Frame[]; finished: Promise<void> } {
  const frames: Frame[] = [];
  const finished = (async () => {
    for await (const ev of gw.streamCompletion({ model: "m", messages: [{ role: "user", content: "hi" }] }, new AbortController().signal)) frames.push(ev as Frame);
  })();
  return { frames, finished };
}

describe("FIX124 R2 — the idle watchdog asks whether the machine is online", () => {
  it("(a) a silent stream while OFFLINE ends stream_interrupted / disconnected, retryable — well before the 60 s idle timeout", async () => {
    vi.useFakeTimers();
    globalThis.fetch = silentAfter([DELTA("half an ")]);
    let online = true;
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => online });
    const s = stream(gw);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(s.frames.map((f) => f.kind)).toEqual(["content_delta"]);
    online = false; // the network goes; the socket says nothing
    await vi.advanceTimersByTimeAsync(20_000);
    await s.finished;

    const last = s.frames[s.frames.length - 1]!;
    expect(last).toMatchObject({ kind: "error", code: "stream_interrupted", cause: "disconnected", retryable: true, details: { offline: true } });
    expect(last.code).not.toBe("stream_stalled");
    expect(last.code).not.toBe("unclassified_error");
    expect(last.message).not.toMatch(/stalled|model/i);
    expect(isTerminalCause(last.cause)).toBe(true);
  });

  it("(a) offline for a moment is not a loss: a stream that keeps delivering is left alone", async () => {
    vi.useFakeTimers();
    let push!: (s: string) => void;
    let close!: () => void;
    globalThis.fetch = (async () =>
      new Response(new ReadableStream<Uint8Array>({ start(c) { push = (s) => c.enqueue(new TextEncoder().encode(s)); close = () => c.close(); } }), { status: 200 })) as typeof fetch;
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => false });
    const s = stream(gw);
    for (let i = 0; i < 10; i++) {
      await vi.advanceTimersByTimeAsync(4_000);
      push(DELTA(`w${i} `));
    }
    push("data: [DONE]\n\n");
    close();
    await vi.advanceTimersByTimeAsync(10);
    await s.finished;
    expect(s.frames.filter((f) => f.kind === "content_delta")).toHaveLength(10);
    expect(s.frames[s.frames.length - 1]!.kind).toBe("done");
  });

  it("(a) the idle timeout itself, reached while offline, is disconnected too — not a stalled model", () => {
    const f = classifyStreamFailure(new Error("stream_stalled"), { stalled: true, offline: true, idleSeconds: 60 });
    expect(f).toMatchObject({ code: "stream_interrupted", cause: "disconnected", retryable: true, details: { offline: true } });
  });

  it("(b) a silent stream while ONLINE is unchanged: nothing before 60 s, then stream_stalled / timeout", async () => {
    vi.useFakeTimers();
    globalThis.fetch = silentAfter([DELTA("half an ")]);
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => true });
    const s = stream(gw);
    await vi.advanceTimersByTimeAsync(59_000);
    expect(s.frames.map((f) => f.kind)).toEqual(["content_delta"]);
    await vi.advanceTimersByTimeAsync(2_000);
    await s.finished;
    expect(s.frames[s.frames.length - 1]).toMatchObject({ kind: "error", code: "stream_stalled", cause: "timeout", retryable: true });
  });

  it("(b) a client built without the option behaves as online (tests, non-Electron contexts)", async () => {
    vi.useFakeTimers();
    globalThis.fetch = silentAfter([]);
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet);
    const s = stream(gw);
    await vi.advanceTimersByTimeAsync(61_000);
    await s.finished;
    expect(s.frames[s.frames.length - 1]).toMatchObject({ code: "stream_stalled", cause: "timeout" });
  });

  it("(b) a gateway on THIS machine is not judged by the network: offline + loopback is still a stall", async () => {
    vi.useFakeTimers();
    globalThis.fetch = silentAfter([]);
    const gw = new GatewayClient("http://127.0.0.1:8000", () => "t", keyStore, quiet, { isOnline: () => false });
    const s = stream(gw);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(s.frames).toEqual([]);
    await vi.advanceTimersByTimeAsync(31_000);
    await s.finished;
    expect(s.frames[s.frames.length - 1]).toMatchObject({ code: "stream_stalled", cause: "timeout" });
  });

  it("(c) a thrown read while offline is stream_interrupted / disconnected and says the machine was offline", async () => {
    globalThis.fetch = tearsAfter([DELTA("a")], "read ECONNRESET");
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => false });
    const s = stream(gw);
    await s.finished;
    const last = s.frames[s.frames.length - 1]!;
    expect(last).toMatchObject({ kind: "error", code: "stream_interrupted", cause: "disconnected", retryable: true, details: { transport: "read ECONNRESET", offline: true } });
  });

  it("(c) a request that never opened because the machine is offline is disconnected as well", async () => {
    globalThis.fetch = (async () => { throw new TypeError("fetch failed"); }) as typeof fetch;
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => false });
    const s = stream(gw);
    await s.finished;
    expect(s.frames).toHaveLength(1);
    expect(s.frames[0]).toMatchObject({ code: "stream_interrupted", cause: "disconnected", retryable: true, details: { offline: true } });
  });

  it("(c) an isOnline that throws is read as online — it never breaks a turn", async () => {
    globalThis.fetch = tearsAfter([], "terminated");
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => { throw new Error("not ready"); } });
    const s = stream(gw);
    await s.finished;
    const last = s.frames[s.frames.length - 1]!;
    expect(last).toMatchObject({ code: "stream_interrupted", cause: "disconnected" });
    expect(last.details).not.toHaveProperty("offline");
  });
});

describe("FIX124 R2 — what was written before the loss is kept", () => {
  it("(d) deltas, then the network goes: the page's terminal says disconnected, the imported row keeps the text with status error / disconnected", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix124-"));
    const imports: ImportMessageItem[][] = [];
    const posted: { event: string; data: Record<string, unknown> }[] = [];
    // Only the completion is served; every other gateway read fails closed.
    globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
      String(url).endsWith("/v1/chat/completions") ? tearsAfter([DELTA("half an "), DELTA("answer")], "terminated")(url as never, init) : new Response("{}", { status: 404 })) as typeof fetch;
    const gw = new GatewayClient("http://gw.test", () => "t", keyStore, quiet, { isOnline: () => false });
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
    const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}), ready: true, phase: "ready", isRunning: true, ensureStarted: async () => {}, invalidateTools: () => {} };
    const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }), temporaryChatReadsMemory: true };
    let co: TurnCoordinator;
    const hostTools = new HostTools(gw, chatEdge as never, (req, sig) => co.runNested(req, sig), quiet, () => false);
    const bridge = {
      postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => posted.push({ event, data: data as Record<string, unknown> }),
      post: () => {}, postHostResult: () => {}, postHostError: () => {},
    };
    co = new TurnCoordinator(
      bridge as never, gw, chatEdge as never, assembler as never, sidecar as never, hostTools,
      new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
      { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", forceModel: "qwen3.8-27b-fp8", approvalMode: "skip", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
      quiet,
    );
    co.onTurnSubmit({ conversationId: "c1", text: "a question", attachmentHandles: [], modelId: "qwen3.8-27b-fp8", imageMode: false, editMessageId: null, quick: false } as never);
    await co.waitForTurn("c1");

    const terminals = posted.filter((p) => p.event === "message_complete" || p.event === "error" || p.event === "cancelled");
    expect(terminals).toHaveLength(1);
    expect(terminals[0]!.event).toBe("error");
    expect(terminals[0]!.data).toMatchObject({ code: "stream_interrupted", cause: "disconnected", retryable: true });
    expect(posted.filter((p) => p.event === "content_delta").map((p) => p.data["delta"]).join("")).toBe("half an answer");

    expect(imports).toHaveLength(1); // one generation, one import: nothing was re-sent
    const assistant = imports[0]!.find((i) => i.role === "assistant");
    expect(assistant).toMatchObject({ content: "half an answer", status: "error", terminal_cause: "disconnected", error_code: "stream_interrupted" });
  });
});

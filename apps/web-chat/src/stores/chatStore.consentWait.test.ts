// FIX30 §3.2 — what the turn is waiting on, and the deadman that must not
// answer a consent for the user.
//
// Pins, over the real stream pump with the host's own frame shapes:
//   • a `status` frame with waiting:"consent" / "tool" lands on the streaming
//     row as ``agent_wait`` (and clears when the host says waiting:null);
//   • a finished tool_activity clears a "tool" wait;
//   • a row with a pending consent is NEVER flagged stalled and NEVER killed
//     by the idle watchdog, however long the user takes;
//   • ending the turn (message_complete / cancel / error) drops the card and
//     the wait — no stale card can be answered into an expired prompt.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public code: string,
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});

vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { STREAM_IDLE, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push(raw: string) {
      controller.enqueue(enc.encode(raw));
    },
    close() {
      controller.close();
    },
  };
}

const conv = (id: string): Conversation => ({
  id,
  title: "T",
  model_id: "m",
  message_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);
const savedIdle = { ...STREAM_IDLE };
const streaming = () => useChatStore.getState().messages["c1"].find((m) => m.isStreaming);
const consent = { promptId: "consent-1", tool: "run_command", argumentSummary: "Get-Date", dangerClass: "danger.shell" };

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.useFakeTimers();
  STREAM_IDLE.timeoutMs = 1000;
  STREAM_IDLE.deadMs = 2000;
  STREAM_IDLE.checkMs = 100;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  mockApi.cancelGeneration.mockResolvedValue(undefined);
});

afterEach(() => {
  Object.assign(STREAM_IDLE, savedIdle);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function startTurn(s: ReturnType<typeof makeStream>) {
  mockApi.openMessageStream.mockResolvedValue(s.response);
  useChatStore.setState({ conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } });
  const p = useChatStore.getState().sendMessage("c1", "hi", []);
  await advance(0);
  s.push(frame("message_start", { message_id: "srv-1", conversation_id: "c1" }));
  await advance(0);
  return p;
}

describe("FIX30 §3.2 — the waiting-on state", () => {
  it("status waiting:consent / tool → agent_wait; waiting:null clears it; a finished tool clears a tool wait", async () => {
    const s = makeStream();
    const turn = startTurn(s);

    s.push(frame("status", { status: "waiting_consent", waiting: "consent", tool: "run_command", promptId: "consent-1", elapsedMs: 0 }));
    await advance(0);
    expect(streaming()?.agent_wait).toEqual({ kind: "consent", tool: "run_command", promptId: "consent-1", elapsedMs: 0 });
    expect(streaming()?.agent_status).toBe("waiting_consent");

    s.push(frame("status", { status: "consent_answered", waiting: null, tool: "run_command", promptId: "consent-1", decision: "allow", elapsedMs: 900 }));
    await advance(0);
    expect(streaming()?.agent_wait).toBeNull();

    s.push(frame("tool_activity", { id: "call_1", tool: "run_command", phase: "started", argsSummary: "Get-Date" }));
    s.push(frame("status", { status: "tool_running", waiting: "tool", tool: "run_command", toolCallId: "call_1", elapsedMs: 15000 }));
    await advance(0);
    expect(streaming()?.agent_wait).toMatchObject({ kind: "tool", tool: "run_command", elapsedMs: 15000 });

    s.push(frame("tool_activity", { id: "call_1", tool: "run_command", phase: "finished", success: true, durationMs: 16000 }));
    await advance(0);
    expect(streaming()?.agent_wait).toBeNull();

    s.push(frame("message_complete", { message_id: "srv-1" }));
    s.close();
    await advance(0);
    await turn;
  });

  it("a pending consent is never stalled and never killed by the deadman — 5× deadMs with no bytes", async () => {
    const s = makeStream();
    const turn = startTurn(s);
    useChatStore.getState().addPendingConsent("c1", consent);
    expect(streaming()?.pending_consent?.promptId).toBe("consent-1");

    await advance(STREAM_IDLE.deadMs * 5);
    expect(useChatStore.getState().stalledStreams["c1"]).toBeFalsy();
    expect(streaming()?.isStreaming).toBe(true); // not aborted
    expect(streaming()?.pending_consent?.promptId).toBe("consent-1");
    expect(mockApi.cancelGeneration).not.toHaveBeenCalled();

    // Answered → the clock starts again from now, and the deadman is armed again:
    // a truly silent stream after the answer IS aborted (its correct job).
    const ctrl = useChatStore.getState().abortControllers["c1"]!;
    useChatStore.getState().resolveConsent("c1", "consent-1", "allow", "once");
    expect(streaming()?.pending_consent).toBeNull();
    expect(ctrl.signal.aborted).toBe(false);
    await advance(STREAM_IDLE.deadMs + STREAM_IDLE.checkMs * 2);
    expect(ctrl.signal.aborted).toBe(true);
    s.close();
    await turn.catch(() => {});
  });

  it("message_complete drops the card and the wait", async () => {
    const s = makeStream();
    const turn = startTurn(s);
    useChatStore.getState().addPendingConsent("c1", consent);
    s.push(frame("status", { status: "waiting_consent", waiting: "consent", tool: "run_command", promptId: "consent-1", elapsedMs: 0 }));
    await advance(0);
    const id = streaming()!.id;
    s.push(frame("message_complete", { message_id: "srv-1" }));
    s.close();
    await advance(0);
    await turn;
    const row = useChatStore.getState().messages["c1"].find((m) => m.id === id || m.id === "srv-1")!;
    expect(row.pending_consent).toBeNull();
    expect(row.agent_wait).toBeNull();
  });

  it("Stop drops the card synchronously (finalize as cancelled)", () => {
    const row: Message = { id: "m1", role: "assistant", content: "", status: "streaming", created_at: "2026-01-01T00:00:00Z", isStreaming: true, pending_consent: consent, agent_wait: { kind: "consent", tool: "run_command", promptId: "consent-1", elapsedMs: 0 } };
    useChatStore.setState({ messages: { c1: [row] }, abortControllers: { c1: new AbortController() }, streamingAssistantId: { c1: "m1" } });
    void useChatStore.getState().cancelGeneration("c1");
    const after = useChatStore.getState().messages["c1"][0];
    expect(after.status).toBe("cancelled");
    expect(after.pending_consent).toBeNull();
    expect(after.agent_wait).toBeNull();
  });
});

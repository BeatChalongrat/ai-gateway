// FIX73 — the question card in the store, over the real stream pump with the
// host's own frame shapes. Pins:
//   • a `status` frame with waiting:"question" lands on the streaming row as
//     ``agent_wait`` (kind "question") and waiting:null clears it;
//   • addPendingQuestion attaches the card to the streaming row; a row with a
//     pending question is NEVER flagged stalled and NEVER killed by the idle
//     watchdog, however long the user takes;
//   • resolveQuestion posts question.respond over the bridge with the answer
//     and clears the card;
//   • ending the turn (message_complete / cancel) drops the card and the wait.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
  },
  mockBridge: { seq: 0, peekSeq: vi.fn(() => 0), respondQuestion: vi.fn(), respondConsent: vi.fn(), cancelTurn: vi.fn() },
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
// The desktop path: the host holds the turn open and the answer rides the bridge.
vi.mock("@/lib/bridge", () => ({ bridge: mockBridge, usingBridgeTransport: true }));
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
const question = { promptId: "question-1", questions: [{ question: "Which format?", header: "Format", multiSelect: false, options: [{ label: "Summary", description: "" }, { label: "Detailed", description: "" }] }] };

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

describe("FIX73 — the question card in the store", () => {
  it("status waiting:question → agent_wait kind question; waiting:null clears it", async () => {
    const s = makeStream();
    const turn = startTurn(s);
    s.push(frame("status", { status: "waiting_question", waiting: "question", tool: "ask_user_question", promptId: "question-1", elapsedMs: 0 }));
    await advance(0);
    expect(streaming()?.agent_wait).toEqual({ kind: "question", tool: "ask_user_question", promptId: "question-1", elapsedMs: 0 });
    s.push(frame("status", { status: "question_answered", waiting: null, tool: "ask_user_question", promptId: "question-1", outcome: "answered", elapsedMs: 900 }));
    await advance(0);
    expect(streaming()?.agent_wait).toBeNull();
    s.close();
    await turn.catch(() => {});
  });

  it("a row with a pending question is never stalled and never killed by the idle watchdog", async () => {
    const s = makeStream();
    const turn = startTurn(s);
    useChatStore.getState().addPendingQuestion("c1", question);
    expect(streaming()?.pending_question?.promptId).toBe("question-1");
    await advance(STREAM_IDLE.deadMs * 3);
    expect(useChatStore.getState().stalledStreams["c1"]).toBeFalsy();
    expect(useChatStore.getState().abortControllers["c1"]!.signal.aborted).toBe(false);
    expect(mockApi.cancelGeneration).not.toHaveBeenCalled();
    s.close();
    await turn.catch(() => {});
  });

  it("resolveQuestion posts question.respond over the bridge with the answer and clears the card", async () => {
    const s = makeStream();
    const turn = startTurn(s);
    useChatStore.getState().addPendingQuestion("c1", question);
    const answer = { promptId: "question-1", outcome: "answered" as const, answers: { "Which format?": ["Detailed"] }, response: null };
    useChatStore.getState().resolveQuestion("c1", answer);
    expect(mockBridge.respondQuestion).toHaveBeenCalledWith(answer);
    expect(streaming()?.pending_question).toBeNull();
    s.close();
    await turn.catch(() => {});
  });

  it("message_complete drops the card and the wait", async () => {
    const s = makeStream();
    const turn = startTurn(s);
    useChatStore.getState().addPendingQuestion("c1", question);
    s.push(frame("status", { status: "waiting_question", waiting: "question", tool: "ask_user_question", promptId: "question-1", elapsedMs: 0 }));
    await advance(0);
    const id = streaming()!.id;
    s.push(frame("message_complete", { message_id: "srv-1" }));
    s.close();
    await advance(0);
    await turn;
    const row = useChatStore.getState().messages["c1"].find((m) => m.id === id || m.id === "srv-1")!;
    expect(row.pending_question).toBeNull();
    expect(row.agent_wait).toBeNull();
  });

  it("Stop drops the card synchronously (finalize as cancelled)", () => {
    const row: Message = { id: "m1", role: "assistant", content: "", status: "streaming", created_at: "2026-01-01T00:00:00Z", isStreaming: true, pending_question: question, agent_wait: { kind: "question", tool: "ask_user_question", promptId: "question-1", elapsedMs: 0 } };
    useChatStore.setState({ messages: { c1: [row] }, abortControllers: { c1: new AbortController() }, streamingAssistantId: { c1: "m1" } });
    void useChatStore.getState().cancelGeneration("c1");
    const after = useChatStore.getState().messages["c1"][0];
    expect(after.status).toBe("cancelled");
    expect(after.pending_question).toBeNull();
    expect(after.agent_wait).toBeNull();
  });
});

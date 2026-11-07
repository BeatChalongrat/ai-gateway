// FIX74 — the question card on the WEB path (no desktop host). Pins, over
// the real stream pump with chat-edge's frame shapes:
//   • a `question` frame lands the card on the streaming row;
//   • `message_paused` ends the stream WITHOUT finishing the turn: the row
//     keeps its text and its card, stops streaming, status waiting_user;
//   • answering posts to the questions endpoint and consumes the
//     continuation into the SAME row (text appended, then completed);
//   • 409 (another tab answered first) drops the card and reloads the
//     conversation instead of inventing a state;
//   • a reloaded conversation whose row carries pending_question renders it
//     (the wire shape is the desktop's).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    openQuestionAnswerStream: vi.fn(),
    listMessages: vi.fn(),
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
vi.mock("@/lib/bridge", () => ({ bridge: mockBridge, usingBridgeTransport: false }));
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
const row = (id: string) => useChatStore.getState().messages["c1"].find((m) => m.id === id);
const card = { promptId: "11111111-1111-1111-1111-111111111111", toolCallId: "call_ask", questions: [{ question: "Which format?", header: "Format", multiSelect: false, options: [{ label: "Summary", description: "" }, { label: "Detailed", description: "" }] }] };

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
  const p = useChatStore.getState().sendMessage("c1", "make a logo", []);
  await advance(0);
  s.push(frame("message_start", { message_id: "srv-1", conversation_id: "c1" }));
  await advance(0);
  return p;
}

async function parkedTurn() {
  const s = makeStream();
  const turn = startTurn(s);
  await advance(0);
  await advance(0); // message_start is on the wire before anything else is pushed
  s.push(frame("content_delta", { delta: "Let me ask. " }));
  s.push(frame("question", { ...card, messageId: "srv-1" }));
  s.push(frame("message_paused", { message_id: "srv-1", prompt_id: card.promptId }));
  s.close();
  await advance(0);
  await turn;
  return row("srv-1")!;
}

describe("FIX74 — the question card on the web path", () => {
  it("question + message_paused: the row keeps its text and card, stops streaming, and is waiting_user (not completed)", async () => {
    const m = await parkedTurn();
    expect(m.pending_question?.promptId).toBe(card.promptId);
    expect(m.pending_question?.questions[0]?.header).toBe("Format");
    expect(m.content).toBe("Let me ask. ");
    expect(m.status).toBe("waiting_user");
    expect(m.isStreaming).toBe(false);
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  it("answering posts to the questions endpoint and the continuation streams into the SAME row, then completes", async () => {
    await parkedTurn();
    const cont = makeStream();
    mockApi.openQuestionAnswerStream.mockResolvedValue(cont.response);
    const answer = { promptId: card.promptId, outcome: "answered" as const, answers: { "Which format?": ["Detailed"] }, response: null };
    useChatStore.getState().resolveQuestion("c1", answer);
    await advance(0);
    expect(mockBridge.respondQuestion).not.toHaveBeenCalled();
    expect(mockApi.openQuestionAnswerStream).toHaveBeenCalledWith("c1", card.promptId, answer, expect.anything());
    expect(row("srv-1")?.pending_question).toBeNull();
    expect(row("srv-1")?.isStreaming).toBe(true);
    cont.push(frame("message_start", { message_id: "srv-1", conversation_id: "c1", resumed: true }));
    cont.push(frame("content_delta", { delta: "Here is the detailed version." }));
    cont.push(frame("message_complete", { message_id: "srv-1" }));
    cont.close();
    await advance(0);
    await advance(0);
    const m = row("srv-1")!;
    expect(m.content).toBe("Let me ask. Here is the detailed version.");
    expect(m.status).toBe("completed");
    expect(m.isStreaming).toBe(false);
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  it("409 (another tab answered first): the card is dropped and the conversation is reloaded from the server", async () => {
    await parkedTurn();
    mockApi.openQuestionAnswerStream.mockResolvedValue({ ok: false, status: 409, json: async () => ({ code: "QUESTION_ALREADY_ANSWERED" }) } as unknown as Response);
    mockApi.listMessages.mockResolvedValue({ messages: [{ id: "srv-1", role: "assistant", content: "Let me ask. Done elsewhere.", status: "completed", created_at: "2026-01-01T00:00:00Z" }], has_more: false });
    useChatStore.getState().resolveQuestion("c1", { promptId: card.promptId, outcome: "skipped", answers: {}, response: null });
    await advance(0);
    await advance(0);
    expect(mockApi.listMessages).toHaveBeenCalledWith("c1", { limit: 50 });
    const m = row("srv-1")!;
    expect(m.pending_question).toBeFalsy();
    expect(m.content).toBe("Let me ask. Done elsewhere.");
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  it("a reloaded conversation renders the parked row's card from the wire and does not try to resume it", async () => {
    const parked: Message = { id: "srv-9", role: "assistant", content: "Let me ask.", status: "waiting_user", created_at: "2026-01-01T00:00:00Z", pending_question: card };
    mockApi.listMessages.mockResolvedValue({ messages: [parked], has_more: false });
    useChatStore.setState({ conversations: [conv("c1")] });
    await useChatStore.getState().loadMessages("c1");
    await advance(0);
    expect(row("srv-9")?.pending_question?.promptId).toBe(card.promptId);
    expect(mockApi.openResumeStream).not.toHaveBeenCalled();
  });
});

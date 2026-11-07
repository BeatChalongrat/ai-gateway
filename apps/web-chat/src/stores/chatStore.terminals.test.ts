// FIX41 — the web client's half of docs/STREAM_CONTRACT.md.
//
//   * `cancelled` is a terminal: the page settles the message as cancelled
//     instead of waiting out its 180 s dead-stream watchdog.
//   * the cursor is (run_id, seq) and it advances on frames the page
//     RENDERED, so a reattach asks for exactly what it is missing;
//   * a reattach with a cursor does NOT wipe the bubble first — the server
//     is not going to resend what the page already has;
//   * 409 RESUME_TOO_OLD reloads the message rather than pretending the
//     truncated text is the whole answer.
//
// Same harness as chatStore.resume.test.ts (mocked api, real parseSSE over
// a hand-fed stream) so the two suites stay comparable.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBumpUsage } = vi.hoisted(() => ({
  mockApi: {
    listMessages: vi.fn(),
    openResumeStream: vi.fn(),
    openMessageStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
  },
  mockBumpUsage: vi.fn(),
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

vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: mockBumpUsage }));

import { RESUME_RETRY_DELAYS, clearStreamCursor, streamCursorFor, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const originalDelays = [...RESUME_RETRY_DELAYS];
const enc = new TextEncoder();

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
  };
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0));

function conv(id: string): Conversation {
  return {
    id,
    title: "T",
    model_id: "claude-sonnet-4",
    message_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}
const userMsg = (id: string): Message => ({
  id, role: "user", content: "ถามอะไรสักอย่าง", status: "completed", created_at: "2026-01-01T00:00:00Z",
});
const streamingAsst = (id: string, content = ""): Message => ({
  id, role: "assistant", content, status: "streaming", created_at: "2026-01-01T00:00:01Z",
});
const messagesOf = (id: string) => useChatStore.getState().messages[id] ?? [];

let rafQueue: Map<number, FrameRequestCallback>;
let rafId: number;
function fireRaf() {
  const cbs = [...rafQueue.values()];
  rafQueue.clear();
  for (const cb of cbs) cb(0);
}

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearStreamCursor("c1");
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays.map(() => 0));
  rafQueue = new Map();
  rafId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = rafId++;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => void rafQueue.delete(id));
  useChatStore.setState({ conversations: [conv("c1")] });
  mockApi.getConversation.mockResolvedValue(conv("c1"));
});

afterEach(() => {
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays);
  clearStreamCursor("c1");
  vi.unstubAllGlobals();
});

describe("FIX41 — the cursor", () => {
  it("advances on every enveloped frame and remembers the run", async () => {
    mockApi.listMessages.mockResolvedValue({ messages: [userMsg("u1"), streamingAsst("m1")], has_more: false });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);

    await useChatStore.getState().loadMessages("c1");
    await tick();
    s.push(frame("message_start", { run_id: "run-1", seq: 1, message_id: "m1", conversation_id: "c1" }));
    s.push(frame("content_delta", { run_id: "run-1", seq: 2, delta: "ครึ่งแรก " }));
    s.push(frame("tool_activity", { run_id: "run-1", seq: 3, id: "t1", tool: "web_fetch", phase: "started" }));
    await tick();

    expect(streamCursorFor("c1")).toEqual({ runId: "run-1", seq: 3 });
  });

  it("resets when a frame from a different run arrives", async () => {
    mockApi.listMessages.mockResolvedValue({ messages: [userMsg("u1"), streamingAsst("m1")], has_more: false });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);
    await useChatStore.getState().loadMessages("c1");
    await tick();

    s.push(frame("content_delta", { run_id: "run-1", seq: 40, delta: "a" }));
    await tick();
    expect(streamCursorFor("c1")?.seq).toBe(40);
    s.push(frame("content_delta", { run_id: "run-2", seq: 1, delta: "b" }));
    await tick();
    expect(streamCursorFor("c1")).toEqual({ runId: "run-2", seq: 1 });
  });

  it("is cleared when the run completes — a finished run resumes nothing", async () => {
    mockApi.listMessages.mockResolvedValue({ messages: [userMsg("u1"), streamingAsst("m1")], has_more: false });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);
    await useChatStore.getState().loadMessages("c1");
    await tick();
    s.push(frame("content_delta", { run_id: "run-1", seq: 1, delta: "a" }));
    s.push(frame("message_complete", { run_id: "run-1", seq: 2, final_seq: 2, message_id: "m1" }));
    s.close();
    await tick();
    await tick();
    expect(streamCursorFor("c1")).toBeUndefined();
  });
});

describe("FIX41 — cancelled is a terminal", () => {
  it("settles the message as cancelled instead of leaving it streaming", async () => {
    mockApi.listMessages.mockResolvedValue({ messages: [userMsg("u1"), streamingAsst("m1")], has_more: false });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);
    await useChatStore.getState().loadMessages("c1");
    await tick();

    s.push(frame("message_start", { run_id: "r", seq: 1, message_id: "m1", conversation_id: "c1" }));
    s.push(frame("content_delta", { run_id: "r", seq: 2, delta: "ตอบไปได้ครึ่งเดียว" }));
    await tick();
    fireRaf();
    s.push(frame("cancelled", { run_id: "r", seq: 3, final_seq: 3, message_id: "m1", partial: true }));
    s.close();
    await tick();
    await tick();

    const m = messagesOf("c1")[1];
    expect(m.status).toBe("cancelled");
    expect(m.isStreaming).toBe(false);
    // The partial answer is KEPT: a person who stops a turn wants what was
    // written so far, not a blank bubble.
    expect(m.content).toBe("ตอบไปได้ครึ่งเดียว");
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });
});

describe("FIX41 — reattaching with a cursor", () => {
  it("sends starting_after and does NOT wipe what is already on screen", async () => {
    mockApi.listMessages.mockResolvedValue({ messages: [userMsg("u1"), streamingAsst("m1")], has_more: false });
    const first = makeStream();
    mockApi.openResumeStream.mockResolvedValue(first.response);
    await useChatStore.getState().loadMessages("c1");
    await tick();
    expect(mockApi.openResumeStream.mock.calls[0][3]).toBe(0);

    first.push(frame("message_start", { run_id: "r", seq: 1, message_id: "m1", conversation_id: "c1" }));
    first.push(frame("content_delta", { run_id: "r", seq: 2, delta: "ส่วนที่หนึ่ง " }));
    await tick();
    fireRaf();
    expect(messagesOf("c1")[1].content).toBe("ส่วนที่หนึ่ง ");

    // The connection drops. The store reattaches on its own; this test
    // drives the same entry point the watchdog uses.
    useChatStore.setState((st) => {
      const sids = { ...st.streamingAssistantId };
      delete sids["c1"];
      return { streamingAssistantId: sids };
    });
    const second = makeStream();
    mockApi.openResumeStream.mockResolvedValue(second.response);
    void useChatStore.getState().resumeStream("c1", "m1");
    await tick();

    const call = mockApi.openResumeStream.mock.calls[1];
    expect(call[3]).toBe(2); // continue after the last frame we rendered
    // The bubble was NOT reset: the server is only sending seq > 2.
    expect(messagesOf("c1")[1].content).toBe("ส่วนที่หนึ่ง ");

    second.push(frame("content_delta", { run_id: "r", seq: 3, delta: "ส่วนที่สอง" }));
    second.push(frame("message_complete", { run_id: "r", seq: 4, final_seq: 4, message_id: "m1" }));
    second.close();
    await tick();
    fireRaf();
    await tick();
    expect(messagesOf("c1")[1].content).toBe("ส่วนที่หนึ่ง ส่วนที่สอง");
  });

  it("409 RESUME_TOO_OLD reloads the message instead of stitching a hole", async () => {
    mockApi.listMessages.mockResolvedValue({ messages: [userMsg("u1"), streamingAsst("m1")], has_more: false });
    const first = makeStream();
    mockApi.openResumeStream.mockResolvedValue(first.response);
    await useChatStore.getState().loadMessages("c1");
    await tick();
    first.push(frame("content_delta", { run_id: "r", seq: 9, delta: "เก่า" }));
    await tick();
    expect(streamCursorFor("c1")?.seq).toBe(9);

    useChatStore.setState((st) => {
      const sids = { ...st.streamingAssistantId };
      delete sids["c1"];
      return { streamingAssistantId: sids };
    });
    mockApi.openResumeStream.mockResolvedValue({
      ok: false,
      status: 409,
      clone: () => ({ json: async () => ({ error: { code: "RESUME_TOO_OLD", message: "gone" } }) }),
    } as unknown as Response);
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), { ...streamingAsst("m1", "คำตอบเต็มจาก API"), status: "completed" }],
      has_more: false,
    });

    await useChatStore.getState().resumeStream("c1", "m1");
    await tick();

    expect(streamCursorFor("c1")).toBeUndefined();
    expect(messagesOf("c1")[1].content).toBe("คำตอบเต็มจาก API");
    // One 409 must not be retried five times: too-old is final.
    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(2);
  });
});

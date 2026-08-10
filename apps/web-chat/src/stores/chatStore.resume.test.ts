// Phase 5 — stream-resume tests. Pins the reattach flow: detect a
// message stuck in status "streaming" on load, open the resume SSE,
// reset-then-replay the prefix through the SAME consumeStream/RAF
// pipeline, retry on 409/network drop, and never resume a message
// that already completed. Mirrors the chatStore.stream.test.ts
// harness (mocked api, real parseSSE over a hand-fed ReadableStream).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBumpUsage } = vi.hoisted(() => {
  return {
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
  };
});

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

vi.mock("@/hooks/useUsage", () => ({
  bumpUsageVersion: mockBumpUsage,
}));

import { dictionaries } from "@/lib/i18n/dict";
import { RESUME_RETRY_DELAYS, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const originalDelays = [...RESUME_RETRY_DELAYS];

const enc = new TextEncoder();

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

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
    fail(err: unknown) {
      controller.error(err);
    },
    wireAbort(signal: AbortSignal) {
      signal.addEventListener("abort", () => {
        controller.error(new DOMException("The user aborted.", "AbortError"));
      });
    },
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
  id,
  role: "user",
  content: "คำถามยาว ๆ",
  status: "completed",
  created_at: "2026-01-01T00:00:00Z",
});

const streamingAsst = (id: string, content = ""): Message => ({
  id,
  role: "assistant",
  content,
  status: "streaming",
  created_at: "2026-01-01T00:00:01Z",
});

function messagesOf(id: string): Message[] {
  return useChatStore.getState().messages[id] ?? [];
}

// RAF control — same pattern as the stream suite.
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
  // Zero the backoff so retry tests run instantly (restored below).
  RESUME_RETRY_DELAYS.splice(
    0,
    RESUME_RETRY_DELAYS.length,
    ...originalDelays.map(() => 0),
  );
  rafQueue = new Map();
  rafId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = rafId++;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    rafQueue.delete(id);
  });
  useChatStore.setState({ conversations: [conv("c1")] });
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
});

afterEach(() => {
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays);
  vi.unstubAllGlobals();
});

describe("stream resume", () => {
  it("detects a streaming row on loadMessages, reattaches, replays the prefix through RAF, and completes", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), streamingAsst("m1")],
      has_more: false,
    });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);

    await useChatStore.getState().loadMessages("c1");
    await tick();

    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(1);
    expect(mockApi.openResumeStream.mock.calls[0][0]).toBe("c1");
    expect(mockApi.openResumeStream.mock.calls[0][1]).toBe("m1");
    // While reattached the message occupies the streaming slot (input
    // shows Stop, exactly like a live stream).
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBe("m1");

    // Server replays the prefix as one delta, then the live tail.
    s.push(frame("message_start", { message_id: "m1", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "คำตอบครึ่งแรกที่พิมพ์ไปแล้ว " }));
    await tick();
    fireRaf();
    expect(messagesOf("c1")[1].content).toBe("คำตอบครึ่งแรกที่พิมพ์ไปแล้ว ");

    s.push(frame("content_delta", { delta: "และครึ่งหลังที่วิ่งต่อ" }));
    s.push(frame("message_complete", { message_id: "m1" }));
    s.close();
    await tick();
    await tick();

    const m = messagesOf("c1")[1];
    expect(m.content).toBe("คำตอบครึ่งแรกที่พิมพ์ไปแล้ว และครึ่งหลังที่วิ่งต่อ");
    expect(m.status).toBe("completed");
    expect(m.isStreaming).toBe(false);
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
    expect(useChatStore.getState().errorBanner).toBeNull();
  });

  it("resets stale partial content before replaying so retries never duplicate text", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), streamingAsst("m1", "STALE-PARTIAL")],
      has_more: false,
    });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);

    await useChatStore.getState().loadMessages("c1");
    await tick();

    s.push(frame("content_delta", { delta: "FRESH" }));
    s.push(frame("message_complete", { message_id: "m1" }));
    s.close();
    await tick();
    await tick();

    expect(messagesOf("c1")[1].content).toBe("FRESH");
  });

  it("never resumes a message that already completed", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [
        userMsg("u1"),
        { ...streamingAsst("m1", "done"), status: "completed" },
      ],
      has_more: false,
    });
    await useChatStore.getState().loadMessages("c1");
    await tick();
    expect(mockApi.openResumeStream).not.toHaveBeenCalled();
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  it("retries after 409 RESUME_UNAVAILABLE and succeeds on the next attempt", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), streamingAsst("m1")],
      has_more: false,
    });
    const s = makeStream();
    mockApi.openResumeStream
      .mockResolvedValueOnce({ ok: false, status: 409 } as unknown as Response)
      .mockResolvedValueOnce(s.response);

    await useChatStore.getState().loadMessages("c1");
    await tick();
    await tick();

    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(2);
    s.push(frame("content_delta", { delta: "หลัง retry" }));
    s.push(frame("message_complete", { message_id: "m1" }));
    s.close();
    await tick();
    await tick();
    expect(messagesOf("c1")[1].content).toBe("หลัง retry");
    expect(messagesOf("c1")[1].status).toBe("completed");
  });

  it("reattaches again when the resume stream itself drops mid-way (no duplicated prefix)", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), streamingAsst("m1")],
      has_more: false,
    });
    const s1 = makeStream();
    const s2 = makeStream();
    mockApi.openResumeStream
      .mockResolvedValueOnce(s1.response)
      .mockResolvedValueOnce(s2.response);

    await useChatStore.getState().loadMessages("c1");
    await tick();

    // First attach delivers half the prefix, then the network dies.
    s1.push(frame("content_delta", { delta: "ครึ่ง" }));
    await tick();
    fireRaf();
    s1.fail(new TypeError("network error"));
    await tick();
    await tick();

    // Second attach replays the FULL prefix — the bubble must have
    // been reset, not appended to.
    s2.push(frame("content_delta", { delta: "ครึ่งแรก เต็ม ๆ" }));
    s2.push(frame("message_complete", { message_id: "m1" }));
    s2.close();
    await tick();
    await tick();

    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(2);
    expect(messagesOf("c1")[1].content).toBe("ครึ่งแรก เต็ม ๆ");
    expect(messagesOf("c1")[1].status).toBe("completed");
  });

  it("finalizes as cancelled when the user stops during a resumed stream", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), streamingAsst("m1")],
      has_more: false,
    });
    const s = makeStream();
    mockApi.openResumeStream.mockImplementation(
      (_c: string, _m: string, signal: AbortSignal) => {
        s.wireAbort(signal);
        return Promise.resolve(s.response);
      },
    );

    await useChatStore.getState().loadMessages("c1");
    await tick();
    s.push(frame("content_delta", { delta: "กำลังพิมพ์" }));
    await tick();
    fireRaf();

    await useChatStore.getState().cancelGeneration("c1");
    await tick();
    await tick();

    expect(mockApi.cancelGeneration).toHaveBeenCalledWith("c1");
    expect(messagesOf("c1")[1].status).toBe("cancelled");
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  it("gives up after all attempts fail and shows the localized error card", async () => {
    mockApi.listMessages.mockResolvedValue({
      messages: [userMsg("u1"), streamingAsst("m1")],
      has_more: false,
    });
    mockApi.openResumeStream.mockRejectedValue(new TypeError("refused"));

    await useChatStore.getState().loadMessages("c1");
    await tick();
    await tick();
    await tick();

    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(
      RESUME_RETRY_DELAYS.length,
    );
    expect(messagesOf("c1")[1].status).toBe("error");
    expect(useChatStore.getState().errorBanner).toBe(
      dictionaries.en["error.resumeLost"],
    );
  });
});

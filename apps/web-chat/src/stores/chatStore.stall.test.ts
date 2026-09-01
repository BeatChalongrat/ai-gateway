// Batch 2 T2 — stream idle watchdog. Pins: a stream with no bytes past
// STREAM_IDLE.timeoutMs flags the conversation stalled (and NOT
// before); server heartbeat comment frames count as liveness even
// though parseSSE never yields them as events (a long legitimate tool
// run must not trip the watchdog); data resuming clears the flag;
// stream end always clears it; retryStalledStream aborts the local
// connection and reattaches through the EXISTING resume machinery.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => {
  return {
    mockApi: {
      openMessageStream: vi.fn(),
      openResumeStream: vi.fn(),
      getConversation: vi.fn(),
      generateTitle: vi.fn(),
      cancelGeneration: vi.fn(),
      renameConversation: vi.fn(),
    },
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
  bumpUsageVersion: vi.fn(),
}));

import { RESUME_RETRY_DELAYS, STREAM_IDLE, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

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
    wireAbort(signal: AbortSignal) {
      signal.addEventListener("abort", () => {
        controller.error(new DOMException("The user aborted.", "AbortError"));
      });
    },
  };
}

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

function seedConversation(id: string, messages: Message[] = []) {
  useChatStore.setState({
    conversations: [conv(id)],
    messages: { [id]: messages },
    messagesLoaded: { [id]: true },
  });
}

const stalledOf = (id: string) =>
  Boolean(useChatStore.getState().stalledStreams[id]);

// Fake timers drive BOTH Date.now (the idle clock) and setInterval
// (the watchdog); microtasks (stream reads) flush between timer steps
// via the async advance.
const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);

const savedIdle = { ...STREAM_IDLE };
const savedDelays = [...RESUME_RETRY_DELAYS];

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.useFakeTimers();
  STREAM_IDLE.timeoutMs = 1000;
  STREAM_IDLE.checkMs = 100;
  // RAF isn't provided by fake timers — run flushes synchronously.
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
});

afterEach(() => {
  STREAM_IDLE.timeoutMs = savedIdle.timeoutMs;
  STREAM_IDLE.checkMs = savedIdle.checkMs;
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...savedDelays);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function startTurn(s: ReturnType<typeof makeStream>) {
  mockApi.openMessageStream.mockResolvedValue(s.response);
  seedConversation("c1");
  const sendPromise = useChatStore.getState().sendMessage("c1", "hi", []);
  await advance(0); // let the fetch resolve + pump start
  s.push(frame("message_start", { message_id: "srv-1", conversation_id: "c1" }));
  await advance(0);
  return sendPromise;
}

describe("stream idle watchdog", () => {
  it("flags the stream stalled after the threshold — and not before", async () => {
    const s = makeStream();
    const turn = startTurn(s);

    await advance(900); // below timeoutMs
    expect(stalledOf("c1")).toBe(false);

    await advance(300); // past it
    expect(stalledOf("c1")).toBe(true);

    // Terminal frame ends the stream and clears the flag.
    s.push(frame("message_complete", { message_id: "srv-1" }));
    s.close();
    await advance(0);
    await turn;
    expect(stalledOf("c1")).toBe(false);
  });

  it("heartbeat comment frames keep a long quiet tool run alive", async () => {
    const s = makeStream();
    const turn = startTurn(s);

    // 3× the timeout in wall-clock, but a ": ping" lands every 400ms —
    // the watchdog must never trip.
    for (let i = 0; i < 8; i++) {
      await advance(400);
      s.push(": ping\n\n");
      await advance(0);
      expect(stalledOf("c1")).toBe(false);
    }

    s.push(frame("message_complete", { message_id: "srv-1" }));
    s.close();
    await advance(0);
    await turn;
  });

  it("data resuming clears an already-raised flag", async () => {
    const s = makeStream();
    const turn = startTurn(s);

    await advance(1200);
    expect(stalledOf("c1")).toBe(true);

    s.push(frame("content_delta", { delta: "back!" }));
    await advance(0);
    expect(stalledOf("c1")).toBe(false);

    s.push(frame("message_complete", { message_id: "srv-1" }));
    s.close();
    await advance(0);
    await turn;
  });

  it("retryStalledStream aborts locally and reattaches via the resume machinery", async () => {
    RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, 0);
    // Resume responds with a fresh (empty→complete) stream.
    const resumed = makeStream();
    mockApi.openResumeStream.mockImplementation(
      (_c: string, _m: string, signal: AbortSignal) => {
        resumed.wireAbort(signal);
        return Promise.resolve(resumed.response);
      },
    );

    const s = makeStream();
    const turn = startTurn(s);
    // Wire the primary stream's abort so retry's ctrl.abort() ends it.
    s.wireAbort(useChatStore.getState().abortControllers["c1"]!.signal);

    await advance(1200);
    expect(stalledOf("c1")).toBe(true);

    useChatStore.getState().retryStalledStream("c1");
    expect(stalledOf("c1")).toBe(false);
    await advance(0); // the setTimeout(0) handoff + aborted pump unwinding
    await turn;
    await advance(0);

    expect(mockApi.openResumeStream).toHaveBeenCalledWith(
      "c1",
      "srv-1",
      expect.anything(),
      // FIX41 — the resume carries the client's cursor. This stream's
      // frames carry no seq, so there is nothing to continue from and the
      // whole run is replayed, exactly as before FIX41.
      0,
    );

    resumed.push(frame("message_complete", { message_id: "srv-1" }));
    resumed.close();
    await advance(0);
  });
});

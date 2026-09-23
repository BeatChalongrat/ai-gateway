// FIX130 B2 — the store side of "the thinking box shows only when the
// model really is thinking".
//
//   * A duration measured between a started and a completed that arrive
//     back to back (a resume snapshot replays both in one write, or a
//     thought too short to time) is not a measurement. It must not be
//     recorded as ~0 ms, and it must not overwrite a duration this page
//     already measured live before the stream dropped and was resumed.
//   * message_complete / waiting_user end the turn: is_thinking goes off
//     there too, not only on cancel / error (FIX116 covered those).
//   * The qwen shape B1 produces lands in reasoning_content, never in the
//     answer.
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
    listMessages: vi.fn(),
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

import { useChatStore } from "./chatStore";

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
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
  };
}
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function conv(id: string): Conversation {
  return { id, title: "T", model_id: "qwen3.6-35b-a3b", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
const answered: Message[] = [
  { id: "u0", role: "user", content: "earlier", status: "completed", created_at: "2026-01-01T00:00:00Z" },
  { id: "a0", role: "assistant", content: "earlier", status: "completed", created_at: "2026-01-01T00:00:00Z" } as Message,
];
function seed(messages: Message[]) {
  useChatStore.setState({ conversations: [conv("c1")], messages: { c1: messages }, messagesLoaded: { c1: true } });
}
const last = () => (useChatStore.getState().messages["c1"] ?? []).at(-1);

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    setTimeout(() => cb(0), 0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
  mockApi.listMessages.mockResolvedValue({ messages: [], has_more: false });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FIX130 B2 — thinking duration is a measurement or nothing", () => {
  it("a started/completed pair that arrives back to back records no duration", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seed(answered);
    const done = useChatStore.getState().sendMessage("c1", "2+3");
    await tick();
    s.push(frame("analysis_status", { status: "started" }) + frame("reasoning_delta", { delta: "2 + 3 = 5" }) + frame("analysis_status", { status: "completed" }));
    await tick();
    expect(last()?.reasoning_content).toBe("2 + 3 = 5");
    expect(last()?.thinking_duration_ms).toBeUndefined();
    s.push(frame("content_delta", { delta: "5" }) + frame("message_complete", { message_id: "a1" }));
    s.close();
    await done;
  });

  it("a live-timed thought keeps its duration", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seed(answered);
    const done = useChatStore.getState().sendMessage("c1", "2+3");
    await tick();
    s.push(frame("analysis_status", { status: "started" }) + frame("reasoning_delta", { delta: "thinking" }));
    await sleep(160);
    s.push(frame("analysis_status", { status: "completed" }));
    await tick();
    expect(last()?.thinking_duration_ms ?? 0).toBeGreaterThanOrEqual(100);
    s.push(frame("message_complete", { message_id: "a1" }));
    s.close();
    await done;
  });

  it("a resume that replays the finished thinking phase keeps the duration measured live", async () => {
    // The row this page was streaming when the connection dropped: it had
    // already timed the thought (12.3 s).
    seed([
      ...answered,
      { id: "u1", role: "user", content: "2+3", status: "completed", created_at: "2026-01-01T00:00:01Z" },
      { id: "a1", role: "assistant", content: "5", status: "streaming", isStreaming: false, created_at: "2026-01-01T00:00:02Z", reasoning_content: "step one", thinking_duration_ms: 12300 } as Message,
    ]);
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().resumeStream("c1", "a1");
    await tick();
    // chat-edge writeResumeSnapshot: started → reasoning → completed → content, in one write.
    s.push(
      frame("analysis_status", { status: "started" }) +
        frame("reasoning_delta", { delta: "step one" }) +
        frame("analysis_status", { status: "completed" }) +
        frame("content_delta", { delta: "5" }) +
        frame("message_complete", { message_id: "a1" }),
    );
    s.close();
    await done;
    expect(last()).toMatchObject({ id: "a1", status: "completed", reasoning_content: "step one", content: "5", is_thinking: false });
    expect(last()?.thinking_duration_ms).toBe(12300);
  });
});

describe("FIX130 B2 — a finished turn is never still thinking", () => {
  it("message_complete without analysis_status: completed turns is_thinking off", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seed(answered);
    const done = useChatStore.getState().sendMessage("c1", "2+3");
    await tick();
    s.push(frame("analysis_status", { status: "started" }) + frame("reasoning_delta", { delta: "step one" }));
    await tick();
    expect(last()?.is_thinking).toBe(true);
    s.push(frame("message_complete", { message_id: "a1" }));
    s.close();
    await done;
    expect(last()).toMatchObject({ status: "completed", isStreaming: false, is_thinking: false, reasoning_content: "step one" });
  });

  it("message_paused (a question card parks the turn) turns is_thinking off", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seed(answered);
    const done = useChatStore.getState().sendMessage("c1", "plan my trip");
    await tick();
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("analysis_status", { status: "started" }) + frame("reasoning_delta", { delta: "which city?" }));
    await tick();
    expect(last()?.is_thinking).toBe(true);
    s.push(frame("message_paused", { message_id: "a1" }));
    s.close();
    await done;
    expect(last()).toMatchObject({ status: "waiting_user", isStreaming: false, is_thinking: false, reasoning_content: "which city?" });
  });
});

describe("FIX130 B2 — qwen thinking (B1) lands in the thinking box", () => {
  it("reasoning_delta frames bracketed by analysis_status go to reasoning_content; the answer is only the answer", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seed(answered);
    const done = useChatStore.getState().sendMessage("c1", "ตอบสั้นๆ: 2 บวก 3 เท่ากับเท่าไร");
    await tick();
    s.push(frame("analysis_status", { status: "started" }));
    s.push(frame("reasoning_delta", { delta: "Here's a thinking process:\n\n1.  **Analyze User Input:** " }));
    s.push(frame("reasoning_delta", { delta: "2 + 3 = 5.\n2.  **Output:** 5 ครับ✅\n" }));
    await sleep(120);
    s.push(frame("analysis_status", { status: "completed" }));
    s.push(frame("content_delta", { delta: "5 ครับ" }));
    s.push(frame("message_complete", { message_id: "a1" }));
    s.close();
    await done;
    const row = last();
    expect(row?.content).toBe("5 ครับ");
    expect(row?.reasoning_content).toContain("Here's a thinking process");
    expect(row?.reasoning_content).toContain("**Output:** 5 ครับ");
    expect(row?.content).not.toContain("thinking process");
    expect(row?.is_thinking).toBe(false);
  });
});

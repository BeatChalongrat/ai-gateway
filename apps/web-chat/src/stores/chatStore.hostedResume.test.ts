// FIX42 C4 — the hosted (desktop) resume probe must release the streaming
// slot when the replayed run ends.
//
// Found on the installed build, not in a test: kill the app mid-turn,
// relaunch, open the conversation. The host replays the frames it kept on
// disk (FIX41 Part F) and closes the run with error/generation_interrupted.
// The bubble rendered the partial answer and the interruption notice — and
// the composer read "Streaming response…" with the Stop button, forever,
// because hostedResumeProbe's finally cleared abortControllers but not
// streamingAssistantId (resumeStream's finally clears both).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    listMessages: vi.fn(),
    openResumeStream: vi.fn(),
    getConversation: vi.fn(),
  },
  mockBridge: { seq: 0, peekSeq: vi.fn(() => mockBridge.seq) },
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
vi.mock("@/lib/auth", async (importOriginal) => ({ ...(await importOriginal<object>()), HOSTED_MODE: true }));
vi.mock("@/lib/bridge", () => ({ bridge: mockBridge }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function conv(id: string): Conversation {
  return { id, title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
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

const until = async (pred: () => boolean, ms = 3000) => {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 20));
  }
};

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv("c1")] }, true);
  vi.clearAllMocks();
  mockBridge.seq = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX42 — hosted resume probe releases the composer", () => {
  it("a replayed run that ends in error/generation_interrupted leaves no streaming slot behind", async () => {
    const s = makeStream();
    mockApi.openResumeStream.mockImplementation(async () => {
      // The host replays: the bridge's seq cursor moves the moment frames
      // arrive, which is what the probe uses to tell "a turn replayed".
      mockBridge.seq = 3;
      return s.response;
    });

    useChatStore.getState().hostedResumeProbe("c1");
    await until(() => Boolean(useChatStore.getState().streamingAssistantId["c1"]));
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeTruthy();

    s.push(frame("message_start", { run_id: "r", seq: 1, message_id: "m1", conversation_id: "c1" }));
    s.push(frame("content_delta", { run_id: "r", seq: 2, delta: "ครึ่งเดียว" }));
    s.push(frame("error", { run_id: "r", seq: 3, final_seq: 3, code: "generation_interrupted", message: "interrupted" }));
    s.close();

    await until(() => !useChatStore.getState().streamingAssistantId["c1"]);
    const st = useChatStore.getState();
    expect(st.streamingAssistantId["c1"]).toBeUndefined();
    expect(st.abortControllers["c1"]).toBeUndefined();
    const asst = (st.messages["c1"] ?? []).find((m) => m.role === "assistant");
    expect(asst?.status).toBe("error");
    expect(asst?.isStreaming).toBe(false);
    expect(asst?.content).toBe("ครึ่งเดียว");
  });
});

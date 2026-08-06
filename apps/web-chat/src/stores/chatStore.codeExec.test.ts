// Phase 8 Task 2 — the `code_execution` SSE handler in consumeStream.
//
// The backend emits TWO frames per run_python call under the same
// tool_call id (running → terminal). The store must collapse them into
// ONE card, keep several concurrent runs distinct and in order, and
// never route a frame to the wrong message. Same harness shape as
// chatStore.stream.test.ts.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBumpUsage } = vi.hoisted(() => ({
  mockApi: {
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

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

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
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
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

function messagesOf(id: string): Message[] {
  return useChatStore.getState().messages[id] ?? [];
}

let rafQueue: Map<number, FrameRequestCallback>;
let rafId: number;

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  rafQueue = new Map();
  rafId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = rafId++;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => rafQueue.delete(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  useChatStore.setState({
    conversations: [conv("c1")],
    messages: { c1: [] },
    messagesLoaded: { c1: true },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("code_execution SSE handling", () => {
  it("collapses the running + terminal frames of one call into a single card", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);

    const done = useChatStore.getState().sendMessage("c1", "compute 6*7");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    await tick();

    s.push(
      frame("code_execution", {
        id: "call_a",
        status: "running",
        code: "print(6*7)",
      }),
    );
    await tick();

    let runs = messagesOf("c1")[1].code_runs ?? [];
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({ status: "running", code: "print(6*7)" });

    s.push(
      frame("code_execution", {
        id: "call_a",
        status: "completed",
        code: "print(6*7)",
        stdout: "42\n",
        exit_code: 0,
        duration_ms: 900,
      }),
    );
    await tick();

    runs = messagesOf("c1")[1].code_runs ?? [];
    expect(runs).toHaveLength(1); // updated in place, not appended
    expect(runs[0]).toMatchObject({ status: "completed", stdout: "42\n" });

    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;

    // The card survives the terminal frame — message_complete must not
    // wipe the runs off the message.
    expect(messagesOf("c1")[1].code_runs).toHaveLength(1);
  });

  it("keeps multiple runs in the order they started", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);

    const done = useChatStore.getState().sendMessage("c1", "two runs");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    await tick();

    s.push(frame("code_execution", { id: "a", status: "running", code: "1" }));
    s.push(frame("code_execution", { id: "b", status: "running", code: "2" }));
    await tick();
    s.push(
      frame("code_execution", { id: "a", status: "completed", code: "1", stdout: "one" }),
    );
    await tick();

    const runs = messagesOf("c1")[1].code_runs ?? [];
    expect(runs.map((r) => r.id)).toEqual(["a", "b"]);
    expect(runs[0].stdout).toBe("one");
    expect(runs[1].status).toBe("running");

    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
  });

  it("leaves turns that never ran code without a code_runs array", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;

    expect(messagesOf("c1")[1].code_runs).toBeUndefined();
  });
});

// FIX121 Part C — the page's half of "one action, one turn" and of "a failed
// turn keeps what was tried".
//
//   C3  sendMessage mints the send's id BEFORE the request leaves and hands
//       it to the transport (it becomes X-Request-Id: the run id and the
//       server's idempotency key). Enter + click inside one commit used to
//       POST two turns that both persisted; the second call is now dropped
//       while the first has no response yet — and ONLY then.
//   C5  a row read back from the server with `error_attempts` reaches the
//       store as it came: the "Connection attempts" list survives a reload
//       with no mapper in the way.
//
// The api module is mocked; the real parseSSE runs against a hand-fed stream
// (the chatStore.stream.test.ts pattern).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    listMessages: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
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
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    finish(messageId: string) {
      controller.enqueue(enc.encode(frame("message_start", { message_id: messageId, conversation_id: "c1" })));
      controller.enqueue(enc.encode(frame("content_delta", { delta: "ok" })));
      controller.enqueue(enc.encode(frame("message_complete", { message_id: messageId })));
      controller.close();
    },
  };
}

function conv(id: string): Conversation {
  return { id, title: "T", model_id: "claude-sonnet-4", message_count: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
function seed(id: string, messages: Message[] = []) {
  useChatStore.setState({ conversations: [conv(id)], messages: { [id]: messages }, messagesLoaded: { [id]: true } });
}
const rows = (id: string) => useChatStore.getState().messages[id] ?? [];
const requestIdOf = (call: number) => mockApi.openMessageStream.mock.calls[call][7] as string;

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX121 C3 — the send names itself before it leaves", () => {
  it("hands the transport a fresh UUID per send", async () => {
    seed("c1");
    const a = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(a.response);
    const first = useChatStore.getState().sendMessage("c1", "one");
    await tick();
    a.finish("m1");
    await first;

    const b = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(b.response);
    const second = useChatStore.getState().sendMessage("c1", "two");
    await tick();
    b.finish("m2");
    await second;

    expect(mockApi.openMessageStream).toHaveBeenCalledTimes(2);
    expect(requestIdOf(0)).toMatch(UUID);
    expect(requestIdOf(1)).toMatch(UUID);
    expect(requestIdOf(0)).not.toBe(requestIdOf(1)); // a new action is a new key, never a replay
  });

  it("Enter + click in one tick is ONE request and one pair of rows", async () => {
    seed("c1");
    const a = makeStream();
    mockApi.openMessageStream.mockResolvedValue(a.response);
    const store = useChatStore.getState();
    const first = store.sendMessage("c1", "same keystroke");
    const dup = store.sendMessage("c1", "same keystroke"); // the second handler of the same commit
    await tick();

    expect(mockApi.openMessageStream).toHaveBeenCalledTimes(1);
    expect(rows("c1").filter((m) => m.role === "user")).toHaveLength(1);
    expect(rows("c1").filter((m) => m.role === "assistant")).toHaveLength(1);
    // the surviving turn was not relabelled "cancelled" by its own echo
    expect(rows("c1")[1].status).not.toBe("cancelled");

    a.finish("m1");
    await Promise.all([first, dup]);
    expect(rows("c1")[1]).toMatchObject({ id: "m1", status: "completed", content: "ok" });
  });

  it("the guard covers only the wait for the response: another conversation, and the next send here, go through", async () => {
    useChatStore.setState({ conversations: [conv("c1"), conv("c2")], messages: { c1: [], c2: [] }, messagesLoaded: { c1: true, c2: true } });
    const a = makeStream();
    const b = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(a.response).mockResolvedValueOnce(b.response);
    const s = useChatStore.getState();
    const p1 = s.sendMessage("c1", "to c1");
    const p2 = s.sendMessage("c2", "to c2"); // same tick, different conversation
    await tick();
    expect(mockApi.openMessageStream).toHaveBeenCalledTimes(2);
    a.finish("m1");
    b.finish("m2");
    await Promise.all([p1, p2]);
  });

  it("a send that never got a response does not leave the conversation refusing the next one", async () => {
    seed("c1");
    mockApi.openMessageStream.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await useChatStore.getState().sendMessage("c1", "lost to the network");

    const b = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(b.response);
    const again = useChatStore.getState().sendMessage("c1", "again");
    await tick();
    expect(mockApi.openMessageStream).toHaveBeenCalledTimes(2);
    b.finish("m2");
    await again;
  });
});

describe("FIX121 C5 — the attempt log comes back with the row", () => {
  it("loadMessages keeps error_attempts on a persisted failed turn", async () => {
    useChatStore.setState({ conversations: [conv("c1")] });
    const attempts = [
      { provider: "sranai", model: "qwen3.8-27b-fp8", error_code: "provider_timeout" },
      { provider: "openai", model: "gpt-5-mini", error_code: "provider_no_credit" },
    ];
    mockApi.listMessages.mockResolvedValue({
      has_more: false,
      messages: [
        { id: "u1", role: "user", content: "q", status: "completed", created_at: "2026-09-17T09:00:00Z" },
        { id: "a1", role: "assistant", content: "", status: "error", created_at: "2026-09-17T09:00:01Z", error_code: "all_models_failed", terminal_cause: "error", run_id: "8f1d2c3b-4e5f-4a6b-9c7d-0e1f2a3b4c5d", error_attempts: attempts },
      ],
    });
    await useChatStore.getState().loadMessages("c1");
    const failed = rows("c1").find((m) => m.id === "a1");
    expect(failed?.error_attempts).toEqual(attempts);
    expect(failed?.error_code).toBe("all_models_failed");
  });
});

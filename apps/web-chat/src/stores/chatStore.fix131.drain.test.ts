// FIX131 E2 — a chat-edge restart does not lose the person's message.
//
// While chat-edge drains it answers a new turn 503 SERVER_DRAINING +
// Retry-After and writes nothing. The page waits (the row says the service
// is restarting), sends the SAME request again — same X-Request-Id, the
// server's idempotency key — and the turn runs normally once the new
// process is up. A connection refused in between (the old process exited,
// the new one is not listening yet) is part of the same restart. When the
// wait runs out, nothing was sent: the rows go, the text is back in the
// composer and a notice says to send again.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openRegenerateStream: vi.fn(),
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

import { clearDraftStore, loadDraft } from "@/lib/draftStore";
import { tOutside } from "@/lib/i18n";
import { clearOutbox, outboxPeek } from "@/lib/sendOutbox";

import { DRAIN_RETRY, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const savedRetry = { ...DRAIN_RETRY };
// (Guarded so the red run on a page without FIX131 fails on behaviour, not on
// the missing export.)
const tune = (v: Partial<typeof DRAIN_RETRY>) => DRAIN_RETRY && Object.assign(DRAIN_RETRY, v);

function conv(id: string): Conversation {
  return { id, title: "t", model_id: "m", message_count: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
const earlier: Message[] = [
  { id: "u0", role: "user", content: "ก่อนหน้า", status: "completed", created_at: "2026-01-01T00:00:00Z" } as Message,
  { id: "a0", role: "assistant", content: "ตอบแล้ว", status: "completed", created_at: "2026-01-01T00:00:01Z" } as Message,
];

const draining = () =>
  new Response(JSON.stringify({ error: { code: "SERVER_DRAINING", message: "the chat service is restarting" } }), {
    status: 503,
    headers: { "Content-Type": "application/json", "Retry-After": "5" },
  });

function sse(frames: [string, unknown][]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const [event, data] of frames) c.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const answer = () =>
  sse([
    ["message_start", { run_id: "r1", seq: 1, message_id: "a1", user_message_id: "u1", conversation_id: "c1" }],
    ["content_delta", { run_id: "r1", seq: 2, delta: "ตอบหลังรีสตาร์ต" }],
    ["message_complete", { run_id: "r1", seq: 3, final_seq: 3, message_id: "a1" }],
  ]);

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  tune({ firstMs: 5, maxMs: 20, budgetMs: 2_000 });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.listMessages.mockResolvedValue({ messages: [], has_more: false });
  useChatStore.setState({ conversations: [conv("c1")], messages: { c1: [...earlier] }, messagesLoaded: { c1: true } });
});
afterEach(() => {
  tune(savedRetry);
  vi.unstubAllGlobals();
});

describe("FIX131 E2 — a send during a chat-edge restart", () => {
  it("waits out 503 SERVER_DRAINING and a refused connection, then sends the SAME request and the turn completes", async () => {
    let waitingSeen = false;
    mockApi.openMessageStream
      .mockImplementationOnce(async () => draining())
      .mockImplementationOnce(async () => {
        // the row says the service is restarting while it waits
        waitingSeen = useChatStore.getState().messages.c1.some((m) => m.role === "assistant" && m.restart_wait);
        throw new TypeError("Failed to fetch"); // old process gone, new one not up
      })
      .mockImplementationOnce(async () => draining())
      .mockImplementationOnce(async () => answer());

    await useChatStore.getState().sendMessage("c1", "ส่งระหว่างรีสตาร์ต");

    expect(mockApi.openMessageStream).toHaveBeenCalledTimes(4);
    const ids = mockApi.openMessageStream.mock.calls.map((c) => c[7]);
    expect(new Set(ids).size).toBe(1); // one request id: the idempotency key
    expect(ids[0]).toBeTruthy();
    expect(waitingSeen).toBe(true);

    const s = useChatStore.getState();
    const asst = s.messages.c1.find((m) => m.id === "a1");
    expect(asst?.content).toBe("ตอบหลังรีสตาร์ต");
    expect(asst?.status).toBe("completed");
    expect(asst?.restart_wait).toBeFalsy();
    expect(s.errorBanner).toBeFalsy();
    expect(outboxPeek("c1")).toBeNull(); // the server took it
  });

  it("when the restart outlasts the wait: nothing was sent — the rows go, the text is back, a notice says to send again", async () => {
    tune({ firstMs: 5, maxMs: 10, budgetMs: 60 });
    mockApi.openMessageStream.mockImplementation(async () => draining());

    await useChatStore.getState().sendMessage("c1", "ยังรีสตาร์ตไม่เสร็จ");

    expect(mockApi.openMessageStream.mock.calls.length).toBeGreaterThan(1);
    const s = useChatStore.getState();
    expect(s.messages.c1.map((m) => m.id)).toEqual(["u0", "a0"]);
    expect(s.returnedDrafts.c1?.text).toBe("ยังรีสตาร์ตไม่เสร็จ");
    expect(loadDraft("c1")).toBe("ยังรีสตาร์ตไม่เสร็จ");
    expect(s.errorBanner).toBe(tOutside("error.serverDraining"));
    expect(s.streamingAssistantId.c1).toBeUndefined();
  });

  it("a refused connection with no restart in progress still fails at once (no silent retry of an unknown failure)", async () => {
    mockApi.openMessageStream.mockImplementationOnce(async () => {
      throw new TypeError("Failed to fetch");
    });
    await useChatStore.getState().sendMessage("c1", "เน็ตหลุด");
    expect(mockApi.openMessageStream).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().returnedDrafts.c1?.text).toBe("เน็ตหลุด");
  });

  it("regenerate also waits out the restart", async () => {
    mockApi.openRegenerateStream
      .mockImplementationOnce(async () => draining())
      .mockImplementationOnce(async () => answer());
    await useChatStore.getState().regenerate("c1", "a0");
    expect(mockApi.openRegenerateStream).toHaveBeenCalledTimes(2);
    const asst = useChatStore.getState().messages.c1.find((m) => m.id === "a1");
    expect(asst?.status).toBe("completed");
  });
});

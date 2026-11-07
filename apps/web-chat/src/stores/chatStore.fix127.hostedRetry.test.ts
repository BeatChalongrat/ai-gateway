// FIX127 C4 — the desktop resumes on its own when the network comes back.
//
// R3 / e49: a 5/30/60 s network drop on the desktop left an "interrupted"
// card with a Retry button and nothing else happened when the network
// returned, while the web reconnects by itself. The desktop has no
// server-side run to reconnect to (the host ended it), so its resume is a
// retry: the row shows "reconnecting", waits for `online`, and regenerates
// once the network is back — at most HOSTED_RETRY_MAX times per row.

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
vi.mock("@/lib/auth", async (importOriginal) => ({ ...(await importOriginal<object>()), HOSTED_MODE: true }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { _resetHostedRetries, HOSTED_RETRY_MAX, hostedRetrySchedule, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const settle = async (n = 6) => { for (let i = 0; i < n; i++) await tick(); };
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (event: string, data: unknown) => controller.enqueue(enc.encode(frame(event, data))),
    close: () => controller.close(),
  };
}
const conv = (): Conversation => ({ id: "c1", title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
const rows = () => useChatStore.getState().messages.c1 ?? [];
const originalWait = hostedRetrySchedule.waitFor;

/** A wake the test releases by hand ("the network is back"). */
function manualWake() {
  const wakes: Array<() => void> = [];
  hostedRetrySchedule.waitFor = () => new Promise<void>((r) => wakes.push(r));
  return { release: () => wakes.splice(0).forEach((r) => r()), pending: () => wakes.length };
}

async function interruptedTurn() {
  const s = makeStream();
  mockApi.openMessageStream.mockResolvedValueOnce(s.response);
  const sending = useChatStore.getState().sendMessage("c1", "hello");
  await tick();
  s.push("message_start", { message_id: "a1", conversation_id: "c1" });
  s.push("content_delta", { delta: "half" });
  s.push("error", { code: "stream_interrupted", message: "cut", cause: "disconnected", retryable: true });
  s.close();
  await sending;
  await settle(3);
}

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv()], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  vi.clearAllMocks();
  _resetHostedRetries();
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  hostedRetrySchedule.waitFor = originalWait;
  vi.unstubAllGlobals();
});

describe("FIX127 C4 — desktop auto-retry after a network drop", () => {
  it("a disconnected turn waits (reconnecting) and regenerates itself when the network is back", async () => {
    const wake = manualWake();
    await interruptedTurn();

    const last = rows()[rows().length - 1];
    expect(last.status).toBe("error");
    expect(last.terminal_cause).toBe("disconnected");
    expect(last.reconnecting).toBe(true);
    expect(mockApi.openRegenerateStream).not.toHaveBeenCalled();
    expect(wake.pending()).toBe(1);

    const regen = makeStream();
    mockApi.openRegenerateStream.mockResolvedValueOnce(regen.response);
    wake.release(); // "online"
    await settle(4);

    expect(mockApi.openRegenerateStream).toHaveBeenCalledTimes(1);
    expect(mockApi.openRegenerateStream.mock.calls[0][0]).toBe("c1");
    expect(mockApi.openRegenerateStream.mock.calls[0][1]).toBe("a1");
    regen.push("message_start", { message_id: "a2", conversation_id: "c1" });
    regen.push("content_delta", { delta: "full answer" });
    regen.push("message_complete", { message_id: "a2" });
    regen.close();
    await settle(4);
    const done = rows()[rows().length - 1];
    expect(done.status).toBe("completed");
    expect(done.reconnecting).toBeUndefined();
  });

  it("stands down when the network is back but the row is no longer the failed last reply", async () => {
    const wake = manualWake();
    await interruptedTurn();
    // The person already pressed Retry / sent again: a newer row exists.
    useChatStore.setState((s) => ({ messages: { ...s.messages, c1: [...(s.messages.c1 ?? []), { id: "u2", role: "user", content: "again", status: "completed", created_at: "2026-01-01T00:00:00Z" } as Message] } }));
    wake.release();
    await settle(4);
    expect(mockApi.openRegenerateStream).not.toHaveBeenCalled();
  });

  it("a plain failure (not a lost link) does not arm a retry", async () => {
    const wake = manualWake();
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const sending = useChatStore.getState().sendMessage("c1", "hello");
    await tick();
    s.push("message_start", { message_id: "a1", conversation_id: "c1" });
    s.push("error", { code: "all_models_failed", message: "down", cause: "error" });
    s.close();
    await sending;
    await settle(3);
    expect(rows()[rows().length - 1].reconnecting).toBeUndefined();
    expect(wake.pending()).toBe(0);
  });

  it("gives up after HOSTED_RETRY_MAX attempts on the same row", () => {
    expect(HOSTED_RETRY_MAX).toBe(3);
  });
});

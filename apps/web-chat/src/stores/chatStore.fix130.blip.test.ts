// FIX130 C1 — a network blip must not hang the web client.
//
// NIGHT2 N1: an intermediary reset the SSE socket 6 s into a turn; Chromium's
// reader.read() never settled, so neither the rejection path nor the clean-EOF
// path that lead to resumeStream was ever entered. The server finished the
// answer (5,733 chars); the page sat on "กำลังประมวลผล…" until a reload.
//
// The stream here is a hand-fed ReadableStream whose reads simply stop
// resolving — exactly the half-open socket — and fake timers drive the clock.
// Pins:
//   1. silence past the read deadline → the page reattaches from its cursor
//      and shows the finished answer without a reload;
//   2. the server's ": ping" every 15 s keeps a long quiet turn attached
//      (long thinking / long tool run is never cut);
//   3. the browser saying it is back online reattaches a silent stream at once;
//   4. a regenerate that drops reattaches too (it used to fail the turn);
//   5. (C3.2) a send whose request never answers fails into the retry card
//      instead of spinning.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openRegenerateStream: vi.fn(),
    openResumeStream: vi.fn(),
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

import { clearDraftStore } from "@/lib/draftStore";
import { clearOutbox } from "@/lib/sendOutbox";

import { RESUME_RETRY_DELAYS, clearStreamCursor, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const savedDelays = [...RESUME_RETRY_DELAYS];
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);

/** A response body the test feeds by hand. After `cut()` nothing more ever
 *  arrives and nothing ever fails: the half-open socket. An abort of the
 *  request's own signal errors it, as fetch does. */
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let cut = false;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (raw: string) => { if (!cut) controller.enqueue(enc.encode(raw)); },
    cut: () => { cut = true; },
    close: () => controller.close(),
    wireAbort(signal: AbortSignal) {
      signal.addEventListener("abort", () => {
        try { controller.error(new DOMException("The user aborted.", "AbortError")); } catch { /* closed */ }
      });
    },
  };
}

const conv = (): Conversation =>
  ({ id: "c1", title: "T", model_id: "deepseek-v4-flash", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
function seed(messages: Message[] = []) {
  useChatStore.setState({ conversations: [conv()], messages: { c1: messages }, messagesLoaded: { c1: true } });
}
const rowById = (id: string) => (useChatStore.getState().messages.c1 ?? []).find((m) => m.id === id);

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  clearStreamCursor("c1");
  vi.useFakeTimers();
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, 0, 1000, 2000, 4000);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv());
  mockApi.generateTitle.mockResolvedValue({ title: "t", source: "model" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: false });
  mockApi.listMessages.mockResolvedValue({ messages: [], has_more: false });
});
afterEach(() => {
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...savedDelays);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Starts a send whose stream gets message_start + the first words. */
async function startTurn() {
  const s = makeStream();
  mockApi.openMessageStream.mockImplementation(async (_c: string, _t: string, signal: AbortSignal) => {
    s.wireAbort(signal);
    return s.response;
  });
  seed();
  const sending = useChatStore.getState().sendMessage("c1", "สวัสดี", []);
  await advance(0);
  s.push(frame("message_start", { message_id: "srv-1", conversation_id: "c1", run_id: "run-1", seq: 1 }));
  s.push(frame("content_delta", { delta: "ส่วนแรก ", run_id: "run-1", seq: 2 }));
  await advance(0);
  return { s, sending };
}

/** The resume endpoint: the rest of the answer from the cursor on, then the terminal. */
function resumeServes() {
  const r = makeStream();
  mockApi.openResumeStream.mockImplementation(async (_c: string, _m: string, signal: AbortSignal) => {
    r.wireAbort(signal);
    queueMicrotask(() => {
      r.push(frame("content_delta", { delta: "ส่วนที่เหลือ", run_id: "run-1", seq: 3 }));
      r.push(frame("message_complete", { message_id: "srv-1", run_id: "run-1", seq: 4 }));
      r.close();
    });
    return r.response;
  });
  return r;
}

describe("FIX130 C1 — a silent (half-open) connection is reattached, never waited on forever", () => {
  it("5-second blip mid-answer: past the read deadline the page reattaches from its cursor and shows the finished answer — no reload", async () => {
    const { s, sending } = await startTurn();
    resumeServes();
    s.cut(); // the socket is gone; the read never settles

    await advance(30_000);
    expect(mockApi.openResumeStream).not.toHaveBeenCalled(); // not before the deadline

    await advance(20_000);
    await sending;
    await advance(0);

    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(1);
    // the cursor: only what was missed (seq > 2) is asked for
    expect(mockApi.openResumeStream).toHaveBeenCalledWith("c1", "srv-1", expect.anything(), 2);
    const row = rowById("srv-1");
    expect(row?.status).toBe("completed");
    expect(row?.content).toBe("ส่วนแรก ส่วนที่เหลือ");
    expect(row?.error_code).toBeUndefined();
    expect(mockApi.listMessages).not.toHaveBeenCalled(); // no reload was needed
  });

  it("the server's 15 s pings keep a 3-minute quiet stretch (long thinking / tool run) attached", async () => {
    const { s, sending } = await startTurn();
    for (let i = 0; i < 12; i++) {
      await advance(15_000);
      s.push(": ping\n\n");
      await advance(0);
    }
    expect(mockApi.openResumeStream).not.toHaveBeenCalled();
    expect(rowById("srv-1")?.status).toBe("streaming");
    s.push(frame("message_complete", { message_id: "srv-1", run_id: "run-1", seq: 3 }));
    s.close();
    await advance(0);
    await sending;
    expect(rowById("srv-1")?.status).toBe("completed");
  });

  it("the browser coming back online reattaches a silent stream at once, not at the deadline", async () => {
    const { s, sending } = await startTurn();
    resumeServes();
    s.cut();
    await advance(5_000);
    window.dispatchEvent(new Event("online"));
    await advance(0);
    await sending;
    await advance(0);
    expect(mockApi.openResumeStream).toHaveBeenCalledTimes(1);
    expect(rowById("srv-1")?.status).toBe("completed");
  });

  it("online while bytes are flowing does not tear a healthy stream down", async () => {
    const { s, sending } = await startTurn();
    await advance(1_000);
    s.push(frame("content_delta", { delta: "ต่อ", run_id: "run-1", seq: 3 }));
    await advance(0);
    window.dispatchEvent(new Event("online"));
    await advance(0);
    expect(mockApi.openResumeStream).not.toHaveBeenCalled();
    s.push(frame("message_complete", { message_id: "srv-1", run_id: "run-1", seq: 4 }));
    s.close();
    await advance(0);
    await sending;
    expect(rowById("srv-1")?.status).toBe("completed");
  });

  it("a regenerate whose connection goes silent reattaches instead of failing the turn", async () => {
    seed([
      { id: "u1", role: "user", status: "completed", content: "q", created_at: "2026-09-23T00:00:00Z" } as Message,
      { id: "a1", role: "assistant", status: "completed", content: "old", created_at: "2026-09-23T00:00:01Z" } as Message,
    ]);
    const s = makeStream();
    mockApi.openRegenerateStream.mockImplementation(async (_c: string, _m: string, signal: AbortSignal) => {
      s.wireAbort(signal);
      return s.response;
    });
    resumeServes();
    const regen = useChatStore.getState().regenerate("c1", "a1");
    await advance(0);
    s.push(frame("message_start", { message_id: "srv-1", conversation_id: "c1", run_id: "run-1", seq: 1 }));
    s.push(frame("content_delta", { delta: "ส่วนแรก ", run_id: "run-1", seq: 2 }));
    await advance(0);
    s.cut();
    await advance(50_000);
    await regen;
    await advance(0);
    expect(mockApi.openResumeStream).toHaveBeenCalledWith("c1", "srv-1", expect.anything(), 2);
    const row = rowById("srv-1");
    expect(row?.status).toBe("completed");
    expect(row?.content).toBe("ส่วนแรก ส่วนที่เหลือ");
  });
});

describe("FIX130 C3.2 — a send nothing answers fails into the retry card", () => {
  it("no response to the send within the open deadline → provider_timeout error card, chat-edge told to stop, never 'cancelled'", async () => {
    mockApi.openMessageStream.mockImplementation(
      (_c: string, _t: string, signal: AbortSignal) =>
        new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))),
    );
    seed();
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี", []);
    await advance(60_000);
    const pending = (useChatStore.getState().messages.c1 ?? []).find((m) => m.role === "assistant");
    expect(pending?.status).toBe("streaming"); // still waiting at 60 s
    await advance(45_000);
    await sending;
    const row = (useChatStore.getState().messages.c1 ?? []).find((m) => m.role === "assistant");
    expect(row?.status).toBe("error");
    expect(row?.error_code).toBe("provider_timeout");
    expect(row?.error_retryable).toBe(true);
    expect(mockApi.cancelGeneration).toHaveBeenCalledWith("c1");
  });
});

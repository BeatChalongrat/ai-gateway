// FIX132 N1 — a lost reply was blamed on the AI provider and offered a paid
// retry, although the answer had been saved. NIGHT3 c32: the proxy forwarded
// the page's send but passed no byte back; at 105 s the card said
// "ผู้ให้บริการ AI ตอบสนองช้าเกินไป" with regenerate, and chat-edge was told to
// cancel — while the server row was `completed` (37 chars) and a reload
// showed the answer. Now, when the send gets no response in time, the page
// asks the server what happened to THIS send (its run id is on the user
// row) before declaring anything:
//   · answered → the saved answer is shown, no card, nothing cancelled;
//   · still running → the page attaches to it;
//   · never arrived / server unreachable → a CONNECTION card (not the
//     provider), the text back in the composer, nothing cancelled.

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


function hangingSend() {
  let requestId = "";
  mockApi.openMessageStream.mockImplementation(
    (_c: string, _t: string, signal: AbortSignal, _f: unknown, _i: unknown, _v: unknown, _m: unknown, rid: string) => {
      requestId = rid;
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    },
  );
  return () => requestId;
}
const serverRows = (rid: string, reply: Partial<Message>): Message[] => [
  { id: "u-srv", role: "user", content: "สวัสดี", status: "completed", created_at: "2026-09-24T00:00:00Z", run_id: rid } as Message,
  { id: "a-srv", role: "assistant", content: "", status: "completed", created_at: "2026-09-24T00:00:01Z", run_id: rid, ...reply } as Message,
];
const assistant = () => (useChatStore.getState().messages.c1 ?? []).filter((m) => m.role === "assistant");

describe("FIX132 N1 — the open deadline asks the server before blaming anyone", () => {
  it("the server has the answer: it is shown, no error card, nothing cancelled, nothing back in the composer", async () => {
    const rid = hangingSend();
    seed();
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี", []);
    await advance(1);
    mockApi.listMessages.mockResolvedValue({ messages: serverRows(rid(), { content: "สวัสดีครับ" }), has_more: false });
    await advance(105_000);
    await sending;
    const rows = useChatStore.getState().messages.c1 ?? [];
    expect(rows.map((m) => m.id)).toEqual(["u-srv", "a-srv"]);
    expect(assistant()[0]).toMatchObject({ status: "completed", content: "สวัสดีครับ" });
    expect(assistant()[0].error_code).toBeUndefined();
    expect(mockApi.cancelGeneration).not.toHaveBeenCalled();
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
    expect(useChatStore.getState().errorBanner).toBeNull();
  });

  it("the server is still answering: the page attaches to that turn", async () => {
    const rid = hangingSend();
    const s = makeStream();
    mockApi.openResumeStream.mockImplementation(async (_c: string, _m: string, signal: AbortSignal) => {
      s.wireAbort(signal);
      return s.response;
    });
    seed();
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี", []);
    await advance(1);
    mockApi.listMessages.mockResolvedValue({ messages: serverRows(rid(), { content: "", status: "streaming" }), has_more: false });
    await advance(105_000);
    s.push(frame("message_start", { message_id: "a-srv", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "ตอบช้าแต่มา" }));
    s.push(frame("message_complete", { message_id: "a-srv" }));
    s.close();
    await sending;
    await advance(10);
    expect(mockApi.openResumeStream).toHaveBeenCalledWith("c1", "a-srv", expect.anything(), expect.anything());
    expect(assistant()[0]).toMatchObject({ id: "a-srv", status: "completed", content: "ตอบช้าแต่มา" });
    expect(mockApi.cancelGeneration).not.toHaveBeenCalled();
  });

  it("the send never arrived: a connection card, not the AI provider; the text is back; nothing cancelled", async () => {
    hangingSend();
    seed();
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี", []);
    await advance(1);
    mockApi.listMessages.mockResolvedValue({ messages: [], has_more: false });
    await advance(105_000);
    await sending;
    const row = assistant()[0];
    expect(row.status).toBe("error");
    expect(row.error_code).toBe("connection_timeout");
    expect(row.error_message).not.toMatch(/AI provider|ผู้ให้บริการ AI/);
    // No regenerate: the server never got this turn (a regenerate would
    // answer the previous one); the text is back in the composer instead.
    expect(row.error_retryable).toBe(false);
    expect(useChatStore.getState().returnedDrafts.c1?.text).toBe("สวัสดี");
    expect(mockApi.cancelGeneration).not.toHaveBeenCalled();
  });

  it("the server cannot be asked either: the same connection card", async () => {
    hangingSend();
    seed();
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี", []);
    await advance(1);
    mockApi.listMessages.mockRejectedValue(new Error("offline"));
    await advance(105_000);
    await sending;
    expect(assistant()[0]).toMatchObject({ status: "error", error_code: "connection_timeout" });
    expect(useChatStore.getState().returnedDrafts.c1?.text).toBe("สวัสดี");
  });
});

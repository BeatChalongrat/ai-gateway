// FIX130 A3b / FIX132 A — the memory frames inside consumeStream. chat-edge
// (and the desktop host, over the bridge, as the same SSE) sends `memory_used`
// once, before the answer, when saved memories went into the prompt, and
// `memory_saved` after each save_memory call that succeeded.
//
// FIX132 A: the page no longer badges an answer for USING memory (owner
// decision — memory works in the background; ChatGPT/Claude show nothing per
// answer), so `memory_used` is left on the wire and in the server's records
// but is not put on the row. `memory_saved` still lands on the assistant row
// so MessageBubble can say "memory saved / suggested" WHILE the answer
// streams — and, because every path (send, reattach, hosted) goes through
// consumeStream, the replayed ledger frames of a reattach land the same way.
// Same mocks and hand-fed SSE stream as chatStore.compaction.test.ts.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBumpUsage, mockPendingRefresh } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    listMessages: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
  },
  mockBumpUsage: vi.fn(),
  mockPendingRefresh: vi.fn(async () => {}),
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
vi.mock("@/hooks/useMemoryPendingCount", () => ({
  useMemoryPendingStore: { getState: () => ({ refresh: mockPendingRefresh }) },
}));

import { RESUME_RETRY_DELAYS, RESUME_WAIT, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const originalDelays = [...RESUME_RETRY_DELAYS];
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
  };
}
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const conv = (id: string): Conversation => ({ id, title: "T", model_id: "claude-sonnet-4", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
const messagesOf = (id: string): Message[] => useChatStore.getState().messages[id] ?? [];

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  vi.clearAllMocks();
  RESUME_WAIT.capMs = 0;
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays.map(() => 0));
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
});
afterEach(() => {
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays);
  vi.unstubAllGlobals();
});

describe("FIX130/FIX132 memory frames on the live stream", () => {
  it("memory_used is not put on the answer — the page shows nothing per answer for using memory", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "แนะนำร้านกาแฟหน่อย");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    s.push(frame("memory_used", { message_id: "real-1", count: 2, items: [{ id: "m1", label: "อยู่เชียงใหม่" }, { id: "m2", label: "ชอบกาแฟดำ" }] }));
    await tick();
    const asst = messagesOf("c1")[1];
    expect(asst.id).toBe("real-1");
    expect(asst.isStreaming).toBe(true);
    expect((asst as unknown as Record<string, unknown>).memory_used).toBeUndefined();

    s.push(frame("content_delta", { delta: "ลองร้านนี้" }));
    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
    const final = messagesOf("c1")[1];
    // The frame is ignored, not treated as trouble: the turn completes normally.
    expect(final.status).toBe("completed");
    expect(final.content).toBe("ลองร้านนี้");
    expect((final as unknown as Record<string, unknown>).memory_used).toBeUndefined();
  });

  it("memory_saved accumulates across frames and a pending one refreshes the suggestion badge", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "จำไว้นะ ผมชื่อบีท แพ้กุ้งด้วย");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    s.push(frame("memory_saved", { message_id: "real-1", items: [{ id: "m1", label: "ชื่อบีท", status: "active" }] }));
    await tick();
    expect(messagesOf("c1")[1].memory_saved).toEqual([{ id: "m1", label: "ชื่อบีท", status: "active" }]);
    expect(mockPendingRefresh).not.toHaveBeenCalled();

    s.push(frame("memory_saved", { message_id: "real-1", items: [{ id: "m2", label: "แพ้กุ้ง", status: "pending_confirmation" }] }));
    await tick();
    expect(messagesOf("c1")[1].memory_saved).toEqual([
      { id: "m1", label: "ชื่อบีท", status: "active" },
      { id: "m2", label: "แพ้กุ้ง", status: "pending_confirmation" },
    ]);
    expect(mockPendingRefresh).toHaveBeenCalled();

    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
  });

  it("a turn that used no memory carries no marker", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "Hi" }));
    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
    expect((messagesOf("c1")[1] as unknown as Record<string, unknown>).memory_used).toBeUndefined();
    expect(messagesOf("c1")[1].memory_saved).toBeUndefined();
  });

  it("a reattach replays the ledger's frames: memory_used is still ignored, memory_saved lands", async () => {
    useChatStore.setState({ messages: {}, messagesLoaded: {} });
    mockApi.listMessages.mockResolvedValue({
      messages: [
        { id: "u1", role: "user", content: "q", status: "completed", created_at: "2026-01-01T00:00:00Z" },
        { id: "m1", role: "assistant", content: "", status: "streaming", created_at: "2026-01-01T00:00:01Z" },
      ],
      has_more: false,
    });
    const s = makeStream();
    mockApi.openResumeStream.mockResolvedValue(s.response);
    await useChatStore.getState().loadMessages("c1");
    await tick();
    s.push(frame("message_start", { message_id: "m1", conversation_id: "c1" }));
    s.push(frame("memory_used", { message_id: "m1", count: 1, items: [{ id: "mem-1", label: "ชื่อเล่นว่าภูผา" }] }));
    s.push(frame("content_delta", { delta: "สวัสดีคุณภูผา" }));
    s.push(frame("memory_saved", { message_id: "m1", items: [{ id: "mem-2", label: "ชอบชาเขียว", status: "active" }] }));
    s.push(frame("message_complete", { message_id: "m1" }));
    s.close();
    await tick();
    await tick();
    const m = messagesOf("c1")[1];
    expect(m.status).toBe("completed");
    expect((m as unknown as Record<string, unknown>).memory_used).toBeUndefined();
    expect(m.memory_saved).toEqual([{ id: "mem-2", label: "ชอบชาเขียว", status: "active" }]);
  });
});

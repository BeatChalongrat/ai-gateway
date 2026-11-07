// FIX33 A-W9 / A-D5 — the compaction events inside consumeStream:
//   compaction_status → the streaming assistant row is "organizing…"
//   (is_compacting), cleared by the first content delta;
//   context_budget (the desktop host's live meter numbers) → stored per
//   conversation for the ContextMeter, which stays hidden below 70 %.
// Same mocks and hand-fed SSE stream as chatStore.stream.test.ts.

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

import { shouldShowMeter } from "@/lib/compaction";
import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
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

let rafQueue: Map<number, FrameRequestCallback>;
let rafId: number;
const fireRaf = () => {
  const cbs = [...rafQueue.values()];
  rafQueue.clear();
  for (const cb of cbs) cb(0);
};

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  vi.clearAllMocks();
  rafQueue = new Map();
  rafId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = rafId++;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => void rafQueue.delete(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX33 compaction events", () => {
  it("compaction_status marks the assistant row 'organizing…' until the first content delta", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "hello");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    s.push(frame("compaction_status", { status: "summarizing" }));
    await tick();
    expect(messagesOf("c1")[1]).toMatchObject({ id: "real-1", is_compacting: true, isStreaming: true });

    s.push(frame("content_delta", { delta: "Hi" }));
    await tick();
    expect(messagesOf("c1")[1].is_compacting).toBe(false);
    fireRaf();
    expect(messagesOf("c1")[1].content).toBe("Hi");

    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
    expect(messagesOf("c1")[1].is_compacting).toBe(false);
    expect(messagesOf("c1")[1].isStreaming).toBe(false);
  });

  it("compaction_status with no delta is still cleared by message_complete", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "hello");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    s.push(frame("compaction_status", { status: "summarizing" }));
    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
    expect(messagesOf("c1")[1].is_compacting).toBe(false);
  });

  it("context_budget (desktop host) lands in contextBudgetByConv; the meter hides below 70 % and shows above", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "hello");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    const low = { used_ratio: 0.31, history_tokens: 31_000, messages_budget: 100_000, threshold_tokens: 85_000, messages_total: 20, messages_sent: 20, state_present: false, compacted: false, dropped: 0, est_messages_remaining: 40, show_meter: false, meter_show_ratio: 0.7 };
    s.push(frame("context_budget", low));
    await tick();
    expect(useChatStore.getState().contextBudgetByConv["c1"]).toMatchObject({ used_ratio: 0.31 });
    expect(shouldShowMeter(useChatStore.getState().contextBudgetByConv["c1"])).toBe(false);
    s.push(frame("context_budget", { ...low, used_ratio: 0.88, show_meter: true, compacted: true, dropped: 30 }));
    await tick();
    expect(shouldShowMeter(useChatStore.getState().contextBudgetByConv["c1"])).toBe(true);
    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
  });
});

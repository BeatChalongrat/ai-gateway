// FIX127 D1 — the message queue.
//
// Enter while an answer streams used to be a silent no-op. A message typed
// then now waits in the queue and is sent as its own turn when the answer
// finishes; queued messages can be edited, removed, or sent now (stop
// first). M5 holds: one turn in flight per chat — the queue releases one
// message per finished turn. A Stop by the person releases nothing.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openRegenerateStream: vi.fn(),
    listMessages: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(async () => {}),
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
const settle = async (n = 8) => { for (let i = 0; i < n; i++) await tick(); };
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
const s = () => useChatStore.getState();
const queue = () => s().queuedMessages.c1 ?? [];
const sentTexts = () => mockApi.openMessageStream.mock.calls.map((c) => c[1] as string);

/** Starts a turn and returns the controls to end it. */
async function startTurn(text: string, id: string) {
  const st = makeStream();
  mockApi.openMessageStream.mockResolvedValueOnce(st.response);
  const sending = s().sendMessage("c1", text);
  await tick();
  st.push("message_start", { message_id: id, conversation_id: "c1" });
  st.push("content_delta", { delta: "…" });
  return {
    sending,
    complete: async () => { st.push("message_complete", { message_id: id }); st.close(); await sending; await settle(); },
    fail: async () => { st.push("error", { code: "all_models_failed", message: "down", cause: "error" }); st.close(); await sending; await settle(); },
    stream: st,
  };
}

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv()], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  vi.clearAllMocks();
  mockApi.getConversation.mockResolvedValue(conv());
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX127 D1 — message queue", () => {
  it("queued messages are sent one per finished turn, in order (M5 kept)", async () => {
    const t1 = await startTurn("first", "a1");
    s().enqueueMessage("c1", "second");
    s().enqueueMessage("c1", "third", ["f-9"]);
    expect(queue().map((q) => q.text)).toEqual(["second", "third"]);
    expect(sentTexts()).toEqual(["first"]);

    // Prepare the next two streams before the turn ends.
    const st2 = makeStream();
    const st3 = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(st2.response).mockResolvedValueOnce(st3.response);
    await t1.complete();

    // Exactly one released: "second" is in flight, "third" still waits.
    expect(sentTexts()).toEqual(["first", "second"]);
    expect(queue().map((q) => q.text)).toEqual(["third"]);
    expect(s().abortControllers.c1).toBeDefined();

    st2.push("message_start", { message_id: "a2", conversation_id: "c1" });
    st2.push("message_complete", { message_id: "a2" });
    st2.close();
    await settle(12);
    expect(sentTexts()).toEqual(["first", "second", "third"]);
    expect(mockApi.openMessageStream.mock.calls[2][3]).toEqual(["f-9"]); // the files travelled with it
    expect(queue()).toEqual([]);
  });

  it("a failed answer also releases the next queued message", async () => {
    const t1 = await startTurn("first", "a1");
    s().enqueueMessage("c1", "second");
    mockApi.openMessageStream.mockResolvedValueOnce(makeStream().response);
    await t1.fail();
    expect(sentTexts()).toEqual(["first", "second"]);
  });

  it("edit and delete change the queue without sending", async () => {
    await startTurn("first", "a1");
    const id = s().enqueueMessage("c1", "secnd");
    s().updateQueuedMessage("c1", id, "second (fixed)");
    expect(queue()[0].text).toBe("second (fixed)");
    s().removeQueuedMessage("c1", id);
    expect(queue()).toEqual([]);
    expect(sentTexts()).toEqual(["first"]);
  });

  it("send now stops the current answer, then sends that message", async () => {
    await startTurn("first", "a1");
    const id = s().enqueueMessage("c1", "urgent");
    const st2 = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(st2.response);
    const p = s().sendQueuedNow("c1", id);
    await settle(6);
    expect(mockApi.cancelGeneration).toHaveBeenCalledWith("c1");
    expect(sentTexts()).toEqual(["first", "urgent"]);
    expect(queue()).toEqual([]);
    st2.push("message_start", { message_id: "a2", conversation_id: "c1" });
    st2.push("message_complete", { message_id: "a2" });
    st2.close();
    await p;
  });

  it("a Stop by the person releases nothing — the queue keeps waiting", async () => {
    const t1 = await startTurn("first", "a1");
    s().enqueueMessage("c1", "later");
    await s().cancelGeneration("c1"); // aborts the reader; the stream is torn down
    await t1.sending;
    await settle();
    expect(sentTexts()).toEqual(["first"]);
    expect(queue().map((q) => q.text)).toEqual(["later"]);
  });
});

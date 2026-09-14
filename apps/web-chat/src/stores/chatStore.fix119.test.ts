// FIX119 — the run id and the stop reason land on the row from the stream.
//
// Driven through sendMessage with a controllable SSE stream (the FIX114
// harness): message_start carries run_id (chat-edge's envelope splices it
// on every frame), message_complete carries stop_reason, an error frame
// carries run_id too.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(async () => ({ title: "t" })),
    listMessages: vi.fn(async () => ({ messages: [] })),
    cancelGeneration: vi.fn(async () => {}),
  },
  mockBridge: { seq: 0, peekSeq: vi.fn(() => 0), respondConsent: vi.fn(), respondQuestion: vi.fn(), cancelTurn: vi.fn(), post: vi.fn(() => true) },
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
vi.mock("@/lib/bridge", () => ({ bridge: mockBridge }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const RUN = "5f3c1a2b-0000-4000-8000-0123456789ab";
const frame = (event: string, data: Record<string, unknown>) => `event: ${event}\ndata: ${JSON.stringify({ run_id: RUN, ...data })}\n\n`;
function conv(id: string): Conversation {
  return { id, title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return { response: { ok: true, status: 200, body: stream } as unknown as Response, push: (raw: string) => controller.enqueue(enc.encode(raw)), close: () => controller.close() };
}
const until = async (pred: () => boolean, ms = 3000) => {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 20));
  }
};
const assistant = () => useChatStore.getState().messages.c1.find((m) => m.role === "assistant");

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX119 — run id and stop reason on the row", () => {
  it("a completed turn that hit max_tokens keeps the text AND says why it stopped; the run id is on the row", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hello");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "cut off he" }));
    s.push(frame("message_complete", { message_id: "a1", stop_reason: "max_tokens", gateway_request_id: "req_x" }));
    s.close();
    await p;
    expect(assistant()).toMatchObject({ status: "completed", content: "cut off he", stop_reason: "max_tokens", run_id: RUN });
  });

  it("a failed turn carries the run id the card can show", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hello");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("error", { code: "provider_timeout", message: "slow", retryable: true, cause: "timeout", request_id: "req_fail" }));
    s.close();
    await p;
    expect(assistant()).toMatchObject({ status: "error", error_code: "provider_timeout", run_id: RUN, terminal_cause: "timeout" });
  });

  it("an unknown stop reason is not written to the row", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hello");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "fine" }));
    s.push(frame("message_complete", { message_id: "a1", stop_reason: "length" }));
    s.close();
    await p;
    expect(assistant()?.stop_reason).toBeUndefined();
  });
});

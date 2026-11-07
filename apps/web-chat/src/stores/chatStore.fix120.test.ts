// FIX120 — a rescued turn's message_complete carries fallback_from, and the
// store keeps it on the row beside model_id (the live fallback_info frame
// still lands as before).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    // O9 — loadMessages under HOSTED_MODE fires hostedResumeProbe, which
    // awaits this. The real api.openResumeStream always resolves to a
    // Response (or throws); a bare vi.fn() resolved to undefined and the
    // probe's `res.ok` escaped as an unhandled rejection after the test
    // ended. No live turn on the host → a non-ok Response.
    openResumeStream: vi.fn(async () => ({ ok: false, status: 204 }) as unknown as Response),
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
  return { id, title: "T", model_id: "gpt-5.6-luna", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
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

describe("FIX120 — fallback_from on the row", () => {
  it("message_complete with fallback_from keeps it beside model_id; the live frame's notice is still there", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hi");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("fallback_info", { requested_model: "gpt-5.6-luna", actual_model: "qwen3.8-27b-fp8", reason: "Primary model unavailable (provider_unavailable)" }));
    s.push(frame("content_delta", { delta: "สวัสดี" }));
    s.push(frame("message_complete", { message_id: "a1", model_id: "qwen3.8-27b-fp8", fallback_from: "gpt-5.6-luna", stop_reason: "end_turn" }));
    s.close();
    await p;
    const a = assistant()!;
    expect(a.status).toBe("completed");
    expect(a.model_id).toBe("qwen3.8-27b-fp8");
    expect(a.fallback_from).toBe("gpt-5.6-luna");
    expect(a.fallback_info).toEqual({ requested_model: "gpt-5.6-luna", actual_model: "qwen3.8-27b-fp8", reason: "Primary model unavailable (provider_unavailable)" });
  });

  it("a normal turn carries no fallback_from", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hi");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "ok" }));
    s.push(frame("message_complete", { message_id: "a1", model_id: "gpt-5.6-luna" }));
    s.close();
    await p;
    expect(assistant()!.status).toBe("completed");
    expect(assistant()!.fallback_from).toBeUndefined();
    expect(assistant()!.fallback_info).toBeUndefined();
  });

  it("a reloaded row keeps the server's fallback_from as is", async () => {
    mockApi.listMessages.mockResolvedValueOnce({
      messages: [
        { id: "u1", role: "user", content: "hi", status: "completed", created_at: "2026-09-15T00:00:00Z" },
        { id: "a1", role: "assistant", content: "สวัสดี", status: "completed", created_at: "2026-09-15T00:00:01Z", model_id: "qwen3.8-27b-fp8", fallback_from: "gpt-5.6-luna" },
      ],
    } as never);
    useChatStore.setState({ messagesLoaded: {} });
    await useChatStore.getState().loadMessages("c1");
    const a = assistant()!;
    expect(a.fallback_from).toBe("gpt-5.6-luna");
    expect(a.model_id).toBe("qwen3.8-27b-fp8");
  });
});

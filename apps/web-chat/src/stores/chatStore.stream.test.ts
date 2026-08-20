// chatStore streaming tests — pins the SSE event handling inside
// consumeStream (via sendMessage): message_start id-swap,
// content_delta accumulation behind the RAF batcher, reasoning
// deltas, fallback_info, error terminal frames, cancel, and the
// pre-stream HTTP error path. The api module is mocked; the real
// parseSSE runs against a hand-fed ReadableStream.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi, mockBumpUsage } = vi.hoisted(() => {
  return {
    mockApi: {
      openMessageStream: vi.fn(),
      getConversation: vi.fn(),
      generateTitle: vi.fn(),
      cancelGeneration: vi.fn(),
      renameConversation: vi.fn(),
    },
    mockBumpUsage: vi.fn(),
  };
});

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

vi.mock("@/hooks/useUsage", () => ({
  bumpUsageVersion: mockBumpUsage,
}));

import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "./chatStore";

// Snapshot of the pristine store (state + actions) for per-test reset.
const initialState = useChatStore.getState();

// ─── Helpers ──────────────────────────────────────────────────

const enc = new TextEncoder();

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** Controllable fake streaming Response. `wireAbort` makes the
 *  pending read reject with an AbortError when the caller's signal
 *  fires — mirroring how a real aborted fetch body behaves. */
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push(raw: string) {
      controller.enqueue(enc.encode(raw));
    },
    close() {
      controller.close();
    },
    wireAbort(signal: AbortSignal) {
      signal.addEventListener("abort", () => {
        controller.error(new DOMException("The user aborted.", "AbortError"));
      });
    },
  };
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0));

function conv(id: string, extra?: Partial<Conversation>): Conversation {
  return {
    id,
    title: "T",
    model_id: "claude-sonnet-4",
    message_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...extra,
  };
}

function seedConversation(id: string, messages: Message[] = []) {
  useChatStore.setState({
    conversations: [conv(id)],
    messages: { [id]: messages },
    messagesLoaded: { [id]: true },
  });
}

const userMsg = (id: string): Message => ({
  id,
  role: "user",
  content: "earlier",
  status: "completed",
  created_at: "2026-01-01T00:00:00Z",
});

function messagesOf(id: string): Message[] {
  return useChatStore.getState().messages[id] ?? [];
}

// ─── RAF control ──────────────────────────────────────────────

let rafQueue: Map<number, FrameRequestCallback>;
let rafId: number;

function fireRaf() {
  const cbs = [...rafQueue.values()];
  rafQueue.clear();
  for (const cb of cbs) cb(0);
}

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  rafQueue = new Map();
  rafId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = rafId++;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    rafQueue.delete(id);
  });
  // Defaults for the post-stream bookkeeping calls.
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "Generated title" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ─── Tests ────────────────────────────────────────────────────

describe("sendMessage / consumeStream", () => {
  it("appends the optimistic user+assistant pair, swaps the temp id on message_start, batches deltas behind RAF, and completes", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    // The finally-block sidebar refresh merges getConversation's payload
    // over the row — reject it here so the AI-title assertion below
    // observes generateTitle's update rather than this merge.
    mockApi.getConversation.mockRejectedValue(new Error("offline"));
    seedConversation("c1");

    const done = useChatStore.getState().sendMessage("c1", "  hi there  ");
    await tick();

    // Optimistic pair (user content trimmed, assistant placeholder
    // streaming with the conversation's model stamped on).
    let list = messagesOf("c1");
    expect(list).toHaveLength(2);
    expect(list[0]).toMatchObject({
      role: "user",
      content: "hi there",
      status: "completed",
    });
    expect(list[1]).toMatchObject({
      role: "assistant",
      content: "",
      status: "streaming",
      isStreaming: true,
      model_id: "claude-sonnet-4",
    });
    const tempId = list[1].id;
    expect(tempId).toMatch(/^tmp-asst-/);
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBe(tempId);
    expect(useChatStore.getState().abortControllers["c1"]).toBeInstanceOf(
      AbortController,
    );

    // message_start swaps the placeholder id for the persisted row id.
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    await tick();
    list = messagesOf("c1");
    expect(list[1].id).toBe("real-1");
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBe("real-1");

    // Deltas buffer until the animation frame fires — no per-token setState.
    s.push(frame("content_delta", { delta: "Hel" }));
    s.push(frame("content_delta", { delta: "lo" }));
    await tick();
    expect(messagesOf("c1")[1].content).toBe("");
    fireRaf();
    expect(messagesOf("c1")[1].content).toBe("Hello");

    // message_complete flushes any tail and writes the terminal state.
    s.push(frame("content_delta", { delta: "!" }));
    await tick();
    s.push(
      frame("message_complete", {
        message_id: "real-1",
        token_usage: { input: 3, output: 5 },
      }),
    );
    s.close();
    await done;

    const finalMsg = messagesOf("c1")[1];
    expect(finalMsg.content).toBe("Hello!");
    expect(finalMsg.status).toBe("completed");
    expect(finalMsg.isStreaming).toBe(false);
    expect(finalMsg.token_usage).toEqual({ input: 3, output: 5 });
    expect(typeof finalMsg.response_time_ms).toBe("number");
    expect(mockBumpUsage).toHaveBeenCalledTimes(1);

    // First turn → AI title generation fired; stream bookkeeping cleared.
    expect(mockApi.generateTitle).toHaveBeenCalledWith("c1");
    expect(useChatStore.getState().abortControllers["c1"]).toBeUndefined();
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
    await tick();
    expect(
      useChatStore.getState().conversations.find((c) => c.id === "c1")?.title,
    ).toBe("Generated title");
  });

  it("accumulates reasoning_delta into reasoning_content and tracks analysis_status thinking window", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seedConversation("c1", [userMsg("u0")]); // not first message → no title gen

    const done = useChatStore.getState().sendMessage("c1", "think");
    await tick();

    s.push(frame("analysis_status", { status: "started" }));
    await tick();
    expect(messagesOf("c1").at(-1)?.is_thinking).toBe(true);

    s.push(frame("reasoning_delta", { delta: "step one. " }));
    s.push(frame("reasoning_delta", { delta: "step two." }));
    await tick();
    // Buffered until flush; analysis completed forces a synchronous flush.
    expect(messagesOf("c1").at(-1)?.reasoning_content ?? "").toBe("");
    s.push(frame("analysis_status", { status: "completed" }));
    await tick();
    const thought = messagesOf("c1").at(-1);
    expect(thought?.reasoning_content).toBe("step one. step two.");
    expect(thought?.is_thinking).toBe(false);
    expect(typeof thought?.thinking_duration_ms).toBe("number");

    s.push(frame("message_complete", { message_id: "m" }));
    s.close();
    await done;
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
  });

  it("stashes fallback_info on the streaming message", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(
      frame("fallback_info", {
        requested_model: "gpt-5",
        actual_model: "claude-sonnet-4",
        reason: "unavailable",
      }),
    );
    await tick();
    expect(messagesOf("c1").at(-1)?.fallback_info).toEqual({
      requested_model: "gpt-5",
      actual_model: "claude-sonnet-4",
      reason: "unavailable",
    });
    s.push(frame("message_complete", { message_id: "m" }));
    s.close();
    await done;
  });

  it("stashes reasoning_notice on the streaming message", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(
      frame("reasoning_notice", {
        model: "gpt-4o-mini",
        reason: "resolved model is not reasoning-capable",
      }),
    );
    await tick();
    expect(messagesOf("c1").at(-1)?.reasoning_notice).toEqual({
      model: "gpt-4o-mini",
      reason: "resolved model is not reasoning-capable",
    });
    // Informational only — the message must NOT be in an error state.
    expect(messagesOf("c1").at(-1)?.error_message).toBeUndefined();
    s.push(frame("message_complete", { message_id: "m" }));
    s.close();
    await done;
  });

  it("raises the stalled notice from a provider_stall frame and clears it on recovery", async () => {
    const st = makeStream();
    mockApi.openMessageStream.mockResolvedValue(st.response);
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();

    expect(useChatStore.getState().providerStalledStreams["c1"]).toBeFalsy();

    st.push(frame("provider_stall", { stalled: true, waiting_seconds: 75 }));
    await tick();
    expect(useChatStore.getState().providerStalledStreams["c1"]).toBe(true);

    // A heartbeat-driven byte activity must NOT clear a server-declared
    // stall — that is exactly the mistake the separate flag prevents.
    st.push(frame("content_delta", { delta: "" }));
    await tick();
    expect(useChatStore.getState().providerStalledStreams["c1"]).toBe(true);

    st.push(frame("provider_stall", { stalled: false }));
    await tick();
    expect(useChatStore.getState().providerStalledStreams["c1"]).toBe(false);

    st.push(frame("message_complete", { message_id: "m" }));
    st.close();
    await done;
  });

  // Phase 13 Part B — run_python artifacts reuse the existing
  // message_files seam: a chart png / generated file the sandbox produced
  // is attached to the assistant message and the generating flags clear.
  // No dedicated run_python case is needed — this pins that reuse.
  it("attaches run_python artifacts (image + file) via message_files and clears generating flags", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "plot it");
    await tick();
    s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
    await tick();

    const files = [
      {
        id: "art-img",
        filename: "chart.png",
        content_type: "image/png",
        is_generated: true,
        download_url: "/api/v1/files/art-img/download",
      },
      {
        id: "art-xlsx",
        filename: "report.xlsx",
        content_type:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        is_generated: true,
        download_url: "/api/v1/files/art-xlsx/download",
      },
    ];
    s.push(frame("message_files", { message_id: "real-1", files }));
    await tick();

    const msg = messagesOf("c1").at(-1);
    expect(msg?.files).toHaveLength(2);
    expect(msg?.files?.[0]).toMatchObject({
      content_type: "image/png",
      filename: "chart.png",
    });
    expect(msg?.files?.[1]?.filename).toBe("report.xlsx");
    expect(msg?.is_generating_image).toBe(false);
    expect(msg?.is_generating_video).toBe(false);

    s.push(frame("message_complete", { message_id: "real-1" }));
    s.close();
    await done;
  });

  it("terminal error frame marks the message error, localizes the code, and raises the banner", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(frame("content_delta", { delta: "partial " }));
    await tick();
    s.push(
      frame("error", {
        code: "rate_limit_exceeded",
        message: "Rate limit exceeded",
        attempts: [{ model: "claude-sonnet-4", error_code: "429" }],
      }),
    );
    s.close();
    await done;

    const msg = messagesOf("c1").at(-1);
    expect(msg?.status).toBe("error");
    expect(msg?.isStreaming).toBe(false);
    expect(msg?.error_code).toBe("rate_limit_exceeded");
    // Localized copy from errorMessages.ts (active locale) — not the wire message.
    expect(msg?.error_message).toBe(
      dictionaries.en["error.rate_limit_exceeded"],
    );
    expect(msg?.error_attempts).toEqual([
      { model: "claude-sonnet-4", error_code: "429" },
    ]);
    // The pre-error delta still flushed so UI matches what the server saved.
    expect(msg?.content).toBe("partial ");
    expect(useChatStore.getState().errorBanner).toBe(msg?.error_message);
  });

  it("cancelGeneration (before message_start) aborts the stream and finalizes the placeholder as cancelled, keeping flushed partial content", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockImplementation(
      async (_conv: string, _text: string, signal: AbortSignal) => {
        s.wireAbort(signal);
        return s.response;
      },
    );
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(frame("content_delta", { delta: "cut sho" }));
    await tick();

    await useChatStore.getState().cancelGeneration("c1");
    await done;

    expect(mockApi.cancelGeneration).toHaveBeenCalledWith("c1");
    const msg = messagesOf("c1").at(-1);
    expect(msg?.status).toBe("cancelled");
    expect(msg?.isStreaming).toBe(false);
    // Buffered delta flushed on the way out (finally block).
    expect(msg?.content).toBe("cut sho");
    expect(useChatStore.getState().abortControllers["c1"]).toBeUndefined();
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  // Fixed (Phase 11 A/E): the AbortError catch now finalizes the
  // CURRENT streaming id (get().streamingAssistantId) rather than the
  // stale tempAsstId, so a cancel that lands AFTER message_start has
  // swapped in the server id still marks the message "cancelled".
  it("cancelGeneration AFTER message_start finalizes the swapped server id as cancelled", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockImplementation(
      async (_conv: string, _text: string, signal: AbortSignal) => {
        s.wireAbort(signal);
        return s.response;
      },
    );
    seedConversation("c1", [userMsg("u0")]);

    const done = useChatStore.getState().sendMessage("c1", "hi");
    await tick();
    s.push(frame("message_start", { message_id: "real-9", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "cut sho" }));
    await tick();

    await useChatStore.getState().cancelGeneration("c1");
    await done;

    const msg = messagesOf("c1").at(-1);
    expect(msg?.id).toBe("real-9");
    expect(msg?.content).toBe("cut sho"); // partial content still flushed
    expect(msg?.status).toBe("cancelled");
    expect(msg?.isStreaming).toBe(false);
  });

  // E2 — collision-proof optimistic ids. Date.now()-based ids collided
  // when two sends landed in the same millisecond; every id must now be
  // unique even for same-tick sends.
  it("two sends in the same tick get four distinct optimistic ids", async () => {
    const s1 = makeStream();
    const s2 = makeStream();
    mockApi.openMessageStream
      .mockResolvedValueOnce(s1.response)
      .mockResolvedValueOnce(s2.response);
    seedConversation("c1", [userMsg("u0")]);

    const p1 = useChatStore.getState().sendMessage("c1", "first");
    const p2 = useChatStore.getState().sendMessage("c1", "second");
    await tick();

    const optimistic = messagesOf("c1").filter((m) => m.id.startsWith("tmp-"));
    expect(optimistic).toHaveLength(4); // 2 user + 2 assistant placeholders
    expect(new Set(optimistic.map((m) => m.id)).size).toBe(4);

    // Close both streams cleanly so nothing leaks into other tests.
    s1.push(frame("message_complete", { message_id: "m1" }));
    s1.close();
    s2.push(frame("message_complete", { message_id: "m2" }));
    s2.close();
    await Promise.all([p1, p2]);
  });

  it("pre-stream HTTP 429 marks the placeholder error and shows the throttle banner", async () => {
    mockApi.openMessageStream.mockResolvedValue({
      ok: false,
      status: 429,
      statusText: "Too Many Requests",
      text: async () =>
        JSON.stringify({
          error: { code: "rate_limit_exceeded", message: "slow down" },
        }),
    } as unknown as Response);
    seedConversation("c1", [userMsg("u0")]);

    await useChatStore.getState().sendMessage("c1", "hi");

    const msg = messagesOf("c1").at(-1);
    expect(msg?.role).toBe("assistant");
    expect(msg?.status).toBe("error");
    expect(useChatStore.getState().errorBanner).toBe(
      "Too many messages — slow down for a minute.",
    );
  });

  it("pre-stream 402 INSUFFICIENT_CREDITS localizes the credits banner", async () => {
    mockApi.openMessageStream.mockResolvedValue({
      ok: false,
      status: 402,
      statusText: "Payment Required",
      text: async () =>
        JSON.stringify({
          error: { code: "INSUFFICIENT_CREDITS", message: "no credits" },
        }),
    } as unknown as Response);
    seedConversation("c1", [userMsg("u0")]);

    await useChatStore.getState().sendMessage("c1", "hi");
    expect(useChatStore.getState().errorBanner).toBe(
      dictionaries.en["error.insufficient_credits"],
    );
  });

  it("does nothing for a blank message with no files", async () => {
    seedConversation("c1");
    await useChatStore.getState().sendMessage("c1", "   ");
    expect(messagesOf("c1")).toHaveLength(0);
    expect(mockApi.openMessageStream).not.toHaveBeenCalled();
  });
});

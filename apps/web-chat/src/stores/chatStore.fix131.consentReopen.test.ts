// FIX131 E3 — desktop: an unanswered permission card survives leaving the
// chat and coming back.
//
// NIGHT3 D1 (desktop e55): ask for a file outside the connected folders →
// consent card → open another chat → come back → no card, only Stop. The
// host log said "RESUME … replaying 69 event(s) from seq 1" and then
// "re-posting pending consent": the host re-posts the card in the same
// breath as the replay, but the page builds the turn's row only when the
// resume probe decides a turn replayed (~700 ms later). The card arrived
// first, found no running row, and was dropped — or pinned to the chat's
// previous, finished answer. Now it waits and lands on the running row.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message, PendingConsent, PendingQuestion } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    listMessages: vi.fn(),
    openResumeStream: vi.fn(),
    getConversation: vi.fn(),
  },
  mockBridge: { seq: 0, peekSeq: vi.fn(() => mockBridge.seq), respondConsent: vi.fn(), respondQuestion: vi.fn() },
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
vi.mock("@/lib/auth", async (importOriginal) => ({ ...(await importOriginal<object>()), HOSTED_MODE: true }));
vi.mock("@/lib/bridge", () => ({ bridge: mockBridge, usingBridgeTransport: true }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function conv(id: string): Conversation {
  return { id, title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
  };
}

const until = async (pred: () => boolean, ms = 3000) => {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 20));
  }
};

const consent: PendingConsent = {
  promptId: "consent-c8faff83-4da",
  tool: "read_file",
  argumentSummary: "night3-note.txt",
  dangerClass: "read",
} as PendingConsent;
const question: PendingQuestion = {
  promptId: "question-1",
  questions: [{ question: "Which format?", header: "Format", multiSelect: false, options: [{ label: "Short", description: "" }, { label: "Long", description: "" }] }],
};

const running = () => (useChatStore.getState().messages["c1"] ?? []).find((m) => m.role === "assistant" && m.isStreaming);

/** The host's answer to the page's ui.ready: the run replays into the sink
 *  (the bridge's cursor moves at once) and the pending card is re-posted in
 *  the same breath — before the page has built the turn's row. */
function hostReplaysWith(card: () => void) {
  const s = makeStream();
  mockApi.openResumeStream.mockImplementation(async () => {
    mockBridge.seq = 69;
    card();
    return s.response;
  });
  return s;
}

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv("c1")] }, true);
  vi.clearAllMocks();
  mockBridge.seq = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX131 E3 — a pending card re-posted on reopen is shown", () => {
  it("a new chat (no earlier answer): the card lands on the replayed turn's row and stays through message_start", async () => {
    useChatStore.setState({ messages: { c1: [] }, messagesLoaded: { c1: true } });
    const s = hostReplaysWith(() => useChatStore.getState().addPendingConsent("c1", consent));

    useChatStore.getState().hostedResumeProbe("c1");
    await until(() => Boolean(running()));
    expect(running()?.pending_consent?.promptId).toBe(consent.promptId);

    s.push(frame("message_start", { run_id: "r", seq: 1, message_id: "host-asst-1", conversation_id: "c1" }));
    s.push(frame("tool_activity", { run_id: "r", seq: 2, id: "call_1", tool: "read_file", phase: "started" }));
    await until(() => running()?.id === "host-asst-1");
    expect(running()?.pending_consent?.promptId).toBe(consent.promptId);

    // The person answers it: the answer reaches the host, the card goes.
    useChatStore.getState().resolveConsent("c1", consent.promptId, "deny", "once");
    expect(mockBridge.respondConsent).toHaveBeenCalledWith(consent.promptId, "deny", "once");
    expect(running()?.pending_consent).toBeNull();
    s.close();
  });

  it("a chat with an earlier finished answer: the card is not pinned to that answer", async () => {
    const earlier: Message[] = [
      { id: "u0", role: "user", content: "ก่อนหน้า", status: "completed", created_at: "2026-01-01T00:00:00Z" } as Message,
      { id: "a0", role: "assistant", content: "ตอบแล้ว", status: "completed", created_at: "2026-01-01T00:00:01Z" } as Message,
    ];
    useChatStore.setState({ messages: { c1: earlier }, messagesLoaded: { c1: true } });
    const s = hostReplaysWith(() => useChatStore.getState().addPendingConsent("c1", consent));

    useChatStore.getState().hostedResumeProbe("c1");
    await until(() => Boolean(running()));
    const rows = useChatStore.getState().messages["c1"];
    expect(rows.find((m) => m.id === "a0")?.pending_consent).toBeFalsy();
    expect(running()?.pending_consent?.promptId).toBe(consent.promptId);
    s.close();
  });

  it("the question card follows the same rule", async () => {
    useChatStore.setState({ messages: { c1: [] }, messagesLoaded: { c1: true } });
    const s = hostReplaysWith(() => useChatStore.getState().addPendingQuestion("c1", question));

    useChatStore.getState().hostedResumeProbe("c1");
    await until(() => Boolean(running()));
    expect(running()?.pending_question?.promptId).toBe("question-1");
    s.close();
  });

  it("a card for the turn the page is already showing attaches at once (unchanged)", () => {
    const row: Message = { id: "m1", role: "assistant", content: "", status: "streaming", created_at: "2026-01-01T00:00:00Z", isStreaming: true };
    useChatStore.setState({ messages: { c1: [row] }, streamingAssistantId: { c1: "m1" } });
    useChatStore.getState().addPendingConsent("c1", consent);
    expect(useChatStore.getState().messages["c1"][0].pending_consent?.promptId).toBe(consent.promptId);
    expect(useChatStore.getState().parkedHostPrompts["c1"]).toBeUndefined();
  });

  it("Stop drops a parked card (the host denies it on cancel)", async () => {
    useChatStore.setState({ messages: { c1: [] }, messagesLoaded: { c1: true } });
    useChatStore.getState().addPendingConsent("c1", consent);
    expect(useChatStore.getState().parkedHostPrompts["c1"]?.consent?.promptId).toBe(consent.promptId);
    await useChatStore.getState().cancelGeneration("c1").catch(() => {});
    expect(useChatStore.getState().parkedHostPrompts["c1"]).toBeUndefined();
  });
});

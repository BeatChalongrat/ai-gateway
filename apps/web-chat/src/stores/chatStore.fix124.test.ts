// FIX124 — the page's half of "never a blank, never a false failed".
//
//   T   a chat is sent for its name after the first turn that is ANSWERED —
//       not only after turn one — and shows the first words until then.
//   R2  a connection that cannot be re-opened is not an answer that failed:
//       the row waits, wakes when the network is back, and takes the server's
//       row as the truth. It never becomes an error on its own.
//   R3  message_complete's tool_rounds reaches the row.
//
// The api module is mocked; the real parseSSE runs against a hand-fed stream.

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

import { RESUME_RETRY_DELAYS, RESUME_WAIT, placeholderTitle, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const originalDelays = [...RESUME_RETRY_DELAYS];
const originalWait = { ...RESUME_WAIT };
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
    fail: () => controller.error(new TypeError("network error")),
  };
}

const conv = (over: Partial<Conversation> = {}): Conversation =>
  ({ id: "c1", title: "New conversation", model_id: "deepseek-v4-flash", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...over });
const row = (id: string, role: "user" | "assistant", status: Message["status"], content = "x"): Message =>
  ({ id, role, status, content, created_at: "2026-09-21T00:00:00Z" }) as Message;
function seed(messages: Message[] = [], over: Partial<Conversation> = {}) {
  useChatStore.setState({ conversations: [conv(over)], messages: { c1: messages }, messagesLoaded: { c1: true } });
}
const rows = () => useChatStore.getState().messages.c1 ?? [];
const titleNow = () => useChatStore.getState().conversations[0]?.title;

async function turn(text: string, ending: "completed" | "error") {
  await settle(4); // let the previous turn's title request land
  const s = makeStream();
  mockApi.openMessageStream.mockResolvedValueOnce(s.response);
  const sending = useChatStore.getState().sendMessage("c1", text);
  await tick();
  const id = `m-${text}`;
  s.push("message_start", { message_id: id, conversation_id: "c1" });
  if (ending === "completed") {
    s.push("content_delta", { delta: "answer" });
    s.push("message_complete", { message_id: id });
  } else {
    s.push("error", { code: "all_models_failed", message: "down", cause: "error" });
  }
  s.close();
  await sending;
  await settle(3);
}

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays.map(() => 0));
  Object.assign(RESUME_WAIT, { pollMs: 5, capMs: 10_000 });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockImplementation(async () => useChatStore.getState().conversations[0]);
  // a model call: it answers after the turn-end refresh of the conversation row
  mockApi.generateTitle.mockImplementation(async () => {
    await settle(2);
    return { title: "ชื่อจากโมเดล", source: "model" };
  });
});
afterEach(() => {
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays);
  Object.assign(RESUME_WAIT, originalWait);
  vi.unstubAllGlobals();
});

describe("FIX124 T — named after the first turn that was answered", () => {
  it("fail → fail → success: one title request, after the success", async () => {
    seed();
    await turn("one", "error");
    await turn("two", "error");
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
    await turn("three", "completed");
    await settle(6);
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
    expect(titleNow()).toBe("ชื่อจากโมเดล");
    await turn("four", "completed");
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
  });

  it("success on turn one still asks once, and later turns never do", async () => {
    seed();
    await turn("one", "completed");
    await turn("two", "completed");
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
  });

  it("never shows 'New conversation' once a message is sent: the first words stand in at once", async () => {
    seed();
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const sending = useChatStore.getState().sendMessage("c1", "ช่วยสรุปรายงานประจำไตรมาสให้หน่อย");
    await tick();
    expect(titleNow()).toBe("ช่วยสรุปรายงานประจำไตรมาสให้หน่อย"); // before any answer
    s.push("message_start", { message_id: "m1", conversation_id: "c1" });
    s.push("error", { code: "all_models_failed", message: "down", cause: "error" });
    s.close();
    await sending;
    await settle(3);
    expect(titleNow()).toBe("ช่วยสรุปรายงานประจำไตรมาสให้หน่อย"); // and after it failed
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
  });

  it("the placeholder is the server's cut, and never replaces a real title", async () => {
    expect(placeholderTitle("  สั้น  ")).toBe("สั้น");
    expect(placeholderTitle("ก".repeat(150))).toBe(`${"ก".repeat(100)}…`);
    seed([], { title: "ชื่อที่ตั้งเอง" });
    await turn("one", "error");
    expect(titleNow()).toBe("ชื่อที่ตั้งเอง");
  });

  it("fail → the person renames → success: no title request, the rename stays", async () => {
    seed();
    await turn("one", "error");
    mockApi.renameConversation.mockResolvedValueOnce({ ...conv(), title: "ชื่อที่ตั้งเอง" });
    await useChatStore.getState().renameConversation("c1", "ชื่อที่ตั้งเอง");
    await turn("two", "completed");
    await settle(6);
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
    expect(titleNow()).toBe("ชื่อที่ตั้งเอง");
  });

  it("temporary chat: no title request on any turn", async () => {
    // FIX127 B1 — temporary = started as temporary (is_temporary).
    seed([], { is_temporary: true, memory_enabled: false });
    await turn("one", "error");
    await turn("two", "completed");
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
  });

  it("FIX127 B1 — memory off on an ordinary chat is not temporary: it is still named", async () => {
    seed([], { memory_enabled: false });
    await turn("one", "completed");
    await settle(3);
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
  });

  it("Retry of a failed first turn that now answers names the chat", async () => {
    seed([row("u1", "user", "completed", "คำถาม"), row("a1", "assistant", "error", "")]);
    const s = makeStream();
    mockApi.openRegenerateStream.mockResolvedValueOnce(s.response);
    const regen = useChatStore.getState().regenerate("c1", "a1");
    await tick();
    s.push("message_start", { message_id: "a2", conversation_id: "c1" });
    s.push("content_delta", { delta: "answer" });
    s.push("message_complete", { message_id: "a2" });
    s.close();
    await regen;
    await settle(3);
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
  });

  it("desktop: the host renames the reply's id at message_complete — still asked once", async () => {
    seed();
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const sending = useChatStore.getState().sendMessage("c1", "one");
    await tick();
    s.push("message_start", { message_id: "asst_local", conversation_id: "c1" });
    s.push("content_delta", { delta: "answer" });
    s.push("message_complete", { message_id: "asst_local", assistant_message_id: "11111111-1111-4111-8111-111111111111", user_message_id: "22222222-2222-4222-8222-222222222222" });
    s.close();
    await sending;
    await settle(3);
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
  });
});

describe("FIX124 R2 — a lost connection is not a failed answer", () => {
  const streamingRow = () => [row("u1", "user", "completed", "q"), { ...row("m1", "assistant", "streaming", "half "), isStreaming: true } as Message];

  it("offline, then the server is reachable again and the turn FINISHED meanwhile: the full answer, never 'failed'", async () => {
    seed(streamingRow());
    let reachable = false;
    const statuses: string[] = [];
    mockApi.openResumeStream.mockImplementation(async () => {
      statuses.push(rows()[1].status);
      if (!reachable) throw new TypeError("Failed to fetch");
      const s = makeStream();
      queueMicrotask(() => {
        s.push("content_delta", { delta: "half the whole answer" });
        s.push("message_complete", { message_id: "m1" });
        s.close();
      });
      return s.response;
    });
    const resuming = useChatStore.getState().resumeStream("c1", "m1");
    await settle(12); // the quick retries are spent; the row is waiting
    expect(rows()[1].reconnecting).toBe(true);
    expect(rows()[1].status).toBe("streaming");
    expect(useChatStore.getState().errorBanner ?? null).toBeNull();

    reachable = true;
    window.dispatchEvent(new Event("online"));
    await resuming;
    expect(rows()[1]).toMatchObject({ status: "completed", content: "half the whole answer" });
    expect(rows()[1].reconnecting).toBeFalsy();
    expect(statuses.every((s) => s === "streaming")).toBe(true); // at no point was it "error"
    expect(mockApi.openResumeStream.mock.calls.length).toBeGreaterThan(RESUME_RETRY_DELAYS.length);
  });

  it("reconnect while the turn is STILL running: the stream continues", async () => {
    seed(streamingRow());
    let reachable = false;
    const live = makeStream();
    mockApi.openResumeStream.mockImplementation(async () => {
      if (!reachable) throw new TypeError("Failed to fetch");
      return live.response;
    });
    const resuming = useChatStore.getState().resumeStream("c1", "m1");
    await settle(12);
    reachable = true;
    window.dispatchEvent(new Event("online"));
    await settle(4);
    live.push("content_delta", { delta: "still " });
    await settle(3);
    expect(rows()[1].isStreaming).toBe(true);
    expect(rows()[1].reconnecting).toBeFalsy();
    live.push("content_delta", { delta: "writing" });
    live.push("message_complete", { message_id: "m1" });
    live.close();
    await resuming;
    expect(rows()[1].status).toBe("completed");
    expect(rows()[1].content).toContain("still writing");
  });

  it("the wait runs out: the server's row is the verdict — finished there, finished here", async () => {
    seed(streamingRow());
    Object.assign(RESUME_WAIT, { pollMs: 1, capMs: 0 });
    mockApi.openResumeStream.mockRejectedValue(new TypeError("Failed to fetch"));
    mockApi.listMessages.mockResolvedValueOnce({ messages: [row("u1", "user", "completed", "q"), row("m1", "assistant", "completed", "the whole answer")], has_more: false });
    await useChatStore.getState().resumeStream("c1", "m1");
    expect(rows()[1]).toMatchObject({ status: "completed", content: "the whole answer" });
  });

  it("…and a turn the SERVER says failed is shown as failed", async () => {
    seed(streamingRow());
    Object.assign(RESUME_WAIT, { pollMs: 1, capMs: 0 });
    mockApi.openResumeStream.mockRejectedValue(new TypeError("Failed to fetch"));
    mockApi.listMessages.mockResolvedValueOnce({ messages: [row("u1", "user", "completed", "q"), { ...row("m1", "assistant", "error", ""), error_code: "all_models_failed" }], has_more: false });
    await useChatStore.getState().resumeStream("c1", "m1");
    expect(rows()[1]).toMatchObject({ status: "error", error_code: "all_models_failed" });
  });

  it("server still out of reach at the end of the wait: 'load the answer', not a failure — and the thread is not emptied", async () => {
    seed(streamingRow());
    Object.assign(RESUME_WAIT, { pollMs: 1, capMs: 0 });
    mockApi.openResumeStream.mockRejectedValue(new TypeError("Failed to fetch"));
    mockApi.listMessages.mockRejectedValue(new TypeError("Failed to fetch"));
    await useChatStore.getState().resumeStream("c1", "m1");
    expect(rows()).toHaveLength(2);
    expect(rows()[1]).toMatchObject({ awaiting_reload: true, isStreaming: false, content: "half " });
    expect(rows()[1].status).not.toBe("error");
    expect(useChatStore.getState().streamingAssistantId.c1).toBeUndefined(); // the composer is free

    // the button
    mockApi.listMessages.mockResolvedValueOnce({ messages: [row("u1", "user", "completed", "q"), row("m1", "assistant", "completed", "the whole answer")], has_more: false });
    await useChatStore.getState().reloadAnswer("c1");
    expect(rows()[1]).toMatchObject({ status: "completed", content: "the whole answer" });
    expect(rows()[1].awaiting_reload).toBeFalsy();
  });

  it("Stop while waiting ends the wait", async () => {
    seed(streamingRow());
    mockApi.openResumeStream.mockRejectedValue(new TypeError("Failed to fetch"));
    mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
    const resuming = useChatStore.getState().resumeStream("c1", "m1");
    await settle(12);
    await useChatStore.getState().cancelGeneration("c1");
    await resuming;
    expect(rows()[1].status).toBe("cancelled");
  });
});

describe("FIX124 R3 — the tool-step limit reaches the row", () => {
  it("message_complete with stop_reason tool_use + tool_rounds", async () => {
    seed([row("u0", "user", "completed"), row("a0", "assistant", "completed")]);
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const sending = useChatStore.getState().sendMessage("c1", "many steps");
    await tick();
    s.push("message_start", { message_id: "m1", conversation_id: "c1" });
    s.push("content_delta", { delta: "Let me work on it. " });
    s.push("message_complete", { message_id: "m1", stop_reason: "tool_use", tool_rounds: 5 });
    s.close();
    await sending;
    expect(rows().at(-1)).toMatchObject({ status: "completed", stop_reason: "tool_use", tool_rounds: 5 });
  });
});

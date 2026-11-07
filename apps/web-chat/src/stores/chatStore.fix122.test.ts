// FIX122 — Part B (the page asks for a name once and never renames) and
// Part C (the sent text lives in the outbox until the server takes it).
//
// The api module is mocked; the real parseSSE runs against a hand-fed stream
// (the chatStore.stream.test.ts pattern).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
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

import { clearDraftStore, loadDraft } from "@/lib/draftStore";
import { clearOutbox, outboxPeek, outboxPut } from "@/lib/sendOutbox";

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push(event: string, data: unknown) {
      controller.enqueue(enc.encode(frame(event, data)));
    },
    close() {
      controller.close();
    },
    finish(messageId: string) {
      controller.enqueue(enc.encode(frame("message_start", { message_id: messageId, conversation_id: "c1" })));
      controller.enqueue(enc.encode(frame("content_delta", { delta: "ok" })));
      controller.enqueue(enc.encode(frame("message_complete", { message_id: messageId })));
      controller.close();
    },
  };
}

function conv(id: string): Conversation {
  return { id, title: "New conversation", model_id: "qwen3.8-27b-fp8", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
function seed(id: string, messages: Message[] = []) {
  useChatStore.setState({ conversations: [conv(id)], messages: { [id]: messages }, messagesLoaded: { [id]: true } });
}
const requestIdOf = (call: number) => mockApi.openMessageStream.mock.calls[call][7] as string;
const refused = (status: number, code: string) =>
  ({ ok: false, status, statusText: "x", text: async () => JSON.stringify({ error: { code, message: code } }) }) as unknown as Response;
const titleOf = (id: string) => useChatStore.getState().conversations.find((c) => c.id === id)?.title;

async function completeFirstTurn(text = "สวัสดี") {
  const a = makeStream();
  mockApi.openMessageStream.mockResolvedValueOnce(a.response);
  const sending = useChatStore.getState().sendMessage("c1", text);
  await tick();
  a.finish("m1");
  await sending;
}

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX122 Part C — the text is on the machine until the server takes it", () => {
  it("is in the outbox while the request is out, and gone once the stream opens", async () => {
    seed("c1");
    let release!: (r: Response) => void;
    mockApi.openMessageStream.mockReturnValueOnce(new Promise<Response>((r) => { release = r; }));
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี");
    await tick();
    expect(outboxPeek("c1")).toMatchObject({ text: "สวัสดี", requestId: requestIdOf(0) });

    const a = makeStream();
    release(a.response);
    await tick();
    expect(outboxPeek("c1")).toBeNull();
    a.finish("m1");
    await sending;
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
    expect(loadDraft("c1")).toBeNull();
  });

  it("a refused send gives the text back to the composer and the draft", async () => {
    seed("c1");
    mockApi.openMessageStream.mockResolvedValueOnce(refused(502, "PROVIDER_UNAVAILABLE"));
    await useChatStore.getState().sendMessage("c1", "ข้อความที่ส่งไม่ถึง");
    expect(useChatStore.getState().returnedDrafts.c1?.text).toBe("ข้อความที่ส่งไม่ถึง");
    expect(loadDraft("c1")).toBe("ข้อความที่ส่งไม่ถึง");
    expect(outboxPeek("c1")).toBeNull();
  });

  it("a request that dies with no answer gives the text back", async () => {
    seed("c1");
    mockApi.openMessageStream.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await useChatStore.getState().sendMessage("c1", "เน็ตหลุด");
    expect(useChatStore.getState().returnedDrafts.c1?.text).toBe("เน็ตหลุด");
    expect(loadDraft("c1")).toBe("เน็ตหลุด");
  });

  it("a Stop is not a failed send", async () => {
    seed("c1");
    mockApi.openMessageStream.mockRejectedValueOnce(Object.assign(new Error("aborted"), { name: "AbortError" }));
    await useChatStore.getState().sendMessage("c1", "หยุดเอง");
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
    expect(loadDraft("c1")).toBeNull();
    expect(outboxPeek("c1")).toBeNull();
  });

  it("a turn that fails AFTER the server took the message does not come back", async () => {
    seed("c1");
    const a = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(a.response);
    const sending = useChatStore.getState().sendMessage("c1", "รับแล้วค่อยล้ม");
    await tick();
    a.push("message_start", { message_id: "m1", conversation_id: "c1" });
    a.push("error", { code: "provider_unavailable", message: "down" });
    a.close();
    await sending;
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
    expect(loadDraft("c1")).toBeNull();
  });

  it("after a reload: an entry the server never got goes back; one it has is dropped", async () => {
    seed("c1");
    outboxPut("c1", "req-lost", "หายระหว่างรีโหลด");
    mockApi.listMessages.mockResolvedValueOnce({ messages: [], has_more: false });
    await useChatStore.getState().loadMessages("c1");
    expect(useChatStore.getState().returnedDrafts.c1?.text).toBe("หายระหว่างรีโหลด");

    clearDraftStore();
    useChatStore.setState({ returnedDrafts: {} });
    outboxPut("c1", "req-kept", "ถึงแล้ว");
    mockApi.listMessages.mockResolvedValueOnce({
      messages: [{ id: "u1", role: "user", content: "ถึงแล้ว", status: "completed", created_at: "2026-09-21T00:00:00Z", run_id: "req-kept" }],
      has_more: false,
    });
    await useChatStore.getState().loadMessages("c1");
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
    expect(outboxPeek("c1")).toBeNull();
  });
});

describe("FIX122 Part B — the page asks for a name once and never renames", () => {
  it("first turn: asks once and shows the server's title", async () => {
    seed("c1");
    // The name arrives after the turn-end refresh of the conversation row,
    // as it does for real (a model call against a row read).
    let answer!: (v: { title: string; source: string }) => void;
    mockApi.generateTitle.mockReturnValueOnce(new Promise((r) => { answer = r; }));
    // …and that refresh brings the server's first-words title, which is not
    // a rename: the generated name still replaces it.
    mockApi.getConversation.mockResolvedValue({ ...conv("c1"), title: "สวัสดี" });
    await completeFirstTurn();
    await tick();
    answer({ title: "ทักทายกัน", source: "model" });
    await tick();
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
    expect(titleOf("c1")).toBe("ทักทายกัน");
  });

  // FIX124 — "later" means after a turn that was ANSWERED; a lone user row
  // (its answer failed) is not one: see chatStore.fix124.test.ts.
  it("turns after an answered one never ask", async () => {
    seed("c1", [
      { id: "u0", role: "user", content: "ก่อนหน้า", status: "completed", created_at: "2026-09-21T00:00:00Z" } as Message,
      { id: "a0", role: "assistant", content: "ตอบแล้ว", status: "completed", created_at: "2026-09-21T00:00:01Z" } as Message,
    ]);
    await completeFirstTurn("ต่อ");
    await tick();
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
  });

  it("a rename made while the name was being written is not replaced on screen", async () => {
    seed("c1");
    let answer!: (v: { title: string }) => void;
    mockApi.generateTitle.mockReturnValueOnce(new Promise((r) => { answer = r; }));
    await completeFirstTurn();
    mockApi.renameConversation.mockResolvedValueOnce({ ...conv("c1"), title: "ชื่อที่ตั้งเอง" });
    await useChatStore.getState().renameConversation("c1", "ชื่อที่ตั้งเอง");
    answer({ title: "ชื่อจากโมเดล" });
    await tick();
    expect(titleOf("c1")).toBe("ชื่อที่ตั้งเอง");
  });

  it("a failed title request renames nothing", async () => {
    seed("c1");
    mockApi.generateTitle.mockRejectedValueOnce(new Error("502"));
    await completeFirstTurn();
    await tick();
    expect(mockApi.renameConversation).not.toHaveBeenCalled();
    expect(titleOf("c1")).toBe("New conversation");
  });

  it("a first turn that was not answered never asks (the server wrote the first words when it took the message)", async () => {
    const cases: Array<[string, () => void]> = [
      ["refused", () => mockApi.openMessageStream.mockResolvedValueOnce(refused(502, "PROVIDER_UNAVAILABLE"))],
      ["request died", () => mockApi.openMessageStream.mockRejectedValueOnce(new TypeError("Failed to fetch"))],
      ["stopped", () => mockApi.openMessageStream.mockRejectedValueOnce(Object.assign(new Error("aborted"), { name: "AbortError" }))],
    ];
    for (const [, arrange] of cases) {
      seed("c1");
      arrange();
      await useChatStore.getState().sendMessage("c1", "สวัสดี");
      await tick();
    }
    // …and a turn the server took that then ended in an error
    seed("c1");
    const a = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(a.response);
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี");
    await tick();
    a.push("message_start", { message_id: "m1", conversation_id: "c1" });
    a.push("error", { message_id: "m1", code: "all_models_failed", message: "x" });
    a.close();
    await sending;
    await tick();
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
  });

  it("desktop: a reply the host renames to the server's row id at message_complete still asks once", async () => {
    seed("c1");
    const a = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(a.response);
    const sending = useChatStore.getState().sendMessage("c1", "สวัสดี");
    await tick();
    a.push("message_start", { message_id: "asst_local1", conversation_id: "c1" });
    a.push("content_delta", { delta: "ok" });
    a.push("message_complete", { message_id: "asst_local1", assistant_message_id: "srv-row-1" });
    a.close();
    await sending;
    await tick();
    expect(useChatStore.getState().messages.c1.some((m) => m.id === "srv-row-1" && m.status === "completed")).toBe(true);
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
  });

  it("a first turn that was answered asks exactly once", async () => {
    seed("c1");
    await completeFirstTurn();
    await tick();
    expect(mockApi.generateTitle).toHaveBeenCalledTimes(1);
    expect(mockApi.generateTitle).toHaveBeenCalledWith("c1");
  });

  it("a temporary chat is never sent for a name", async () => {
    seed("c1");
    // FIX127 B1 — temporary = is_temporary; memory off alone still gets a name.
    useChatStore.setState((s) => ({ conversations: s.conversations.map((c) => ({ ...c, is_temporary: true, memory_enabled: false })) }));
    await completeFirstTurn();
    await tick();
    expect(mockApi.generateTitle).not.toHaveBeenCalled();
  });
});

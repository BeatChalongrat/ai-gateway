// FIX125 M5 — a second send into a conversation that already has a turn in
// flight (another tab) is refused by the server with 409 CONVERSATION_BUSY.
// Nothing was sent: the text goes back to the composer, this send's
// optimistic user + assistant rows disappear (no error bubble, no duplicate
// question), and a localized notice says why.

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
import { tOutside } from "@/lib/i18n";
import { clearOutbox, outboxPeek } from "@/lib/sendOutbox";

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

function conv(id: string): Conversation {
  return { id, title: "t", model_id: "qwen3.8-27b-fp8", message_count: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
const earlier: Message[] = [
  { id: "u0", role: "user", content: "ก่อนหน้า", status: "completed", created_at: "2026-01-01T00:00:00Z" } as Message,
  { id: "a0", role: "assistant", content: "ตอบแล้ว", status: "completed", created_at: "2026-01-01T00:00:01Z" } as Message,
];
const refused = (status: number, code: string, message = "conversation busy") =>
  ({ ok: false, status, statusText: "Conflict", text: async () => JSON.stringify({ error: { code, message } }) }) as unknown as Response;

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  useChatStore.setState({ conversations: [conv("c1")], messages: { c1: [...earlier] }, messagesLoaded: { c1: true } });
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX125 M5 — 409 CONVERSATION_BUSY", () => {
  it("removes this send's rows, returns the draft and shows the busy notice", async () => {
    mockApi.openMessageStream.mockResolvedValueOnce(refused(409, "CONVERSATION_BUSY"));
    await useChatStore.getState().sendMessage("c1", "ส่งซ้อนจากอีกแท็บ");

    const s = useChatStore.getState();
    // Only the rows that were there before — no extra user row, no error row.
    expect(s.messages.c1.map((m) => m.id)).toEqual(["u0", "a0"]);
    expect(s.messages.c1.some((m) => m.status === "error")).toBe(false);
    // The text is back in the composer.
    expect(s.returnedDrafts.c1?.text).toBe("ส่งซ้อนจากอีกแท็บ");
    expect(loadDraft("c1")).toBe("ส่งซ้อนจากอีกแท็บ");
    expect(outboxPeek("c1")).toBeNull();
    // A localized notice, not the raw server message.
    expect(s.errorBanner).toBe(tOutside("error.conversationBusy"));
    expect(s.errorBanner).not.toBe("conversation busy");
    // The conversation is not left "streaming".
    expect(s.streamingAssistantId.c1).toBeUndefined();
  });

  it("another 409 code still takes the ordinary refused path (error row kept)", async () => {
    mockApi.openMessageStream.mockResolvedValueOnce(refused(409, "SOMETHING_ELSE", "other conflict"));
    await useChatStore.getState().sendMessage("c1", "อื่น ๆ");
    const s = useChatStore.getState();
    expect(s.messages.c1.length).toBe(4);
    expect(s.messages.c1[3].status).toBe("error");
    expect(s.errorBanner).toBe("other conflict");
  });
});

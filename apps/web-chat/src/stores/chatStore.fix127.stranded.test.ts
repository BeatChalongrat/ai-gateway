// FIX127 A3 — a send into a conversation deleted elsewhere keeps the text.
//
// R3: the 404 took the page to /chat with an empty composer and no notice
// of what happened to the message. Now the text becomes the new-chat draft
// (where the page lands), the banner says so in the user's language and
// offers "send in a new chat", and a draft typed under the dead id moves
// with it.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    listMessages: vi.fn(),
    openMessageStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
  },
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
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { ApiError } from "@/lib/api";
import { clearDraftStore, loadDraft, NEW_CHAT_DRAFT_KEY, saveDraft } from "@/lib/draftStore";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

function conv(id: string, extra?: Partial<Conversation>): Conversation {
  return {
    id, title: "T", model_id: "m", message_count: 0,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...extra,
  };
}

const gone404 = {
  ok: false, status: 404,
  text: () => Promise.resolve(JSON.stringify({ error: { code: "NOT_FOUND", message: "conversation not found" } })),
} as unknown as Response;

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  clearDraftStore();
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockRejectedValue(new ApiError("not_found", "gone", 404));
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX127 A3 — send into a deleted conversation", () => {
  it("keeps the text as the new-chat draft, says so, and offers send-in-new-chat", async () => {
    useChatStore.setState({ conversations: [conv("c1")], activeConversationId: "c1", messages: { c1: [] }, messagesLoaded: { c1: true } });
    mockApi.openMessageStream.mockResolvedValue(gone404);

    await useChatStore.getState().sendMessage("c1", "ข้อความสำคัญที่พิมพ์ไว้");

    const s = useChatStore.getState();
    expect(s.conversations).toHaveLength(0);
    expect(loadDraft(NEW_CHAT_DRAFT_KEY)).toBe("ข้อความสำคัญที่พิมพ์ไว้");
    // FIX132 F25 — plus the model of the chat it was meant for.
    expect(s.strandedSend).toEqual({ text: "ข้อความสำคัญที่พิมพ์ไว้", modelId: "m" });
    expect(s.errorBanner).toBe(dictionaries.en["error.conversationDeletedKeptText"]);
    expect(dictionaries.th["error.conversationDeletedKeptText"]).toContain("ถูกลบไปแล้ว");
    expect(dictionaries.th["error.sendInNewChat"]).toBe("ส่งในแชทใหม่");
  });

  it("a draft typed under the dead conversation moves to the new-chat composer", () => {
    useChatStore.setState({ conversations: [conv("c1")], activeConversationId: "c1" });
    saveDraft("c1", "half-typed thought");

    useChatStore.getState().handleConversationGone("c1");

    expect(loadDraft("c1")).toBeNull();
    expect(loadDraft(NEW_CHAT_DRAFT_KEY)).toBe("half-typed thought");
    // Nothing was sent, so there is nothing to offer to send.
    expect(useChatStore.getState().strandedSend).toBeNull();
    expect(useChatStore.getState().errorBanner).toBe(dictionaries.en["error.conversationDeleted"]);
  });

  it("clearStrandedSend drops the hand-off", async () => {
    useChatStore.setState({ conversations: [conv("c1")], activeConversationId: "c1", messages: { c1: [] }, messagesLoaded: { c1: true } });
    mockApi.openMessageStream.mockResolvedValue(gone404);
    await useChatStore.getState().sendMessage("c1", "hello");
    useChatStore.getState().clearStrandedSend();
    expect(useChatStore.getState().strandedSend).toBeNull();
  });
});

// FIX132 F12 — a search hit opens the chat AT the message. NIGHT3 J9: Ctrl+K
// found a phrase in message 16 of a 3,000-message chat, and clicking the hit
// opened the chat at message 2,999; the top was ~59 "load older" clicks of
// 50 away. The store now fills the gap to the hit (or to the first message)
// with `through=` requests of up to 1000 rows, and the search hit carries
// the message id to go to.
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: { listMessages: vi.fn(), listConversationsSearch: vi.fn(), searchMessages: vi.fn() },
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});

import { useChatStore } from "./chatStore";

const initial = useChatStore.getState();
const m = (i: number): Message => ({ id: `m${i}`, role: i % 2 ? "assistant" : "user", content: `#${i}`, status: "completed", created_at: new Date(Date.UTC(2026, 8, 1, 0, 0, i)).toISOString() }) as Message;
const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, k) => m(a + k));
const ids = () => (useChatStore.getState().messages.c1 ?? []).map((x) => x.id);

beforeEach(() => {
  vi.clearAllMocks();
  // The chat is open at its newest 50 of 3,000 (m2950..m2999).
  useChatStore.setState({ ...initial, messages: { c1: range(2950, 2999) }, messagesLoaded: { c1: true }, hasMoreMessages: { c1: true }, loadingOlder: {} }, true);
});

describe("FIX132 F12 — loadThrough fills the gap to a search hit", () => {
  it("asks once with through=<hit> from the oldest loaded row, and the hit is then in the list", async () => {
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(2000, 2949), has_more: true });
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(16, 1999), has_more: true });
    const found = await useChatStore.getState().loadThrough("c1", "m16");
    expect(found).toBe(true);
    expect(mockApi.listMessages).toHaveBeenNthCalledWith(1, "c1", { limit: 1000, before: "m2950", through: "m16" });
    expect(mockApi.listMessages).toHaveBeenNthCalledWith(2, "c1", { limit: 1000, before: "m2000", through: "m16" });
    expect(ids()[0]).toBe("m16");
    expect(ids()).toHaveLength(2984);
    expect(useChatStore.getState().hasMoreMessages.c1).toBe(true);
    expect(useChatStore.getState().loadingOlder.c1).toBe(false);
  });

  it("a hit already loaded asks nothing", async () => {
    expect(await useChatStore.getState().loadThrough("c1", "m2960")).toBe(true);
    expect(mockApi.listMessages).not.toHaveBeenCalled();
  });

  it("'first' loads down to the first message", async () => {
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(1950, 2949), has_more: true });
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(950, 1949), has_more: true });
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(0, 949), has_more: false });
    expect(await useChatStore.getState().loadThrough("c1", "first")).toBe(true);
    expect(mockApi.listMessages).toHaveBeenLastCalledWith("c1", { limit: 1000, before: "m950", through: "first" });
    expect(ids()[0]).toBe("m0");
    expect(useChatStore.getState().hasMoreMessages.c1).toBe(false);
  });

  it("a hit that is not in this chat (deleted since) ends false, not in a loop", async () => {
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(0, 2949), has_more: false });
    expect(await useChatStore.getState().loadThrough("c1", "gone")).toBe(false);
    expect(mockApi.listMessages).toHaveBeenCalledTimes(1);
  });

  it("a failed request ends false and leaves what was loaded", async () => {
    mockApi.listMessages.mockRejectedValueOnce(new Error("offline"));
    expect(await useChatStore.getState().loadThrough("c1", "m16")).toBe(false);
    expect(ids()).toHaveLength(50);
    expect(useChatStore.getState().loadingOlder.c1).toBe(false);
  });
});

describe("FIX132 F12 — a content hit names the message", () => {
  it("searchConversations keeps message_id on the hit", async () => {
    mockApi.listConversationsSearch.mockResolvedValue({ conversations: [] });
    mockApi.searchMessages.mockResolvedValue({
      query: "q", scope: "all", total: 1,
      results: [{ type: "message", conversation_id: "c9", conversation_title: "T", message_id: "m16", content: "the phrase", date: "2026-09-01", relevance: "high" }],
    });
    await useChatStore.getState().searchConversations("phrase");
    expect(useChatStore.getState().conversationsSearchHits[0]).toMatchObject({ conversation_id: "c9", message_id: "m16" });
  });
});

describe("FIX132 F20 — a content hit's snippet is plain text", () => {
  it("no **markdown** in the sidebar snippet", async () => {
    mockApi.listConversationsSearch.mockResolvedValue({ conversations: [] });
    mockApi.searchMessages.mockResolvedValue({
      query: "q", scope: "all", total: 1,
      results: [{ type: "message", conversation_id: "c9", conversation_title: "T", message_id: "m1", content: "**ศาลเจ้าฟูชิมิอินาริ** — เปิด 24 ชั่วโมง", date: "2026-09-01", relevance: "high" }],
    });
    await useChatStore.getState().searchConversations("ศาลเจ้า");
    expect(useChatStore.getState().conversationsSearchHits[0].snippet).toBe("ศาลเจ้าฟูชิมิอินาริ — เปิด 24 ชั่วโมง");
  });
});

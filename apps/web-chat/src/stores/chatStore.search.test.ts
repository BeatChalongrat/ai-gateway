// Sidebar search — dual-engine tests (Phase-1 quick-win fix).
//
// searchConversations now fans out to BOTH the legacy title substring
// search and the S6 full-text /search endpoint (scope=messages).
// These tests pin the merge/dedup contract and the failure isolation:
// a dead /search must never take the title results down, and a dead
// title search must never hide content hits.

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    listConversationsSearch: vi.fn(),
    searchMessages: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({ api: mockApi }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

function conv(id: string, title: string): Conversation {
  return {
    id,
    title,
    model_id: "claude-sonnet-4",
    message_count: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  } as Conversation;
}

function messageHit(convId: string, title: string, content: string) {
  return {
    type: "message",
    conversation_id: convId,
    conversation_title: title,
    content,
    role: "user",
    date: "2026-08-01",
    relevance: "0.9",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState(initialState, true);
});

describe("searchConversations (dual engine)", () => {
  it("finds a conversation by CONTENT even when the title does not match", async () => {
    mockApi.listConversationsSearch.mockResolvedValue({
      conversations: [],
      has_more: false,
      next_cursor: null,
    });
    mockApi.searchMessages.mockResolvedValue({
      query: "quarterly",
      scope: "messages",
      results: [messageHit("c1", "Planning chat", "the quarterly numbers are up")],
      total: 1,
    });

    await useChatStore.getState().searchConversations("quarterly");

    const s = useChatStore.getState();
    expect(s.conversationsSearchActive).toBe(true);
    expect(s.conversationsSearchLoading).toBe(false);
    expect(s.conversationsSearchResults).toEqual([]);
    expect(s.conversationsSearchHits).toHaveLength(1);
    expect(s.conversationsSearchHits[0]).toMatchObject({
      conversation_id: "c1",
      conversation_title: "Planning chat",
      snippet: "the quarterly numbers are up",
    });
    expect(mockApi.searchMessages).toHaveBeenCalledWith("quarterly", 20);
  });

  it("dedups: a title match suppresses its content hit; one hit per conversation", async () => {
    mockApi.listConversationsSearch.mockResolvedValue({
      conversations: [conv("c1", "quarterly report")],
      has_more: false,
      next_cursor: null,
    });
    mockApi.searchMessages.mockResolvedValue({
      query: "quarterly",
      scope: "messages",
      results: [
        messageHit("c1", "quarterly report", "dup — already a title match"),
        messageHit("c2", "Other chat", "first hit for c2"),
        messageHit("c2", "Other chat", "second hit for c2 (collapsed)"),
      ],
      total: 3,
    });

    await useChatStore.getState().searchConversations("quarterly");

    const s = useChatStore.getState();
    expect(s.conversationsSearchResults.map((c) => c.id)).toEqual(["c1"]);
    expect(s.conversationsSearchHits).toHaveLength(1);
    expect(s.conversationsSearchHits[0].conversation_id).toBe("c2");
    expect(s.conversationsSearchHits[0].snippet).toBe("first hit for c2");
  });

  it("keeps title results when the FTS endpoint fails", async () => {
    mockApi.listConversationsSearch.mockResolvedValue({
      conversations: [conv("c1", "quarterly report")],
      has_more: false,
      next_cursor: null,
    });
    mockApi.searchMessages.mockRejectedValue(new Error("boom"));

    await useChatStore.getState().searchConversations("quarterly");

    const s = useChatStore.getState();
    expect(s.conversationsSearchLoading).toBe(false);
    expect(s.conversationsSearchResults.map((c) => c.id)).toEqual(["c1"]);
    expect(s.conversationsSearchHits).toEqual([]);
  });

  it("keeps content hits when the title search fails", async () => {
    mockApi.listConversationsSearch.mockRejectedValue(new Error("boom"));
    mockApi.searchMessages.mockResolvedValue({
      query: "quarterly",
      scope: "messages",
      results: [messageHit("c1", "Planning chat", "found in content")],
      total: 1,
    });

    await useChatStore.getState().searchConversations("quarterly");

    const s = useChatStore.getState();
    expect(s.conversationsSearchLoading).toBe(false);
    expect(s.conversationsSearchResults).toEqual([]);
    expect(s.conversationsSearchHits).toHaveLength(1);
  });

  it("both engines down → spinner stops, previous results survive", async () => {
    useChatStore.setState({
      conversationsSearchActive: true,
      conversationsSearchResults: [conv("old", "previous result")],
      conversationsSearchHits: [],
    });
    mockApi.listConversationsSearch.mockRejectedValue(new Error("down"));
    mockApi.searchMessages.mockRejectedValue(new Error("down"));

    await useChatStore.getState().searchConversations("anything");

    const s = useChatStore.getState();
    expect(s.conversationsSearchLoading).toBe(false);
    expect(s.conversationsSearchResults.map((c) => c.id)).toEqual(["old"]);
  });

  it("empty query clears search state without calling either engine", async () => {
    useChatStore.setState({
      conversationsSearchActive: true,
      conversationsSearchResults: [conv("c1", "x")],
      conversationsSearchHits: [
        {
          conversation_id: "c1",
          conversation_title: "x",
          snippet: "y",
          date: "2026-08-01",
        },
      ],
    });

    await useChatStore.getState().searchConversations("   ");

    const s = useChatStore.getState();
    expect(s.conversationsSearchActive).toBe(false);
    expect(s.conversationsSearchResults).toEqual([]);
    expect(s.conversationsSearchHits).toEqual([]);
    expect(mockApi.listConversationsSearch).not.toHaveBeenCalled();
    expect(mockApi.searchMessages).not.toHaveBeenCalled();
  });

  it("truncates long snippets to ~140 chars", async () => {
    const long = "x".repeat(200);
    mockApi.listConversationsSearch.mockResolvedValue({
      conversations: [],
      has_more: false,
      next_cursor: null,
    });
    mockApi.searchMessages.mockResolvedValue({
      query: "q",
      scope: "messages",
      results: [messageHit("c1", "t", long)],
      total: 1,
    });

    await useChatStore.getState().searchConversations("q");

    const snippet = useChatStore.getState().conversationsSearchHits[0].snippet;
    expect(snippet.length).toBe(141); // 140 + ellipsis
    expect(snippet.endsWith("…")).toBe(true);
  });
});

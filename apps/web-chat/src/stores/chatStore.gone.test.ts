// chatStore conversation-gone tests — pins the 404/410 handling for a
// conversation deleted elsewhere (desktop delete propagation, another
// tab): the conversation leaves the sidebar, active/message state is
// cleared, and the banner is the specific "deleted" message — never the
// generic stream-error toast. Mirrors the mocking pattern of
// chatStore.stream.test.ts.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

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

vi.mock("@/hooks/useUsage", () => ({
  bumpUsageVersion: vi.fn(),
}));

import { ApiError } from "@/lib/api";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

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

const userMsg = (id: string): Message => ({
  id,
  role: "user",
  content: "earlier",
  status: "completed",
  created_at: "2026-01-01T00:00:00Z",
});

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockRejectedValue(
    new ApiError("not_found", "conversation not found", 404),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("handleConversationGone", () => {
  it("removes the conversation, clears active + message state, sets the deleted banner", () => {
    useChatStore.setState({
      conversations: [conv("c1"), conv("c2")],
      activeConversationId: "c1",
      messages: { c1: [userMsg("m1")], c2: [] },
      messagesLoaded: { c1: true, c2: true },
      hasMoreMessages: { c1: true },
    });

    useChatStore.getState().handleConversationGone("c1");

    const s = useChatStore.getState();
    expect(s.conversations.map((c) => c.id)).toEqual(["c2"]);
    expect(s.activeConversationId).toBeNull();
    expect(s.messages.c1).toBeUndefined();
    expect(s.messagesLoaded.c1).toBeUndefined();
    expect(s.hasMoreMessages.c1).toBeUndefined();
    expect(s.errorBanner).toBe(dictionaries.en["error.conversationDeleted"]);
  });

  it("leaves a different active conversation untouched", () => {
    useChatStore.setState({
      conversations: [conv("c1"), conv("c2")],
      activeConversationId: "c2",
    });

    useChatStore.getState().handleConversationGone("c1");

    expect(useChatStore.getState().activeConversationId).toBe("c2");
  });
});

describe("loadMessages 404", () => {
  it("routes a 404 through handleConversationGone instead of a generic banner", async () => {
    useChatStore.setState({
      conversations: [conv("c1")],
      activeConversationId: "c1",
    });
    mockApi.listMessages.mockRejectedValue(
      new ApiError("not_found", "conversation not found", 404),
    );

    await useChatStore.getState().loadMessages("c1");

    const s = useChatStore.getState();
    expect(s.conversations).toHaveLength(0);
    expect(s.activeConversationId).toBeNull();
    expect(s.errorBanner).toBe(dictionaries.en["error.conversationDeleted"]);
  });

  it("keeps non-404 failures retry-able with the localized load-failed banner", async () => {
    useChatStore.setState({
      conversations: [conv("c1")],
      activeConversationId: "c1",
    });
    mockApi.listMessages.mockRejectedValue(
      new ApiError("internal_error", "boom", 500),
    );

    await useChatStore.getState().loadMessages("c1");

    const s = useChatStore.getState();
    // Conversation stays — this failure is retry-able, not "gone".
    expect(s.conversations.map((c) => c.id)).toEqual(["c1"]);
    expect(s.activeConversationId).toBe("c1");
    expect(s.messagesLoaded.c1).toBe(true);
    expect(s.errorBanner).toBe(dictionaries.en["error.conversationLoadFailed"]);
  });
});

describe("sendMessage into a deleted conversation", () => {
  it("404 pre-stream response drops the conversation and shows the deleted banner", async () => {
    useChatStore.setState({
      conversations: [conv("c1")],
      activeConversationId: "c1",
      messages: { c1: [userMsg("m1")] },
      messagesLoaded: { c1: true },
    });
    mockApi.openMessageStream.mockResolvedValue({
      ok: false,
      status: 404,
      text: () =>
        Promise.resolve(
          JSON.stringify({
            error: { code: "NOT_FOUND", message: "conversation not found" },
          }),
        ),
    } as unknown as Response);

    await useChatStore.getState().sendMessage("c1", "hello");

    const s = useChatStore.getState();
    expect(s.conversations).toHaveLength(0);
    expect(s.activeConversationId).toBeNull();
    expect(s.messages.c1).toBeUndefined();
    // FIX127 A3 — a send with text says the text was kept (see chatStore.fix127.stranded.test.ts).
    expect(s.errorBanner).toBe(dictionaries.en["error.conversationDeletedKeptText"]);
  });

  it("410 Gone behaves identically to 404", async () => {
    useChatStore.setState({
      conversations: [conv("c1")],
      activeConversationId: "c1",
      messages: { c1: [] },
      messagesLoaded: { c1: true },
    });
    mockApi.openMessageStream.mockResolvedValue({
      ok: false,
      status: 410,
      text: () => Promise.resolve(""),
    } as unknown as Response);

    await useChatStore.getState().sendMessage("c1", "hello");

    const s = useChatStore.getState();
    expect(s.conversations).toHaveLength(0);
    expect(s.errorBanner).toBe(dictionaries.en["error.conversationDeletedKeptText"]);
  });

  it("does not disturb the credits (402) error path", async () => {
    useChatStore.setState({
      conversations: [conv("c1")],
      activeConversationId: "c1",
      messages: { c1: [] },
      messagesLoaded: { c1: true },
    });
    mockApi.openMessageStream.mockResolvedValue({
      ok: false,
      status: 402,
      text: () =>
        Promise.resolve(
          JSON.stringify({
            error: { code: "INSUFFICIENT_CREDITS", message: "out of credits" },
          }),
        ),
    } as unknown as Response);

    await useChatStore.getState().sendMessage("c1", "hello");

    const s = useChatStore.getState();
    // Conversation must remain — only 404/410 mean "gone".
    expect(s.conversations.map((c) => c.id)).toEqual(["c1"]);
    expect(s.errorBanner).not.toBe(
      dictionaries.en["error.conversationDeleted"],
    );
  });
});

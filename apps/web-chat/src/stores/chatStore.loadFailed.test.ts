// FIX86 Part C — "could not load your conversations" is a different fact
// from "you have none". The store records the failure so the sidebar can
// say which one it is.

import { afterEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ listConversationsPage: vi.fn() }));
vi.mock("@/lib/api", async (orig) => {
  const real = (await orig()) as Record<string, unknown>;
  return { ...real, api: { ...(real.api as object), listConversationsPage: apiMock.listConversationsPage } };
});

import { ApiError } from "@/lib/api";
import { useChatStore } from "./chatStore";

afterEach(() => {
  vi.clearAllMocks();
});

describe("conversation list load failure", () => {
  it("a failed load sets conversationsLoadFailed and leaves the list empty", async () => {
    apiMock.listConversationsPage.mockRejectedValueOnce(new ApiError("HTTP_503", "down", 503));
    await useChatStore.getState().loadConversations();
    const s = useChatStore.getState();
    expect(s.conversationsLoaded).toBe(true);
    expect(s.conversationsLoadFailed).toBe(true);
    expect(s.conversations).toEqual([]);
  });

  it("a later successful load clears the flag", async () => {
    apiMock.listConversationsPage.mockResolvedValueOnce({ conversations: [], next_cursor: null, has_more: false });
    await useChatStore.getState().loadConversations();
    expect(useChatStore.getState().conversationsLoadFailed).toBe(false);
  });
});

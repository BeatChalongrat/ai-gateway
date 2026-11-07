// FIX84 row 6 — one click, one outcome. setConversationArchived tells its
// caller whether the server accepted the move, so the archive page's
// success toast can no longer fire on top of the failure banner (FIX83 #4:
// "อัปเดตคลังไม่สำเร็จ" and "ย้ายกลับแถบข้างแล้ว" for the same click).

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    setConversationArchived: vi.fn(),
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

import { useChatStore } from "./chatStore";

const initial = useChatStore.getState();

function conv(id: string, archived: boolean): Conversation {
  return { id, title: id, created_at: "2026-09-08T00:00:00Z", updated_at: "2026-09-08T00:00:00Z", is_archived: archived } as unknown as Conversation;
}

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({ ...initial, conversations: [], archivedConversations: [conv("a1", true)], errorBanner: null }, true);
});

describe("setConversationArchived (FIX84 row 6)", () => {
  it("resolves true when the server accepts, and the row has moved", async () => {
    mockApi.setConversationArchived.mockResolvedValueOnce(undefined);
    const ok = await useChatStore.getState().setConversationArchived("a1", false);
    expect(ok).toBe(true);
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(["a1"]);
    expect(useChatStore.getState().archivedConversations).toEqual([]);
  });

  it("resolves false when the server refuses: the row is rolled back and the failure banner is raised — no success for the caller to announce", async () => {
    mockApi.setConversationArchived.mockRejectedValueOnce(new Error("500"));
    const ok = await useChatStore.getState().setConversationArchived("a1", false);
    expect(ok).toBe(false);
    expect(useChatStore.getState().conversations).toEqual([]);
    expect(useChatStore.getState().archivedConversations.map((c) => c.id)).toEqual(["a1"]);
    expect(useChatStore.getState().errorBanner).toBeTruthy();
  });

  it("resolves false for an unknown id without calling the server", async () => {
    const ok = await useChatStore.getState().setConversationArchived("nope", false);
    expect(ok).toBe(false);
    expect(mockApi.setConversationArchived).not.toHaveBeenCalled();
  });
});

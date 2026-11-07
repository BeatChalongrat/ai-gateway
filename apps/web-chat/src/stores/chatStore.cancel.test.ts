// P2 (Phase 6a defect round) — cancel must be reflected in the UI on click,
// not ~1.5s later when the host confirms. cancelGeneration now optimistically
// clears the streaming slots (so useIsStreaming flips the composer back to
// Send) and finalizes the in-flight message as "cancelled" (so the "Working…"
// indicator disappears) BEFORE awaiting the network cancel.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    cancelGeneration: vi.fn(),
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

const initialState = useChatStore.getState();

function streamingMessage(id: string): Message {
  return {
    id,
    role: "assistant",
    content: "partial answer so far",
    status: "streaming",
    created_at: "2026-01-01T00:00:00Z",
    isStreaming: true,
  };
}

describe("cancelGeneration — optimistic UI", () => {
  beforeEach(() => {
    useChatStore.setState({ ...initialState }, true);
    vi.clearAllMocks();
    // never resolves during the synchronous assertions below
    mockApi.cancelGeneration.mockReturnValue(new Promise(() => {}));
  });
  afterEach(() => useChatStore.setState({ ...initialState }, true));

  it("clears the streaming slots and finalizes the message synchronously on click", () => {
    const convId = "c1";
    const ctrl = new AbortController();
    const abortSpy = vi.spyOn(ctrl, "abort");
    useChatStore.setState({
      messages: { [convId]: [streamingMessage("m1")] },
      abortControllers: { [convId]: ctrl },
      streamingAssistantId: { [convId]: "m1" },
    });

    // Fire and forget — do NOT await; the optimistic updates run before the
    // first `await api.cancelGeneration`.
    void useChatStore.getState().cancelGeneration(convId);

    const s = useChatStore.getState();
    expect(s.streamingAssistantId[convId]).toBeUndefined();
    expect(s.abortControllers[convId]).toBeUndefined();
    const msg = s.messages[convId].find((m) => m.id === "m1")!;
    expect(msg.isStreaming).toBe(false);
    expect(msg.status).toBe("cancelled");
    expect(abortSpy).toHaveBeenCalled();
    expect(mockApi.cancelGeneration).toHaveBeenCalledWith(convId);
  });

  it("is a no-op-safe call when nothing is streaming", () => {
    // Should not throw even with no controller / no streaming id.
    expect(() =>
      void useChatStore.getState().cancelGeneration("nope"),
    ).not.toThrow();
  });
});

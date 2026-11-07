// FIX34 Part A — the quick-ask window page.
//
// Pins the Claude quick-entry contract this page mirrors:
//   * the empty state offers the FIVE most recent non-empty chats;
//   * Esc on a quiet window asks the host to close it;
//   * Esc while a reply streams STOPS the reply and does NOT close;
//   * Ctrl+Shift+N and Claude's Ctrl+Shift+O both start a new chat;
//   * the page marks the bridge as quick-ask (tool-less turns) while mounted.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockHost } = vi.hoisted(() => ({
  mockApi: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), listConversationsPage: vi.fn() },
  mockHost: { quickClose: vi.fn(async () => ({ ok: true })), quickOpenInMain: vi.fn(async () => ({ ok: true })) },
}));

vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/lib/hostApi", () => ({ hostApi: mockHost }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => true }));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { bridge } from "@/lib/bridge";
import { useI18nStore } from "@/lib/i18n";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { pickRecentConversations, QUICK_RECENT_COUNT } from "@/components/quick/QuickAsk";

import QuickAskPage from "./page";

const chatInitial = useChatStore.getState();

function conv(i: number, extra?: Partial<Conversation>): Conversation {
  return {
    id: `c${i}`,
    title: `Chat ${i}`,
    model_id: "auto",
    message_count: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...extra,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial, conversationsLoaded: true }, true);
  useModelStore.setState({ models: [], selectedModelId: "auto" });
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  bridge.resetForTest();
});

describe("pickRecentConversations", () => {
  it("returns the 5 newest non-empty chats in store order", () => {
    const all = [conv(1, { message_count: 0 }), conv(2), conv(3), conv(4), conv(5), conv(6), conv(7), conv(8)];
    const picked = pickRecentConversations(all);
    expect(QUICK_RECENT_COUNT).toBe(5);
    expect(picked.map((c) => c.id)).toEqual(["c2", "c3", "c4", "c5", "c6"]);
  });
});

describe("QuickAsk page", () => {
  it("shows five recent chats and marks the bridge as quick-ask while mounted", () => {
    useChatStore.setState({ conversations: [1, 2, 3, 4, 5, 6, 7].map((i) => conv(i)) });
    const { unmount } = render(<QuickAskPage />);
    expect(bridge.isQuickAsk).toBe(true);
    const list = screen.getByTestId("quick-recents");
    expect(list.querySelectorAll("li").length).toBe(5);
    expect(list.textContent).toContain("Chat 1");
    expect(list.textContent).not.toContain("Chat 6");
    unmount();
    expect(bridge.isQuickAsk).toBe(false);
  });

  it("Esc on a quiet window closes it via the host", () => {
    render(<QuickAskPage />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(mockHost.quickClose).toHaveBeenCalledTimes(1);
  });

  it("Esc while streaming stops the reply and does not close", () => {
    const cancelGeneration = vi.fn(async () => {});
    useChatStore.setState({
      conversations: [conv(1)],
      activeConversationId: "c1",
      abortControllers: { c1: new AbortController() },
      cancelGeneration,
    });
    render(<QuickAskPage />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(cancelGeneration).toHaveBeenCalledWith("c1");
    expect(mockHost.quickClose).not.toHaveBeenCalled();
  });

  it("a slash menu that consumed Esc wins (defaultPrevented)", () => {
    render(<QuickAskPage />);
    const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    ev.preventDefault();
    act(() => {
      window.dispatchEvent(ev);
    });
    expect(mockHost.quickClose).not.toHaveBeenCalled();
  });

  it("continuing a recent chat shows New chat / Open in main window; Ctrl+Shift+O starts over", () => {
    useChatStore.setState({ conversations: [conv(1)], messagesLoaded: { c1: true }, messages: { c1: [] } });
    render(<QuickAskPage />);
    fireEvent.click(screen.getByText("Chat 1"));
    expect(screen.getByTestId("quick-open-main")).toBeTruthy();
    expect(useChatStore.getState().activeConversationId).toBe("c1");
    fireEvent.keyDown(window, { key: "O", ctrlKey: true, shiftKey: true });
    expect(screen.queryByTestId("quick-open-main")).toBeNull();
    expect(useChatStore.getState().activeConversationId).toBeNull();
  });

  it("Open in main window turns memory back on, then hands the chat to the host", async () => {
    const setConversationMemory = vi.fn(async () => {});
    useChatStore.setState({ conversations: [conv(1)], messagesLoaded: { c1: true }, messages: { c1: [] }, setConversationMemory });
    render(<QuickAskPage />);
    fireEvent.click(screen.getByText("Chat 1"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("quick-open-main"));
    });
    expect(setConversationMemory).toHaveBeenCalledWith("c1", true);
    expect(mockHost.quickOpenInMain).toHaveBeenCalledWith("c1");
  });
});

// FIX34 Part B — sran://…/new?q=… → /chat?q=…: the composer is PREFILLED and
// nothing is sent. This is the security property, not a nicety: a page that
// could auto-send through a deep link could make the app ask anything with one
// click (Anthropic: "Prompts are prefilled but not automatically sent").

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockRouter } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    listSkills: vi.fn(async () => ({ skills: [] })),
  },
  mockRouter: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("next/navigation", () => ({ useRouter: () => mockRouter, usePathname: () => "/chat" }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import ChatLanding from "./page";

const chatInitial = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear(); // the composer persists drafts (Batch 1 W1)
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
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
  window.history.replaceState(null, "", "/chat");
});

describe("/chat?q= (deep-link prefill)", () => {
  it("puts q in the composer, strips it from the URL, and sends NOTHING", async () => {
    const sendMessage = vi.fn(async () => {});
    const createConversation = vi.fn(async () => ({ id: "c-new" }));
    useChatStore.setState({ sendMessage, createConversation } as never);
    window.history.replaceState(null, "", "/chat?q=What%20is%20DLP%3F");

    await act(async () => {
      render(<ChatLanding />);
    });
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    await waitFor(() => expect(ta.value).toBe("What is DLP?"));
    expect(window.location.search).toBe("");
    // Nothing was created or sent — the user still has to press Send.
    expect(sendMessage).not.toHaveBeenCalled();
    expect(createConversation).not.toHaveBeenCalled();
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it("without q the composer is empty", async () => {
    await act(async () => {
      render(<ChatLanding />);
    });
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });
});

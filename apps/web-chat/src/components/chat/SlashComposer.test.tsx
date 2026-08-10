// Phase 10 Task 2 — the slash menu inside the REAL composer.
//
// The pure trigger rule is pinned in Phase10Ux.test.tsx; this file
// mounts ChatInput itself, because the thing that would actually hurt
// users is a regression in the composer: the menu failing to appear, or
// worse, appearing while they type normal prose. It also pins that the
// Phase-7 Enter/Shift+Enter/IME behaviour still holds with the slash
// key handling layered on top.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

// The composer's voice hooks reach for getUserMedia / speech APIs that
// jsdom doesn't have; stub them to their "unsupported" shape.
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({
    supported: false,
    isRecording: false,
    start: vi.fn(),
    stop: vi.fn(),
    duration: 0,
  }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({
    isTranscribing: false,
    isRecording: false,
    start: vi.fn(),
    stop: vi.fn(),
    duration: 0,
  }),
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [] });
  if (!window.matchMedia) {
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
  }
});

function mount(onSend = vi.fn()) {
  render(
    <ChatInput
      conversationId="c1"
      disabled={false}
      streaming={false}
      activeModelId={AUTO_MODEL_ID}
      onSend={onSend}
      onStop={vi.fn()}
    />,
  );
  return {
    textarea: screen.getByRole("textbox") as HTMLTextAreaElement,
    onSend,
  };
}

describe("slash menu in the composer", () => {
  it("opens on a bare '/' and lists the quick actions", () => {
    const { textarea } = mount();
    expect(screen.queryByTestId("slash-menu")).toBeNull();

    fireEvent.change(textarea, { target: { value: "/" } });

    expect(screen.getByTestId("slash-menu")).toBeInTheDocument();
    expect(screen.getByText(dictionaries.en["slash.image"])).toBeInTheDocument();
    expect(screen.getByText(dictionaries.en["slash.shortcuts"])).toBeInTheDocument();
  });

  it("narrows as the user types and closes once the token ends", () => {
    const { textarea } = mount();

    fireEvent.change(textarea, { target: { value: "/img" } });
    expect(screen.getByText(dictionaries.en["slash.image"])).toBeInTheDocument();
    expect(screen.queryByText(dictionaries.en["slash.new"])).toBeNull();

    // A space ends the token — this is ordinary text again.
    fireEvent.change(textarea, { target: { value: "/img of a cat" } });
    expect(screen.queryByTestId("slash-menu")).toBeNull();
  });

  it("does NOT open for a slash inside ordinary prose", () => {
    const { textarea } = mount();
    for (const value of ["and/or", "open 24/7", "https://x.dev/docs"]) {
      fireEvent.change(textarea, { target: { value } });
      expect(screen.queryByTestId("slash-menu"), value).toBeNull();
    }
  });

  it("runs the highlighted action on Enter and clears the composer", () => {
    const { textarea, onSend } = mount();
    fireEvent.change(textarea, { target: { value: "/shortcuts" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    // The cheatsheet opened instead of a message being sent.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(textarea.value).toBe("");
  });

  it("Escape dismisses the menu without sending", () => {
    const { textarea, onSend } = mount();
    fireEvent.change(textarea, { target: { value: "/" } });
    fireEvent.keyDown(textarea, { key: "Escape" });

    expect(screen.queryByTestId("slash-menu")).toBeNull();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("leaves normal Enter-to-send untouched (Phase 7 behaviour)", () => {
    const { textarea, onSend } = mount();
    fireEvent.change(textarea, { target: { value: "สวัสดีครับ" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0]).toBe("สวัสดีครับ");
  });

  it("Shift+Enter still makes a newline, and IME Enter still never sends", () => {
    const { textarea, onSend } = mount();

    fireEvent.change(textarea, { target: { value: "line one" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });
});

// Batch 1 W1 — composer draft persistence.
//
// Pins the desktop-parity contract (ChatViewModel.cs Phase 3 C4):
// typed-but-unsent text survives an unmount/remount (conversation
// switch, reload), sending deletes the draft, and drafts are keyed
// per conversation so two chats never leak text into each other.

import { cleanup, fireEvent, render } from "@testing-library/react";
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

import { clearDraftStore, loadDraft } from "@/lib/draftStore";
import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [] });
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
  vi.stubGlobal(
    "requestAnimationFrame",
    (cb: FrameRequestCallback): number => {
      cb(0);
      return 0;
    },
  );
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
});

function mount(conversationId: string, onSend = vi.fn()) {
  const view = render(
    <ChatInput
      conversationId={conversationId}
      disabled={false}
      streaming={false}
      activeModelId={AUTO_MODEL_ID}
      onSend={onSend}
      onStop={vi.fn()}
    />,
  );
  const textarea = view.container.querySelector(
    "textarea",
  ) as HTMLTextAreaElement;
  return { view, textarea, onSend };
}

describe("composer draft persistence", () => {
  it("restores typed text after unmount → remount (flush on unmount)", () => {
    const first = mount("c1");
    fireEvent.change(first.textarea, { target: { value: "three paragraphs of thought" } });
    // No debounce wait: the unmount flush must catch a fast click-away.
    first.view.unmount();

    expect(loadDraft("c1")).toBe("three paragraphs of thought");

    const second = mount("c1");
    expect(second.textarea.value).toBe("three paragraphs of thought");
  });

  it("saves via the ~1s debounce while the composer stays mounted", () => {
    vi.useFakeTimers();
    try {
      const { textarea } = mount("c1");
      fireEvent.change(textarea, { target: { value: "still typing" } });
      expect(loadDraft("c1")).toBeNull();
      vi.advanceTimersByTime(1100);
      expect(loadDraft("c1")).toBe("still typing");
    } finally {
      vi.useRealTimers();
    }
  });

  it("clears the draft on send", () => {
    const onSend = vi.fn();
    const first = mount("c1", onSend);
    fireEvent.change(first.textarea, { target: { value: "send me" } });
    fireEvent.keyDown(first.textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("send me", [], false, false);
    expect(first.textarea.value).toBe("");
    expect(loadDraft("c1")).toBeNull();

    first.view.unmount();
    const second = mount("c1");
    expect(second.textarea.value).toBe("");
  });

  it("keeps drafts separate per conversation", () => {
    const a = mount("c1");
    fireEvent.change(a.textarea, { target: { value: "draft for c1" } });
    a.view.unmount();

    const b = mount("c2");
    expect(b.textarea.value).toBe("");
    fireEvent.change(b.textarea, { target: { value: "draft for c2" } });
    b.view.unmount();

    const back = mount("c1");
    expect(back.textarea.value).toBe("draft for c1");
    back.view.unmount();

    expect(loadDraft("c1")).toBe("draft for c1");
    expect(loadDraft("c2")).toBe("draft for c2");
  });

  it("deletes the entry when the composer is emptied", () => {
    vi.useFakeTimers();
    try {
      const { textarea } = mount("c1");
      fireEvent.change(textarea, { target: { value: "temp" } });
      vi.advanceTimersByTime(1100);
      expect(loadDraft("c1")).toBe("temp");
      fireEvent.change(textarea, { target: { value: "" } });
      vi.advanceTimersByTime(1100);
      expect(loadDraft("c1")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

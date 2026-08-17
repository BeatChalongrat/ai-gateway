// P1 focus fix — composer focus after a stream ends.
//
// Pins the restore-only-when-lost contract: when `streaming` flips
// true→false the textarea is refocused ONLY if focus is nowhere useful
// (body/null/detached node). Focus the user deliberately moved into
// another field stays put, an already-focused composer is not
// re-focused (no caret reset), touch devices never auto-focus (soft
// keyboard), and nothing fires on mount.

import { cleanup, render } from "@testing-library/react";
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
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

// The restore effect defers one frame so the stop→send swap has
// committed before it reads activeElement; run rAF synchronously so
// each assertion sees the settled state without visual-timer polling.
let pointerFine = true;
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [] });
  pointerFine = true;
  window.matchMedia = ((q: string) => ({
    matches: q === "(pointer: fine)" ? pointerFine : false,
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
});

function mount(streaming: boolean) {
  const props = {
    conversationId: "c1",
    disabled: false,
    activeModelId: AUTO_MODEL_ID,
    onSend: vi.fn(),
    onStop: vi.fn(),
  };
  const view = render(<ChatInput {...props} streaming={streaming} />);
  const textarea = view.container.querySelector(
    "textarea",
  ) as HTMLTextAreaElement;
  return {
    textarea,
    finishStream: () =>
      view.rerender(<ChatInput {...props} streaming={false} />),
  };
}

describe("composer focus after a stream ends", () => {
  it("refocuses the textarea when focus was lost to <body>", () => {
    const { textarea, finishStream } = mount(true);
    (document.activeElement as HTMLElement | null)?.blur?.();
    expect(document.activeElement).toBe(document.body);

    finishStream();

    expect(document.activeElement).toBe(textarea);
  });

  it("does NOT move focus the user placed in another field", () => {
    const { textarea, finishStream } = mount(true);
    const other = document.createElement("input");
    document.body.appendChild(other);
    other.focus();

    finishStream();

    expect(document.activeElement).toBe(other);
    expect(document.activeElement).not.toBe(textarea);
    other.remove();
  });

  it("does not re-call focus() when the textarea already has it", () => {
    const { textarea, finishStream } = mount(true);
    textarea.focus();
    const focusSpy = vi.spyOn(textarea, "focus");

    finishStream();

    expect(focusSpy).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(textarea);
  });

  it("never auto-focuses on touch devices (no soft-keyboard pop)", () => {
    pointerFine = false;
    const { textarea, finishStream } = mount(true);
    (document.activeElement as HTMLElement | null)?.blur?.();

    finishStream();

    expect(document.activeElement).not.toBe(textarea);
    expect(document.activeElement).toBe(document.body);
  });

  it("does not fire on mount when streaming is already false", () => {
    const { textarea } = mount(false);

    expect(document.activeElement).not.toBe(textarea);
    expect(document.activeElement).toBe(document.body);
  });
});

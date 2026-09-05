// FIX62 — an explicit image request typed at a model that cannot make
// images is HELD in the composer, not sent: the way out is named, the
// text stays, and sending the same text again lets it through as a
// plain question. The composer is shared by the web and the desktop, so
// this is the one gate both paths pass through.
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const { mockApi } = vi.hoisted(() => ({
  mockApi: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
import { clearDraftStore } from "@/lib/draftStore";
import { dictionaries } from "@/lib/i18n/dict";
import { useI18nStore } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

function model(partial: Partial<ModelEntry> & { id: string }): ModelEntry {
  return {
    name: partial.id,
    provider: "test",
    description: "",
    context_window: 128000,
    capabilities: [],
    icon: "",
    category: "fast" as ModelEntry["category"],
    allowed: true,
    ...partial,
  };
}

const NO_IMAGES = model({ id: "qwen-text-only", name: "Qwen (text)" });
const WITH_IMAGES = model({
  id: "tooly",
  name: "Tooly",
  capabilities: ["function_calling", "image_generation_via_tools"],
});

beforeEach(() => {
  vi.clearAllMocks();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [NO_IMAGES, WITH_IMAGES] });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
});

function mount(activeModelId: string) {
  const onSend = vi.fn();
  const props = { conversationId: "c1", disabled: false, streaming: false, onSend, onStop: vi.fn() };
  const view = render(<ChatInput {...props} activeModelId={activeModelId} />);
  const textarea = () => view.container.querySelector("textarea") as HTMLTextAreaElement;
  const notice = () => view.container.querySelector('[data-testid="image-intent-blocked"]');
  const type = (text: string) => act(() => { fireEvent.change(textarea(), { target: { value: text } }); });
  const pressEnter = () => act(() => { fireEvent.keyDown(textarea(), { key: "Enter", code: "Enter" }); });
  return { onSend, textarea, notice, type, pressEnter };
}

describe("image intent at a model that cannot make images (FIX62)", () => {
  it("holds the request, names the way out, keeps the text — nothing is sent", () => {
    const { onSend, textarea, notice, type, pressEnter } = mount(NO_IMAGES.id);
    type("ทำรูปแมว");
    pressEnter();
    expect(onSend).not.toHaveBeenCalled();
    expect(notice()).not.toBeNull();
    expect(notice()!.textContent).toBe(
      dictionaries.en["input.imageIntent.blocked"].replace("{model}", NO_IMAGES.name),
    );
    expect(textarea().value).toBe("ทำรูปแมว");
  });

  it("sending the same text again lets it through as a plain question", () => {
    const { onSend, notice, type, pressEnter } = mount(NO_IMAGES.id);
    type("ทำรูปแมว");
    pressEnter();
    expect(onSend).not.toHaveBeenCalled();
    pressEnter();
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0]).toBe("ทำรูปแมว");
    expect(notice()).toBeNull();
  });

  it("a model that can make images is never held", () => {
    const { onSend, notice, type, pressEnter } = mount(WITH_IMAGES.id);
    type("ทำรูปแมว");
    pressEnter();
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(notice()).toBeNull();
  });

  it("ordinary text at the same model is never held", () => {
    const { onSend, notice, type, pressEnter } = mount(NO_IMAGES.id);
    type("คุณคือใคร");
    pressEnter();
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(notice()).toBeNull();
  });

  it("the Thai reason exists", () => {
    expect(dictionaries.th["input.imageIntent.blocked"]).toContain("🎨");
  });
});

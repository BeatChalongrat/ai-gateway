// FIX47.6 Part C / FIX65 — the tool wall becomes unreachable in the composer:
//
//  * a model that cannot make images has NO 🎨 button (FIX65: the picker
//    curates vendor models to a floor and labels the operator's own, so
//    an absent button is the honest shape; FIX47.6's visible-but-disabled
//    button belonged to a picker that offered every model);
//  * switching to such a model while image mode is ON drops the mode WITH
//    a toast naming the model — never silently;
//  * the FIX47 backstop error is untouched (server-side; not this test).

import { act, cleanup, render } from "@testing-library/react";
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
import { useToastStore } from "@/components/ui/Toast";
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

const NO_TOOLS = model({ id: "plain-chat", name: "Plain Chat" });
const WITH_TOOLS = model({
  id: "tooly",
  name: "Tooly",
  capabilities: ["function_calling", "image_generation_via_tools"],
});

beforeEach(() => {
  vi.clearAllMocks();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [NO_TOOLS, WITH_TOOLS] });
  useToastStore.setState({ message: null, nonce: 0 });
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
  const props = { conversationId: "c1", disabled: false, streaming: false, onSend: vi.fn(), onStop: vi.fn() };
  const view = render(<ChatInput {...props} activeModelId={activeModelId} />);
  const imageBtn = () => view.container.querySelector('[data-testid="image-mode-button"]') as HTMLButtonElement | null;
  return {
    view, imageBtn,
    switchModel: (id: string) => view.rerender(<ChatInput {...props} activeModelId={id} />),
  };
}

describe("tools affordance (FIX47.6 Part C)", () => {
  it("a model that cannot make images has NO image button (FIX65) — and nothing inert in its place", () => {
    const { imageBtn, view } = mount(NO_TOOLS.id);
    expect(imageBtn()).toBeNull();
    expect(view.container.querySelector('[aria-disabled="true"][data-testid="image-mode-button"]')).toBeNull();
  });

  it("a tool-capable model gets the button enabled with the normal label", () => {
    const { imageBtn } = mount(WITH_TOOLS.id);
    const btn = imageBtn();
    expect(btn).not.toBeNull();
    expect(btn!.disabled).toBe(false);
    expect(btn!.title).toBe(dictionaries.en["input.imageMode"]);
  });

  it("switching to a tools-blind model with image mode ON drops the mode WITH a toast and the button goes away", () => {
    const { imageBtn, switchModel } = mount(WITH_TOOLS.id);
    act(() => { imageBtn()!.click(); });
    expect(imageBtn()!.getAttribute("aria-pressed")).toBe("true");
    act(() => { switchModel(NO_TOOLS.id); });
    expect(imageBtn()).toBeNull();
    const toastMsg = useToastStore.getState().message;
    expect(toastMsg).toContain("Image mode turned off");
    expect(toastMsg).toContain(NO_TOOLS.name);
    // Back on a capable model the button returns, off.
    act(() => { switchModel(WITH_TOOLS.id); });
    expect(imageBtn()).not.toBeNull();
    expect(imageBtn()!.getAttribute("aria-pressed")).toBe("false");
  });

  it("Thai reasons exist", () => {
    expect(dictionaries.th["input.imageMode.needsTools"]).toBeTruthy();
    expect(dictionaries.th["input.imageMode.droppedForModel"]).toBeTruthy();
    expect(dictionaries.th["input.videoMode.needsTools"]).toBeTruthy();
    expect(dictionaries.th["input.videoMode.droppedForModel"]).toBeTruthy();
  });
});

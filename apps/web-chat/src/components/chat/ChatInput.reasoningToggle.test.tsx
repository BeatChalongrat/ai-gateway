// FIX125 M3 — the thinking toggle (โหมดคิดลึก) must not offer OFF for a
// model that cannot stop thinking. The server lists "off" in
// reasoning.modes only for models that really can; without it the toggle
// is shown on, disabled, with a localized "always thinks" label.

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn() },
  ApiError: class extends Error {},
}));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { useI18nStore } from "@/lib/i18n";
import type { Conversation, ModelEntry } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

function model(id: string, modes: string[]): ModelEntry {
  return {
    id,
    name: id,
    provider: "p",
    description: "",
    context_window: 131072,
    capabilities: ["chat"],
    icon: "",
    category: "reasoning",
    allowed: true,
    reasoning: { supported: true, modes, default_mode: modes[0] ?? "on", summary_supported: false },
  } as ModelEntry;
}
const conv = (reasoning_enabled: boolean) =>
  ({ id: "c1", title: "t", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", reasoning_enabled }) as Conversation;

const setReasoning = vi.fn(async () => {});

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({ ...chatInitial }, true);
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

function mount(modes: string[], reasoningEnabled: boolean, locale: "en" | "th" = "en") {
  useI18nStore.setState({ locale });
  useModelStore.setState({ models: [model("think-only", modes)] });
  useChatStore.setState({ conversations: [conv(reasoningEnabled)], setConversationReasoning: setReasoning } as never);
  const view = render(
    <ChatInput conversationId="c1" disabled={false} streaming={false} activeModelId="think-only" onSend={vi.fn()} onStop={vi.fn()} />,
  );
  return view.getByTestId("reasoning-toggle") as HTMLButtonElement;
}

describe("FIX125 M3 — thinking toggle", () => {
  it("a model whose modes lack \"off\" shows always-on, cannot be toggled, and says why", () => {
    const btn = mount(["on", "auto"], false);
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute("aria-disabled")).toBe("true");
    // never shown OFF, even though the conversation's flag is false
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn.title).toBe("This model always thinks — thinking can't be turned off");
    expect(btn.getAttribute("aria-label")).toBe("This model always thinks — thinking can't be turned off");
    fireEvent.click(btn);
    expect(setReasoning).not.toHaveBeenCalled();
  });

  it("the always-on label is localized (Thai)", () => {
    const btn = mount(["on"], false, "th");
    expect(btn.title).toBe("โมเดลนี้คิดทุกครั้ง ปิดโหมดคิดไม่ได้");
  });

  it("a model with \"off\" in modes toggles as before", () => {
    const btn = mount(["off", "on"], false);
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(btn.title).toBe("Thinking mode off — turn on for harder questions");
    fireEvent.click(btn);
    expect(setReasoning).toHaveBeenCalledWith("c1", { reasoning_enabled: true });
  });
});

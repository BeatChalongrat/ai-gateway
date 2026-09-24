// FIX132 D3 — five file chips at 360 px pushed the composer off the screen.
// Measured on the live page (360×740, a [FIX132] long chat, five PDFs attached,
// uploads answered by the probe): each chip is a 160-px card, one per row, so
// five of them made the composer ~830 px tall. The text box sat at y=972 in a
// 740-px window (Send out of reach), the thread vanished, and the floating
// credits pill — placed "above the composer" — landed on the header's buttons
// (scripts/probes/FIX132/results/lanes/lane-ux/probe/d3b-360-five-chips.png).
//
// Now the chip row never grows without bound: on a phone it is ONE row that
// scrolls sideways (as ChatGPT and Claude show attachments), and from sm up it
// wraps but stops at a height cap and scrolls inside. jsdom has no layout, so
// this pins the classes that make it so; the live probe shows the geometry.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), deleteFile: vi.fn(), deleteFileByClientId: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));

import { clearDraftStore } from "@/lib/draftStore";
import { useI18nStore } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { _resetUploadRegistry } from "@/lib/uploadRegistry";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();
const MODEL = { id: "m", name: "m", provider: "t", description: "", context_window: 128000, capabilities: [], icon: "", category: "fast", allowed: true } as unknown as ModelEntry;

beforeEach(() => {
  vi.clearAllMocks();
  _resetUploadRegistry();
  clearDraftStore();
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [MODEL] });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.uploadFile.mockImplementation(async (_c: string, f: File) => ({ id: `srv-${f.name}`, extraction_status: "completed" }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 D3 — the attachment row cannot push the composer off a phone screen", () => {
  it("five chips: one sideways-scrolling row on a phone, a height-capped wrap from sm up", async () => {
    const view = render(<ChatInput conversationId="c1" disabled={false} streaming={false} onSend={vi.fn()} onStop={vi.fn()} activeModelId="m" />);
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [1, 2, 3, 4, 5].map((i) => new File(["x"], `รายงาน-${i}.pdf`, { type: "application/pdf" }));
    await act(async () => {
      fireEvent.change(input, { target: { files } });
      await new Promise((r) => setTimeout(r, 0));
    });
    const row = screen.getByTestId("composer-chip-row");
    const cls = row.className.split(/\s+/);
    // phone: one row, scrolls sideways, never wraps into five stacked rows
    expect(cls).toContain("flex-nowrap");
    expect(cls).toContain("overflow-x-auto");
    // sm and up: wraps, but stops at a cap and scrolls inside
    expect(cls).toContain("sm:flex-wrap");
    expect(cls.some((c) => /^sm:max-h-\[/.test(c))).toBe(true);
    expect(cls).toContain("sm:overflow-y-auto");
    // the cards keep their width in the sideways row (a squeezed card is unreadable)
    const cards = [...row.children] as HTMLElement[];
    expect(cards).toHaveLength(5);
    for (const c of cards) expect(c.className.split(/\s+/)).toContain("shrink-0");
    // it is a named region a keyboard/screen-reader user can scroll
    expect(row.getAttribute("role")).toBe("group");
    expect(row.getAttribute("aria-label")).toBeTruthy();
  });
});

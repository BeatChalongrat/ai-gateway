// FIX75 — the stop button: neutral fill, breathing halo.
//
// The streaming state of the composer's send/stop button used to be
// `bg-status-danger-solid` — the error red, 2.77:1 against the dark
// composer surface (WCAG 1.4.11 wants 3:1 for a control against what it
// sits on) and the failure colour spent on a safe, reversible action.
// Now both the send-ready and the stop state take ONE class
// (`composer-send-fill`: --text-primary fill, --bg-primary icon) so they
// cannot drift apart, and the stop state adds a box-shadow-only halo
// that never moves the hit target and goes static under
// prefers-reduced-motion. These tests go red if any of that is undone.

import fs from "node:fs";
import path from "node:path";

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
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const GLOBALS = fs.readFileSync(path.join(ROOT, "src", "app", "globals.css"), "utf8");

const chatInitial = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
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
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

function mount(streaming: boolean) {
  const props = { conversationId: "c1", disabled: false, activeModelId: AUTO_MODEL_ID, onSend: vi.fn(), onStop: vi.fn() };
  const view = render(<ChatInput {...props} streaming={streaming} />);
  const textarea = view.container.querySelector("textarea") as HTMLTextAreaElement;
  return { view, textarea, props };
}

/** The one CSS rule block for a selector (first match), as text. */
function rule(selector: string): string {
  const i = GLOBALS.indexOf(`${selector} {`);
  if (i < 0) throw new Error(`no rule for ${selector}`);
  return GLOBALS.slice(i, GLOBALS.indexOf("}", i));
}

describe("FIX75 — the stop button", () => {
  it("streaming: the fill is the shared neutral class, never a status colour", () => {
    const { view } = mount(true);
    const stop = view.getByLabelText("Stop generation") as HTMLButtonElement;
    expect(stop.className).toContain("composer-send-fill");
    expect(stop.className).toContain("stop-halo");
    // a status token reintroduced here (bg-status-danger-solid, text-white,
    // an inline red) is exactly the defect this round removed
    expect(stop.className).not.toMatch(/status-|danger|error|bg-\w|text-white/);
    expect(stop.style.background).toBe("");
    expect(stop.style.color).toBe("");
    expect(stop.disabled).toBe(false);
  });

  it("the class resolves to --text-primary / --bg-primary, not --error-solid", () => {
    const fill = rule(".composer-send-fill");
    expect(fill).toMatch(/background:\s*var\(--text-primary\)/);
    expect(fill).toMatch(/color:\s*var\(--bg-primary\)/);
    expect(fill).not.toMatch(/error|danger|status/);
    expect(rule(".composer-send-fill:hover")).toMatch(/background:\s*var\(--accent-hover\)/);
  });

  it("send-ready and stop derive their fill from the same class, so they cannot drift", () => {
    const { view, textarea } = mount(false);
    const send = view.getByLabelText("Send") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(send.className).not.toContain("composer-send-fill");
    fireEvent.change(textarea, { target: { value: "hello" } });
    expect(send.disabled).toBe(false);
    expect(send.className).toContain("composer-send-fill");
    expect(send.className).not.toContain("stop-halo"); // the halo is streaming-only
    expect(send.style.background).toBe("");
    // one class name in the source, used by both branches
    const src = fs.readFileSync(path.join(__dirname, "ChatInput.tsx"), "utf8");
    expect(src.match(/composer-send-fill/g)?.length).toBeGreaterThanOrEqual(2);
    expect(src).not.toMatch(/bg-status-danger-solid/);
  });

  it("the halo moves nothing: box-shadow only, streaming only, 2.4 s ease-in-out", () => {
    const kf = GLOBALS.slice(GLOBALS.indexOf("@keyframes stop-halo"), GLOBALS.indexOf(".stop-halo {"));
    expect(kf).toMatch(/box-shadow/);
    // only box-shadow is animated — no property that moves or resizes the box
    expect(kf).not.toMatch(/(^|[{;])\s*(transform|width|height|margin|padding|top|left|right|bottom|inset|scale|translate)\s*:/m);
    expect(kf).toMatch(/rgb\(var\(--text-primary-rgb\)/); // theme-aware, never a hardcoded white
    expect(kf).not.toMatch(/#fff|255 255 255|white/i);
    expect(rule(".stop-halo")).toMatch(/animation:\s*stop-halo 2\.4s ease-in-out infinite/);
  });

  it("reduced motion: no animation, a static ring at mid-strength", () => {
    const rm = GLOBALS.slice(GLOBALS.indexOf("@media (prefers-reduced-motion: reduce)"));
    const halo = rm.slice(rm.indexOf(".stop-halo {"), rm.indexOf("}", rm.indexOf(".stop-halo {")));
    expect(halo).toMatch(/animation:\s*none/);
    expect(halo).toMatch(/box-shadow:\s*0 0 0 3px rgb\(var\(--text-primary-rgb\) \/ 0\.16\)/);
  });

  it("the square icon (11 × 11, radius 3) and the label carry 'stop' in EN and TH", () => {
    const { view } = mount(true);
    const rect = view.getByLabelText("Stop generation").querySelector("rect")!;
    expect([rect.getAttribute("width"), rect.getAttribute("height"), rect.getAttribute("rx")]).toEqual(["11", "11", "3"]);
    useI18nStore.setState({ locale: "th" });
    view.rerender(<ChatInput conversationId="c1" disabled={false} activeModelId={AUTO_MODEL_ID} onSend={vi.fn()} onStop={vi.fn()} streaming />);
    expect(view.getByLabelText("หยุด").className).toContain("stop-halo");
  });

  it("focus-ring stays on the button in both states", () => {
    const { view } = mount(true);
    expect(view.getByLabelText("Stop generation").className).toContain("focus-ring");
    const { view: v2, textarea } = mount(false);
    fireEvent.change(textarea, { target: { value: "x" } });
    expect(v2.getByLabelText("Send").className).toContain("focus-ring");
  });
});

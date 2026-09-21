// FIX122 Part A — a long conversation opens at its newest message, and what
// the person is reading stays put when the layout above it changes.
//
// Two layers. The arithmetic (which row is the landmark, what scrollTop puts
// it back, where a fresh virtual list starts) is pure and tested as tables.
// The mount test pins the bug itself: WITHOUT React StrictMode — production
// — the virtualized list must have rows in its first committed frame. With
// StrictMode's second effect pass the old code passed too, which is how a
// blank thread for every conversation of 50+ messages shipped.

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./MessageBubble", () => ({
  MessageBubble: ({ message }: { message: { content: string } }) => <div>{message.content}</div>,
}));

import type { Message } from "@/lib/types";
import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { landmarkCorrection, pickReadingLandmark, type RowBox } from "@/lib/turnAnchor";

import { ChatThread, virtualInitialOffset } from "./ChatThread";

const row = (key: string, top: number, height: number): RowBox => ({ key, top, bottom: top + height });

describe("pickReadingLandmark", () => {
  const VIEW = 600;
  it("takes the first row that STARTS inside the viewport, not the one cut by its top edge", () => {
    // the measured case: an unmeasured row (estimated 200) cut by the top
    // edge, the row being read starting 67 px down
    const rows = [row("old-99", -400, 200), row("old-100", -149, 200), row("reading-101", 67, 80), row("next", 163, 300)];
    expect(pickReadingLandmark(rows, VIEW)).toEqual({ key: "reading-101", offset: 67 });
  });
  it("a row flush with the top edge counts as starting inside", () => {
    expect(pickReadingLandmark([row("a", -200, 200), row("b", 0, 100)], VIEW)).toEqual({ key: "b", offset: 0 });
    expect(pickReadingLandmark([row("a", -0.6, 100)], VIEW)).toEqual({ key: "a", offset: -0.6 });
  });
  it("one message taller than the window: the cut row is all there is", () => {
    expect(pickReadingLandmark([row("a", -900, 200), row("giant", -300, 2000), row("below", 1700, 100)], VIEW))
      .toEqual({ key: "giant", offset: -300 });
  });
  it("ignores rows wholly above and wholly below", () => {
    expect(pickReadingLandmark([row("above", -300, 300), row("below", 600, 100)], VIEW)).toBeNull();
    expect(pickReadingLandmark([], VIEW)).toBeNull();
  });
});

describe("landmarkCorrection", () => {
  it("older messages arrived above: scroll down by exactly what was added", () => {
    // 50 rows estimated at 200 px were prepended: the row is 10,000 px lower
    expect(landmarkCorrection(0, 67, 10067, 30000)).toBe(10000);
  });
  it("a row above turned out taller / shorter than its estimate", () => {
    expect(landmarkCorrection(10065, 67, 207, 30000)).toBe(10205);
    expect(landmarkCorrection(10343, 67, -45, 30000)).toBe(10231);
  });
  it("within a pixel is no write at all", () => {
    expect(landmarkCorrection(500, 67, 67.6, 30000)).toBeNull();
    expect(landmarkCorrection(500, 67, 66, 30000)).toBeNull();
  });
  it("never past either end of the content", () => {
    expect(landmarkCorrection(10, 67, 20, 30000)).toBe(0);
    expect(landmarkCorrection(29990, 67, 300, 30000)).toBe(30000);
  });
});

describe("virtualInitialOffset", () => {
  it("a thread opens at its end — past any content, never an estimate of it", () => {
    expect(virtualInitialOffset(true, 0)).toBeGreaterThan(1e8);
    expect(virtualInitialOffset(true, 4321)).toBeGreaterThan(1e8);
  });
  it("a reader who is somewhere stays there", () => {
    expect(virtualInitialOffset(false, 4321)).toBe(4321);
    expect(virtualInitialOffset(false, -3)).toBe(0);
  });
});

// ── the mount ──────────────────────────────────────────────────────────
const VIEW_H = 600;
const isScroller = (el: Element) => el.classList?.contains("overflow-y-auto") && el.classList?.contains("h-full");
const scrollWrites: number[] = [];

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ turnScrollRequests: {} });
  scrollWrites.length = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return isScroller(this) ? VIEW_H : 0; } });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get() { return isScroller(this) ? 800 : 0; } });
  // the virtualizer reads the viewport from offsetWidth/Height when it attaches
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return isScroller(this) ? VIEW_H : 0; } });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return isScroller(this) ? 800 : 0; } });
  Element.prototype.scrollTo = function (arg?: ScrollToOptions | number) {
    if (isScroller(this) && typeof arg === "object" && typeof arg.top === "number") scrollWrites.push(arg.top);
  } as typeof Element.prototype.scrollTo;
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as { clientHeight?: unknown }).clientHeight;
  delete (HTMLElement.prototype as { clientWidth?: unknown }).clientWidth;
  delete (HTMLElement.prototype as { offsetHeight?: unknown }).offsetHeight;
  delete (HTMLElement.prototype as { offsetWidth?: unknown }).offsetWidth;
  cleanup();
});

const seed = (n: number): Message[] =>
  Array.from({ length: n }, (_, i) => ({
    id: `m${i}`, role: i % 2 === 0 ? "user" : "assistant", content: `row ${i}`,
    status: "completed", created_at: "2026-09-21T00:00:00Z",
  }) as Message);

const indexes = (c: HTMLElement) => Array.from(c.querySelectorAll("[data-msg-index]")).map((r) => Number((r as HTMLElement).dataset.msgIndex));

describe("a conversation of 50+ messages, first committed frame, no StrictMode", () => {
  it("has rows, and they are the NEWEST ones", () => {
    const { container } = render(<ChatThread convId="c1" messages={seed(60)} onRegenerate={() => {}} />);
    const shown = indexes(container);
    expect(shown.length).toBeGreaterThan(0);
    expect(shown).toContain(59);
    expect(shown).not.toContain(0);
  });

  it("attaching the virtualizer scrolls to the end, never to 0", () => {
    render(<ChatThread convId="c1" messages={seed(60)} onRegenerate={() => {}} />);
    expect(scrollWrites.length).toBeGreaterThan(0);
    expect(scrollWrites.every((top) => top > 0)).toBe(true);
  });

  it("49 messages stay on the plain list: every row is in the DOM", () => {
    const { container } = render(<ChatThread convId="c1" messages={seed(49)} onRegenerate={() => {}} />);
    expect(indexes(container)).toHaveLength(49);
  });

  it("a send that crosses the threshold does not swap the list under the streaming row", () => {
    const before = seed(48);
    const { container, rerender } = render(<ChatThread convId="c1" messages={before} onRegenerate={() => {}} />);
    const firstRow = container.querySelector('[data-msg-index="0"]');
    const live = [...before,
      { id: "u", role: "user", content: "q", status: "completed", created_at: "2026-09-21T00:00:01Z" } as Message,
      { id: "a", role: "assistant", content: "", status: "streaming", isStreaming: true, created_at: "2026-09-21T00:00:01Z" } as Message];
    rerender(<ChatThread convId="c1" messages={live} onRegenerate={() => {}} />);
    // still the plain list: all 50 rows, and row 0 is the same DOM node
    expect(indexes(container)).toHaveLength(50);
    expect(container.querySelector('[data-msg-index="0"]')).toBe(firstRow);
    // the turn ends → the list may turn virtual now
    const done = live.map((m) => (m.id === "a" ? { ...m, content: "answer", status: "completed", isStreaming: false } as Message : m));
    rerender(<ChatThread convId="c1" messages={done} onRegenerate={() => {}} />);
    expect(indexes(container).length).toBeLessThan(50);
    expect(indexes(container)).toContain(49);
  });
});
